//! The supervisor — the one loop that holds a connection up, and the one place a command from the
//! tray, the CLI or the web changes what this computer is doing.
//!
//! ```text
//!   tray menu ─┐
//!   CLI  ──────┼──▶ Command ──▶ Supervisor ──▶ session ──▶ status ──▶ tray, status file, CLI
//!   web (4403)─┘
//! ```
//!
//! Everything a user can do goes through [`Command`], so there is exactly one implementation of
//! "pause" and exactly one of "remove this computer", whichever surface asked for it.

use crate::config::Server;
use crate::http::Api;
use crate::keychain::{Keychain, StoredDevice};
use crate::relay::{self, AfterSession, Hello, SessionConfig, SessionEnd};
use crate::status::{State, StatusHandle};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc::{UnboundedReceiver, UnboundedSender};
use tokio::sync::{watch, Mutex};

/// Everything a person can ask of the helper, from any surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command {
    /// Switch this computer off — here, and on the account.
    Pause,
    /// Switch it back on.
    Resume,
    /// Remove this computer from the account and forget its token.
    SignOut,
    /// Open the account's computers page in the browser.
    OpenComputersPage,
    /// Open the page that confirms removing this computer.
    ConfirmRemovalInBrowser,
    /// Stop the helper. The account keeps the computer; it is simply not running.
    Quit,
}

/// What the supervisor is trying to do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Desired {
    Running,
    Paused,
    Stopped,
}

/// A session outcome that will not come back by itself, however long the helper waits.
///
/// The loop does not exit on one of these — it parks. A process whose run loop has returned still
/// has a menu bar item, a control socket and a CLI, and every one of them would go on offering
/// Pause and Resume for a connection that no longer exists: `Resume` would write into a watch
/// channel with no receiver, the status would still read "Connecting", and nothing would ever
/// happen. That is precisely the silent no-op law 4 forbids, so instead the loop stays alive
/// holding this, every surface can see it, and the controls it makes untrue are withdrawn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Terminal {
    /// `4401` — the account removed this computer.
    Removed,
    /// `4409` — another copy of the helper took this computer's connection.
    Replaced,
}

impl Terminal {
    /// The state this outcome leaves the helper in.
    pub fn state(self) -> State {
        match self {
            // The account genuinely no longer has this computer.
            Terminal::Removed => State::SignedOut,
            // The account still lists it; this copy simply is not the one holding it.
            Terminal::Replaced => State::Error,
        }
    }

    /// What happened, in one sentence.
    pub fn sentence(self) -> &'static str {
        match self {
            Terminal::Removed => {
                "This computer was removed from your account — run Connect again to add it back."
            }
            Terminal::Replaced => {
                "Another copy of the Home Connection took over for this computer."
            }
        }
    }

    /// What to do about it.
    pub fn remedy(self) -> &'static str {
        match self {
            Terminal::Removed => "Open AI Matrx on the web and connect this computer again.",
            Terminal::Replaced => {
                "Only one copy runs at a time. Quit this one, or quit the other and start this \
                 one again."
            }
        }
    }
}

/// The pages the tray opens. The list of computers is Settings → Devices & Sync at
/// `/settings?tab=devices` (contract § The web app); removal is the Remove control on that
/// list, which names its consequence before acting. The base is a flag and both paths live in
/// ONE place — a frontend that moves the list is a one-line change here, not a hunt.
#[derive(Debug, Clone)]
pub struct WebUrls {
    /// `https://aimatrx.com`.
    pub base: String,
}

impl Default for WebUrls {
    fn default() -> Self {
        WebUrls {
            base: "https://aimatrx.com".to_string(),
        }
    }
}

impl WebUrls {
    /// Where the person manages the computers on their account.
    pub fn computers(&self) -> String {
        format!("{}/settings?tab=devices", self.base.trim_end_matches('/'))
    }

    /// Where the person confirms removing this one: the same list, whose Remove control asks
    /// first. `device_id` rides along so the page can bring that computer into view.
    pub fn confirm_removal(&self, device_id: &str) -> String {
        format!(
            "{}/settings?tab=devices&computer={device_id}",
            self.base.trim_end_matches('/')
        )
    }
}

/// The running helper.
pub struct Supervisor {
    /// The status every surface reads.
    pub status: Arc<StatusHandle>,
    api: Api,
    device: Mutex<StoredDevice>,
    /// `None` in engine mode: the token came from stdin and this process owns no keychain item.
    keychain: Option<Keychain>,
    web: WebUrls,
    desired: watch::Sender<Desired>,
    hello: Hello,
    /// Set once a session ended in a way that cannot be retried. Read by every surface so that
    /// none of them offers a control that would silently do nothing.
    terminal: std::sync::Mutex<Option<Terminal>>,
}

impl Supervisor {
    /// Build a supervisor around a device this computer already has.
    pub fn new(
        server: Server,
        device: StoredDevice,
        keychain: Option<Keychain>,
        hello: Hello,
        status: Arc<StatusHandle>,
        web: WebUrls,
    ) -> Result<Arc<Self>, crate::http::ApiError> {
        let api = Api::new(server)?;
        let (desired, _) = watch::channel(Desired::Running);
        status.set_device_name(device.display_name.clone());
        Ok(Arc::new(Supervisor {
            status,
            api,
            device: Mutex::new(device),
            keychain,
            web,
            desired,
            hello,
            terminal: std::sync::Mutex::new(None),
        }))
    }

    /// Hold the connection up until something terminal happens or the helper is told to stop.
    pub async fn run(self: Arc<Self>) {
        let mut backoff = crate::backoff::Backoff::new();
        let mut desired = self.desired.subscribe();

        loop {
            let want = *desired.borrow_and_update();
            match want {
                Desired::Stopped => return,
                Desired::Paused => {
                    self.status.set_state(State::Paused);
                    if desired.changed().await.is_err() {
                        return;
                    }
                    continue;
                }
                Desired::Running => {}
            }

            self.status.set_state(State::Connecting);
            let (device_token, socket_url) = {
                let device = self.device.lock().await;
                (device.device_token.clone(), self.socket_url())
            };
            let config = SessionConfig {
                socket_url,
                token: device_token,
                hello: self.hello.clone(),
            };

            // A session is stopped from outside by this flag, which mirrors "anything but
            // Running" — one source of truth for "should this socket still be up".
            let (stop_tx, stop_rx) = watch::channel(false);
            let mirror = {
                let mut desired = self.desired.subscribe();
                tokio::spawn(async move {
                    loop {
                        if *desired.borrow_and_update() != Desired::Running {
                            let _ = stop_tx.send(true);
                            return;
                        }
                        if desired.changed().await.is_err() {
                            let _ = stop_tx.send(true);
                            return;
                        }
                    }
                })
            };

            let reset = std::sync::atomic::AtomicBool::new(false);
            let end = relay::run_session(&config, Arc::clone(&self.status), stop_rx, || {
                reset.store(true, std::sync::atomic::Ordering::SeqCst);
            })
            .await;
            mirror.abort();
            if reset.load(std::sync::atomic::Ordering::SeqCst) {
                // Only a session that reached HELLO_ACK counts as progress.
                backoff.reset();
            }

            match relay::after_session(&end, &self.status, &mut backoff) {
                AfterSession::Stop => {
                    if end == SessionEnd::StoppedLocally {
                        continue;
                    }
                    // Removed or replaced: the status already says which, with its remedy. The
                    // token is deliberately NOT deleted — a refusal that turns out to be the
                    // server's mistake must not cost the user their pairing.
                    //
                    // The loop does NOT return. The process lives on (the tray owns the main
                    // thread, and `main` deliberately keeps it there), so returning here would
                    // leave a menu still offering Pause and Resume for a connection that no
                    // longer exists — `Resume` writing into a watch channel nobody is reading and
                    // a title stuck on "Connecting" forever. Instead the outcome is recorded, the
                    // menu withdraws the controls it makes untrue, and this loop parks until the
                    // helper is told to stop.
                    if let Some(terminal) = end.terminal() {
                        *self.terminal.lock().expect("terminal mutex") = Some(terminal);
                    }
                    loop {
                        if *desired.borrow_and_update() == Desired::Stopped {
                            return;
                        }
                        if desired.changed().await.is_err() {
                            return;
                        }
                    }
                }
                AfterSession::RetryAfter(delay) => {
                    // The loop's own receiver, not a fresh one: a `Quit` that arrived while the
                    // session was ending is already marked unseen on this one, so the wait ends
                    // immediately instead of sitting out a 60-second backoff.
                    let _ = tokio::time::timeout(delay, desired.changed()).await;
                }
            }
        }
    }

    fn socket_url(&self) -> String {
        self.api.server().device_socket_url()
    }

    /// Serve commands until the channel closes or a command ends the helper's life.
    ///
    /// `SignOut` ends it as surely as `Quit` does: this computer no longer has a connection to
    /// hold, and a menu-bar item still sitting there afterwards would be a control for something
    /// that no longer exists.
    pub async fn serve_commands(self: Arc<Self>, mut commands: UnboundedReceiver<Command>) {
        while let Some(command) = commands.recv().await {
            self.apply(command).await;
            if matches!(command, Command::Quit | Command::SignOut) {
                return;
            }
        }
    }

    /// Do one command. Public so the tray can drive it directly in tests.
    pub async fn apply(&self, command: Command) {
        match command {
            Command::Pause => self.set_enabled(false).await,
            Command::Resume => self.set_enabled(true).await,
            Command::SignOut => self.sign_out().await,
            Command::OpenComputersPage => self.open(&self.web.computers()),
            Command::ConfirmRemovalInBrowser => {
                let device_id = self.device.lock().await.device_id.clone();
                self.open(&self.web.confirm_removal(&device_id));
            }
            Command::Quit => {
                self.desired.send_replace(Desired::Stopped);
            }
        }
    }

    /// Whether the helper is trying to stay connected right now — what the tray's Pause/Resume
    /// item reads to know which word to wear.
    pub fn is_paused(&self) -> bool {
        *self.desired.borrow() == Desired::Paused
    }

    /// The ending this helper cannot come back from, once it has had one. `None` while the
    /// connection is ordinary — connected, connecting, paused, or retrying.
    pub fn terminal(&self) -> Option<Terminal> {
        *self.terminal.lock().expect("terminal mutex")
    }

    async fn set_enabled(&self, enabled: bool) {
        // Pause and Resume mean nothing once this computer has been removed or taken over: there
        // is no connection to switch off and resuming would dial with a token the server has
        // already refused. The menu withdraws both items in that state, but the CLI and the
        // control socket can still ask — and the answer is the truth, never silence.
        if let Some(terminal) = self.terminal() {
            self.status
                .set_error(terminal.state(), terminal.sentence(), terminal.remedy());
            return;
        }

        // Local first, and immediately: the user asked THIS computer to stop lending its
        // connection, and that must not wait on a network call that might fail.
        // `send_replace`, never `send`: `send` fails when no receiver is subscribed, and the run
        // loop only subscribes while it is running. A pause that silently did nothing because
        // nobody happened to be listening is exactly the failure law 4 forbids.
        self.desired.send_replace(if enabled {
            Desired::Running
        } else {
            Desired::Paused
        });

        let (device_id, device_token) = {
            let device = self.device.lock().await;
            (device.device_id.clone(), device.device_token.clone())
        };
        if let Err(e) = self.api.set_enabled(&device_id, &device_token, enabled).await {
            // Law 4: the account and this computer now disagree, and the user is told so rather
            // than shown a tidy "Paused" that is only half true.
            let state = if enabled { State::Connecting } else { State::Paused };
            self.status.set_error(
                state,
                format!(
                    "{} on this computer, but AI Matrx has not been told yet ({}).",
                    if enabled { "Turned back on" } else { "Paused" },
                    e
                ),
                e.remedy,
            );
        }
    }

    async fn sign_out(&self) {
        let (device_id, device_token) = {
            let device = self.device.lock().await;
            (device.device_id.clone(), device.device_token.clone())
        };
        let removal = self.api.remove_device(&device_id, &device_token).await;
        let forgotten = match &self.keychain {
            Some(keychain) => keychain.delete(),
            None => Ok(()),
        };
        self.desired.send_replace(Desired::Stopped);

        match (removal, forgotten) {
            (Ok(()), Ok(())) => self.status.set_error(
                State::SignedOut,
                "This computer is no longer lending its internet connection to AI Matrx.",
                "Open AI Matrx and connect it again whenever you want it back.",
            ),
            (Err(e), _) => self.status.set_error(
                State::SignedOut,
                format!(
                    "This computer has stopped lending its internet connection, but AI Matrx \
                     still lists it ({e})."
                ),
                e.remedy,
            ),
            (Ok(()), Err(e)) => self.status.set_error(
                State::SignedOut,
                format!("This computer was removed from your account, but its saved connection \
                         could not be deleted from this computer ({e})."),
                e.remedy(),
            ),
        }
    }

    fn open(&self, url: &str) {
        if let Err(e) = open::that_detached(url) {
            self.status.set_error(
                self.status.snapshot().state,
                format!("This computer could not open {url} in a browser ({e})."),
                format!("Open {url} yourself."),
            );
        }
    }
}

/// A sender the tray and the control server both hold.
pub type Commands = UnboundedSender<Command>;

/// Wait for the signals that end the process: SIGTERM, Ctrl-C, and — in engine mode — stdin EOF.
pub async fn wait_for_shutdown(watch_stdin: bool) {
    let stdin_closed = async {
        if !watch_stdin {
            std::future::pending::<()>().await;
            return;
        }
        // The engine holds the child's stdin open; when the engine goes, the read returns 0 and
        // the helper stops. No signal, no orphan.
        let mut stdin = tokio::io::stdin();
        let mut buffer = [0u8; 256];
        loop {
            use tokio::io::AsyncReadExt as _;
            match stdin.read(&mut buffer).await {
                Ok(0) | Err(_) => return,
                Ok(_) => continue,
            }
        }
    };

    #[cfg(unix)]
    let terminated = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };
    #[cfg(not(unix))]
    let terminated = std::future::pending::<()>();

    tokio::select! {
        _ = stdin_closed => {}
        _ = terminated => {}
        _ = tokio::signal::ctrl_c() => {}
    }
}

/// Write the status file every five seconds, so the counters a running relay moves are never more
/// than that stale for anything tailing it.
pub fn spawn_status_ticker(status: Arc<StatusHandle>) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(Duration::from_secs(5));
        ticker.tick().await;
        loop {
            ticker.tick().await;
            status.persist();
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tray_opens_the_pages_the_contract_names() {
        let web = WebUrls::default();
        assert_eq!(web.computers(), "https://aimatrx.com/settings?tab=devices");
        assert_eq!(
            web.confirm_removal("d-1"),
            "https://aimatrx.com/settings?tab=devices&computer=d-1"
        );
        let local = WebUrls {
            base: "http://matrx.localhost:3001/".into(),
        };
        assert_eq!(
            local.computers(),
            "http://matrx.localhost:3001/settings?tab=devices"
        );
    }

    fn device() -> StoredDevice {
        StoredDevice {
            v: StoredDevice::VERSION,
            device_id: "d-1".into(),
            device_token: "mxe_d-1_secret".into(),
            display_name: "The kitchen Mac".into(),
            server: "http://127.0.0.1:1".into(),
            paired_at: "2026-09-18T00:00:00Z".into(),
        }
    }

    fn supervisor(server: &str) -> Arc<Supervisor> {
        let status = Arc::new(StatusHandle::new(server.into(), None, false));
        Supervisor::new(
            Server::parse(server).expect("server"),
            device(),
            None,
            Hello {
                protocol: relay::PROTOCOL_VERSION,
                helper_version: crate::VERSION.into(),
                platform: crate::identity::platform_word().into(),
                hostname: "test-host".into(),
                client_kind: "helper".into(),
                max_streams: relay::DEFAULT_MAX_STREAMS,
            },
            status,
            WebUrls::default(),
        )
        .expect("supervisor")
    }

    #[tokio::test]
    async fn the_display_name_reaches_the_status_the_moment_the_supervisor_exists() {
        let supervisor = supervisor("http://127.0.0.1:1");
        assert_eq!(
            supervisor.status.snapshot().device_name.as_deref(),
            Some("The kitchen Mac")
        );
    }

    #[tokio::test]
    async fn pausing_takes_effect_here_even_when_the_server_cannot_be_told() {
        // Port 1 on loopback refuses instantly, so this is the "the web could not be told" path.
        let supervisor = supervisor("http://127.0.0.1:1");
        supervisor.apply(Command::Pause).await;
        assert!(supervisor.is_paused(), "the local pause did not take effect");
        let snapshot = supervisor.status.snapshot();
        assert_eq!(snapshot.state, State::Paused);
        let sentence = snapshot.last_error.expect("a disagreement must be said out loud");
        assert!(sentence.contains("has not been told"), "{sentence}");
        assert!(snapshot.remedy.is_some());
    }

    #[tokio::test]
    async fn resuming_clears_the_pause_locally_first() {
        let supervisor = supervisor("http://127.0.0.1:1");
        supervisor.apply(Command::Pause).await;
        supervisor.apply(Command::Resume).await;
        assert!(!supervisor.is_paused());
    }

    #[tokio::test]
    async fn signing_out_says_so_even_when_the_account_still_lists_the_computer() {
        let supervisor = supervisor("http://127.0.0.1:1");
        supervisor.apply(Command::SignOut).await;
        let snapshot = supervisor.status.snapshot();
        assert_eq!(snapshot.state, State::SignedOut);
        let sentence = snapshot.last_error.expect("sentence");
        assert!(sentence.contains("still lists it"), "{sentence}");
        assert!(snapshot.remedy.is_some());
    }

    #[tokio::test]
    async fn quitting_stops_the_run_loop() {
        let supervisor = supervisor("http://127.0.0.1:1");
        supervisor.apply(Command::Quit).await;
        // `run` must return rather than dial: the desired state is Stopped before it starts.
        tokio::time::timeout(Duration::from_secs(5), Arc::clone(&supervisor).run())
            .await
            .expect("the run loop did not stop when it was told to");
    }

    /// A gateway that accepts the socket and immediately closes it with `code`. Returns the
    /// `http://127.0.0.1:<port>` base the supervisor should be pointed at.
    async fn gateway_that_closes_with(code: u16) -> String {
        use futures_util::SinkExt as _;
        use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
        use tokio_tungstenite::tungstenite::protocol::CloseFrame;
        use tokio_tungstenite::tungstenite::Message;

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let port = listener.local_addr().expect("addr").port();
        tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    return;
                };
                tokio::spawn(async move {
                    let Ok(mut socket) = tokio_tungstenite::accept_async(stream).await else {
                        return;
                    };
                    let _ = socket
                        .send(Message::Close(Some(CloseFrame {
                            code: CloseCode::from(code),
                            reason: "".into(),
                        })))
                        .await;
                    let _ = socket.close(None).await;
                });
            }
        });
        format!("http://127.0.0.1:{port}")
    }

    /// The defect this exists to stop: after a `4401` the run loop used to RETURN while `main`
    /// kept the tray alive, so Resume wrote into a watch channel with no receiver and the menu
    /// read "Connecting" forever. The loop must park instead, keep answering, and still quit.
    #[tokio::test]
    async fn a_removed_computer_parks_and_answers_resume_with_the_truth() {
        let supervisor = supervisor(&gateway_that_closes_with(4401).await);
        let running = tokio::spawn(Arc::clone(&supervisor).run());

        // The loop reaches the terminal state…
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while supervisor.terminal().is_none() {
            assert!(
                std::time::Instant::now() < deadline,
                "the 4401 never became a terminal state"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert_eq!(supervisor.terminal(), Some(Terminal::Removed));
        assert_eq!(supervisor.status.snapshot().state, State::SignedOut);

        // …and does NOT return, because the process (and its menu) is still alive.
        tokio::time::sleep(Duration::from_millis(200)).await;
        assert!(!running.is_finished(), "the run loop abandoned the tray");

        // Resume is not silence and is not a lie: the status keeps saying what happened.
        supervisor.apply(Command::Resume).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
        let snapshot = supervisor.status.snapshot();
        assert_eq!(
            snapshot.state,
            State::SignedOut,
            "Resume moved a removed computer to {:?}",
            snapshot.state
        );
        assert_eq!(snapshot.last_error.as_deref(), Some(Terminal::Removed.sentence()));
        assert!(snapshot.remedy.is_some());

        // And the menu the tray would draw offers no Pause and no Resume at all.
        let model = crate::menu::model(&snapshot, supervisor.terminal(), supervisor.is_paused());
        assert!(model
            .actions
            .iter()
            .all(|a| a.command != Command::Pause && a.command != Command::Resume));

        // Quit still ends it.
        supervisor.apply(Command::Quit).await;
        tokio::time::timeout(Duration::from_secs(5), running)
            .await
            .expect("a parked run loop ignored Quit")
            .expect("the run loop panicked");
    }

    #[tokio::test]
    async fn a_replaced_connection_parks_as_an_error_not_as_signed_out() {
        let supervisor = supervisor(&gateway_that_closes_with(4409).await);
        let running = tokio::spawn(Arc::clone(&supervisor).run());
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while supervisor.terminal().is_none() {
            assert!(
                std::time::Instant::now() < deadline,
                "the 4409 never became a terminal state"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert_eq!(supervisor.terminal(), Some(Terminal::Replaced));
        // The account still lists this computer, so it is NOT "removed from your account".
        assert_eq!(supervisor.status.snapshot().state, State::Error);
        assert!(!running.is_finished());
        supervisor.apply(Command::Quit).await;
        tokio::time::timeout(Duration::from_secs(5), running)
            .await
            .expect("a parked run loop ignored Quit")
            .expect("the run loop panicked");
    }

    #[tokio::test]
    async fn a_command_channel_that_closes_ends_the_command_loop() {
        let supervisor = supervisor("http://127.0.0.1:1");
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        drop(tx);
        tokio::time::timeout(
            Duration::from_secs(5),
            Arc::clone(&supervisor).serve_commands(rx),
        )
        .await
        .expect("the command loop outlived its channel");
    }
}
