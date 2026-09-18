//! `matrx-egress` — **AI Matrx Home Connection**, the device leg of the residential-egress relay.
//!
//! This computer lends its internet connection to its owner's own AI Matrx work, and only when a
//! site has blocked our servers. It dials OUT and holds one WebSocket; nothing listens on this
//! machine, no port is opened, no router is touched, and every connection it is asked to make is
//! checked against [`policy`] first so it can never be used to reach this computer's own network.
//!
//! Contract: `common-docs/systems/platform/residential-egress/FEATURE.md`.
//!
//! ```text
//! matrx-egress                       standalone: keychain token + menu-bar item
//! matrx-egress pair [--server URL]   connect this computer to an account
//! matrx-egress run --token-stdin …   engine mode: the desktop app runs this as a child
//! matrx-egress status|pause|resume|sign-out
//! matrx-egress install|uninstall     start (or stop starting) when you sign in
//! ```

#![warn(missing_docs)]
#![warn(clippy::all)]

mod backoff;
mod config;
mod control;
#[cfg(test)]
mod fake_gateway;
mod frame;
mod http;
mod icon;
mod identity;
mod install;
mod keychain;
mod pairing;
mod paths;
mod policy;
mod relay;
mod status;
mod supervisor;
#[cfg(any(target_os = "macos", target_os = "windows"))]
mod tray;
mod world;

use config::Server;
use control::{ControlClient, ControlServer};
use keychain::Keychain;
use paths::Paths;
use relay::Hello;
use status::{State, StatusHandle};
use std::process::ExitCode;
use std::sync::Arc;
use supervisor::{Supervisor, WebUrls};
use world::World;

/// This binary's version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
const NAME: &str = env!("CARGO_PKG_NAME");

/// How long any one credential-store call may take before the helper says the store will not open
/// rather than waiting on a dialog nobody can answer (the lesson `matrx-syncd` learned on
/// 2026-09-15: macOS can prompt for keychain access, and a login-time process has no window).
const KEYCHAIN_BUDGET: std::time::Duration = std::time::Duration::from_secs(5);

fn help() -> String {
    format!(
        "{NAME} {VERSION}\n\
         AI Matrx Home Connection — lends this computer's internet connection to your own AI Matrx\n\
         work, and only when a site blocks our servers.\n\n\
         USAGE:\n    \
             {NAME} [OPTIONS]                    Run it (connect, and show the menu-bar item)\n    \
             {NAME} pair [OPTIONS]               Connect this computer to your AI Matrx account\n    \
             {NAME} run --token-stdin [OPTIONS]  Run under the AI Matrx desktop app\n    \
             {NAME} status                       What the running copy is doing\n    \
             {NAME} pause | resume               Stop or restart lending this connection\n    \
             {NAME} sign-out                     Remove this computer from your account\n    \
             {NAME} install | uninstall          Start (or stop starting) when you sign in\n\n\
         OPTIONS:\n    \
             --server <url>       The AI Matrx server. Default {default_server}\n    \
             --web <url>          The AI Matrx website the menu opens. Default https://aimatrx.com\n    \
             --name <name>        What to call this computer on your account\n    \
             --world <live|dev>   Which world to run in. A source build defaults to dev\n    \
             --status-file <path> Write the status document here (run mode)\n    \
             --token-stdin        Read the connection token from the first line of stdin\n    \
             --no-tray            Do not show a menu-bar item\n    \
             -V, --version        Print the version and exit\n    \
             -h, --help           Print this help and exit\n\n\
         ENVIRONMENT:\n    \
             MATRX_HOME_DIR       This world's home; defaults to ~/.matrx or ~/.matrx-dev\n",
        default_server = config::DEFAULT_SERVER
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Verb {
    Standalone,
    Pair,
    Run,
    Status,
    Pause,
    Resume,
    SignOut,
    Install,
    Uninstall,
}

#[derive(Debug)]
struct Options {
    verb: Verb,
    world: World,
    server: Option<String>,
    web: Option<String>,
    name: Option<String>,
    status_file: Option<std::path::PathBuf>,
    token_stdin: bool,
    tray: bool,
}

fn parse_args(args: &[String]) -> Result<Options, (String, u8)> {
    let mut options = Options {
        verb: Verb::Standalone,
        world: World::default_for_build(),
        server: None,
        web: None,
        name: None,
        status_file: None,
        token_stdin: false,
        // A menu-bar item is what a person installed this for; a helper the desktop app runs as a
        // child never shows one, so `run` turns it off below.
        tray: true,
    };

    let mut index = 0;
    if let Some(first) = args.first() {
        if !first.starts_with('-') {
            options.verb = match first.as_str() {
                "pair" => Verb::Pair,
                "run" => Verb::Run,
                "status" => Verb::Status,
                "pause" => Verb::Pause,
                "resume" => Verb::Resume,
                "sign-out" => Verb::SignOut,
                "install" => Verb::Install,
                "uninstall" => Verb::Uninstall,
                other => {
                    return Err((
                        format!("{NAME}: {other:?} is not something this can do.\n\n{}", help()),
                        2,
                    ))
                }
            };
            index = 1;
        }
    }
    if options.verb == Verb::Run {
        options.tray = false;
    }

    while index < args.len() {
        let arg = args[index].as_str();
        let value = |index: &mut usize, flag: &str| -> Result<String, (String, u8)> {
            *index += 1;
            args.get(*index).cloned().ok_or_else(|| {
                (format!("{NAME}: {flag} needs a value after it."), 2)
            })
        };
        match arg {
            "-V" | "--version" => {
                return Err((format!("{NAME} {VERSION}"), 0));
            }
            "-h" | "--help" => return Err((help(), 0)),
            "--server" => options.server = Some(value(&mut index, "--server")?),
            "--web" => options.web = Some(value(&mut index, "--web")?),
            "--name" => options.name = Some(value(&mut index, "--name")?),
            "--status-file" => {
                options.status_file = Some(std::path::PathBuf::from(value(
                    &mut index,
                    "--status-file",
                )?))
            }
            "--world" => {
                let given = value(&mut index, "--world")?;
                options.world = World::parse(&given).ok_or_else(|| {
                    (
                        format!("{NAME}: --world takes `live` or `dev`, not {given:?}"),
                        2,
                    )
                })?;
            }
            "--token-stdin" => options.token_stdin = true,
            "--no-tray" => options.tray = false,
            "--tray" => options.tray = true,
            other => {
                return Err((
                    format!("{NAME}: {other:?} is not an option this understands.\n\n{}", help()),
                    2,
                ))
            }
        }
        index += 1;
    }

    Ok(options)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let options = match parse_args(&args) {
        Ok(options) => options,
        Err((message, 0)) => {
            print!("{message}");
            if !message.ends_with('\n') {
                println!();
            }
            return ExitCode::SUCCESS;
        }
        Err((message, code)) => {
            eprintln!("{message}");
            return ExitCode::from(code);
        }
    };

    match options.verb {
        Verb::Install => match install::install(options.world) {
            Ok(outcome) => {
                println!("{}", outcome.message);
                if let Some(path) = outcome.path {
                    println!("({})", path.display());
                }
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("{NAME}: {e}");
                ExitCode::from(1)
            }
        },
        Verb::Uninstall => match install::uninstall(options.world) {
            Ok(outcome) => {
                println!("{}", outcome.message);
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("{NAME}: {e}");
                ExitCode::from(1)
            }
        },
        _ => {
            let runtime = match tokio::runtime::Runtime::new() {
                Ok(runtime) => runtime,
                Err(e) => {
                    eprintln!("{NAME}: this computer could not start the helper's background work: {e}");
                    return ExitCode::from(1);
                }
            };
            run_with_runtime(runtime, options)
        }
    }
}

fn run_with_runtime(runtime: tokio::runtime::Runtime, options: Options) -> ExitCode {
    let paths = match Paths::resolve(options.world) {
        Ok(paths) => paths,
        Err(e) => {
            eprintln!("{NAME}: {e}");
            return ExitCode::from(1);
        }
    };

    match options.verb {
        Verb::Status | Verb::Pause | Verb::Resume | Verb::SignOut => {
            runtime.block_on(talk_to_running(&paths, options.verb))
        }
        Verb::Pair => runtime.block_on(do_pairing(&paths, &options)),
        Verb::Run => start_relay(runtime, paths, options, None),
        Verb::Standalone => {
            // Standalone needs a token; without one the only honest next step is pairing.
            let keychain = match keychain_for(&paths) {
                Ok(keychain) => keychain,
                Err(code) => return code,
            };
            let stored = match load_within_budget(&keychain) {
                Ok(Some(stored)) => Some(stored),
                Ok(None) => {
                    println!(
                        "This computer is not connected to an AI Matrx account yet.\n"
                    );
                    match runtime.block_on(do_pairing(&paths, &options)) {
                        code if code == ExitCode::SUCCESS => match load_within_budget(&keychain) {
                            Ok(Some(stored)) => Some(stored),
                            _ => return ExitCode::from(1),
                        },
                        code => return code,
                    }
                }
                Err(e) => {
                    eprintln!("{NAME}: {e}\n{}", e.remedy());
                    return ExitCode::from(1);
                }
            };
            start_relay(runtime, paths, options, stored)
        }
        Verb::Install | Verb::Uninstall => unreachable!("handled before the runtime is built"),
    }
}

/// Read the credential item, but never wait forever for it.
///
/// macOS can put up an approval dialog for a rebuilt binary, and a helper started at login has no
/// window to answer it in. After [`KEYCHAIN_BUDGET`] the helper says that, with its remedy, rather
/// than hanging with no output at all — the shape `matrx-syncd` was bitten by on 2026-09-15.
fn load_within_budget(
    keychain: &Keychain,
) -> Result<Option<keychain::StoredDevice>, keychain::KeychainError> {
    let (tx, rx) = std::sync::mpsc::channel();
    let keychain = keychain.clone();
    std::thread::spawn(move || {
        let _ = tx.send(keychain.load());
    });
    match rx.recv_timeout(KEYCHAIN_BUDGET) {
        Ok(result) => result,
        Err(_) => Err(keychain::KeychainError {
            operation: "read",
            cause: format!(
                "this computer's password store did not answer within {} seconds",
                KEYCHAIN_BUDGET.as_secs()
            ),
        }),
    }
}

fn keychain_for(paths: &Paths) -> Result<Keychain, ExitCode> {
    let hostname = identity::hostname();
    let account = identity::instance_id(&hostname, identity::hardware_uuid().as_deref());
    Ok(Keychain::new(paths.world, account))
}

async fn talk_to_running(paths: &Paths, verb: Verb) -> ExitCode {
    let client = match ControlClient::connect(paths) {
        Ok(client) => client,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(1);
        }
    };
    let result = match verb {
        Verb::Status => client.status().await,
        Verb::Pause => client.command("/pause").await,
        Verb::Resume => client.command("/resume").await,
        Verb::SignOut => client.command("/sign-out").await,
        _ => unreachable!("only the four control verbs reach here"),
    };
    match result {
        Ok(body) => {
            match verb {
                Verb::Status => println!("{body}"),
                Verb::Pause => println!("This computer has stopped lending its internet connection."),
                Verb::Resume => println!("This computer is lending its internet connection again."),
                Verb::SignOut => println!(
                    "This computer has been removed from your AI Matrx account and has forgotten \
                     its connection."
                ),
                _ => {}
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("{e}");
            ExitCode::from(1)
        }
    }
}

async fn do_pairing(paths: &Paths, options: &Options) -> ExitCode {
    let server = match resolve_server(options.server.as_deref()) {
        Ok(server) => server,
        Err(code) => return code,
    };
    let keychain = match keychain_for(paths) {
        Ok(keychain) => keychain,
        Err(code) => return code,
    };
    match pairing::pair(server, &keychain, options.name.as_deref(), |code, url| {
        println!("{}", pairing::announcement(code, url));
    })
    .await
    {
        Ok(device) => {
            println!("\nConnected. This computer is now “{}”.", device.display_name);
            println!(
                "It will only be used for your own work, and only when a site blocks AI Matrx's \
                 servers."
            );
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("\n{NAME}: {e}\n{}", e.remedy());
            ExitCode::from(1)
        }
    }
}

fn resolve_server(given: Option<&str>) -> Result<Server, ExitCode> {
    match given {
        Some(given) => Server::parse(given).map_err(|e| {
            eprintln!("{NAME}: {e}");
            ExitCode::from(2)
        }),
        None => Ok(Server::default_server()),
    }
}

/// Everything after "we have a token": the supervisor, the control surface, the status file, and
/// either the tray (on the main thread) or a plain wait.
fn start_relay(
    runtime: tokio::runtime::Runtime,
    paths: Paths,
    options: Options,
    stored: Option<keychain::StoredDevice>,
) -> ExitCode {
    if let Err(e) = paths.create_dirs() {
        eprintln!(
            "{NAME}: could not create {}: {e}. The helper needs a folder it can write to.",
            paths.dir.display()
        );
        return ExitCode::from(1);
    }

    let engine_mode = options.verb == Verb::Run;
    let device = match stored {
        Some(device) => device,
        None => match read_token_from_stdin(&options) {
            Ok(device) => device,
            Err(code) => return code,
        },
    };

    // `--server` wins; otherwise the server this token was issued by.
    let server = match options.server.as_deref() {
        Some(given) => match resolve_server(Some(given)) {
            Ok(server) => server,
            Err(code) => return code,
        },
        None => match Server::parse(&device.server) {
            Ok(server) => server,
            Err(_) => Server::default_server(),
        },
    };

    let status_path = if engine_mode {
        options.status_file.clone()
    } else {
        Some(paths.status.clone())
    };
    let status = Arc::new(StatusHandle::new(
        server.base().to_string(),
        status_path,
        engine_mode,
    ));
    let hello = Hello {
        protocol: relay::PROTOCOL_VERSION,
        helper_version: VERSION.to_string(),
        platform: identity::platform_word().to_string(),
        hostname: identity::hostname(),
        client_kind: if engine_mode { "desktop_app" } else { "helper" }.to_string(),
        max_streams: relay::DEFAULT_MAX_STREAMS,
    };
    let keychain = if engine_mode {
        // The engine's token came down a pipe and this process owns no keychain item; saying so
        // keeps `sign-out` from pretending to forget something it never held.
        None
    } else {
        match keychain_for(&paths) {
            Ok(keychain) => Some(keychain),
            Err(code) => return code,
        }
    };
    let web = WebUrls {
        base: options
            .web
            .clone()
            .unwrap_or_else(|| WebUrls::default().base),
    };

    let supervisor = match Supervisor::new(
        server,
        device,
        keychain,
        hello,
        Arc::clone(&status),
        web,
    ) {
        Ok(supervisor) => supervisor,
        Err(e) => {
            eprintln!("{NAME}: {e}\n{}", e.remedy);
            return ExitCode::from(1);
        }
    };

    let (commands_tx, commands_rx) = tokio::sync::mpsc::unbounded_channel();
    let control_path = paths.control.clone();

    let guard = runtime.enter();
    let ticker = supervisor::spawn_status_ticker(Arc::clone(&status));
    runtime.spawn(Arc::clone(&supervisor).run());
    let commands_task = {
        let supervisor = Arc::clone(&supervisor);
        let status = Arc::clone(&status);
        let control_path = control_path.clone();
        runtime.spawn(async move {
            supervisor.serve_commands(commands_rx).await;
            // Quit or sign-out. Stop looking live before the process goes: the control file is
            // how `matrx-egress status` decides something is running.
            let _ = paths::remove_quietly(&control_path);
            status.persist();
            // The tray owns the main thread and cannot be woken from here, and there is nothing
            // left for this process to do in either mode.
            std::process::exit(0);
        })
    };

    if !engine_mode {
        match runtime.block_on(ControlServer::bind()) {
            Ok(server) => {
                if let Err(e) = server.publish(&paths) {
                    eprintln!(
                        "[egress] could not publish {}: {e}. `matrx-egress status` and \
                         `matrx-egress pause` will not find this copy; use the menu-bar item or \
                         AI Matrx on the web.",
                        paths.control.display()
                    );
                } else {
                    server.serve(Arc::clone(&status), commands_tx.clone());
                }
            }
            Err(e) => eprintln!(
                "[egress] could not open the local control port ({e}). `matrx-egress status` and \
                 `matrx-egress pause` will not find this copy; use the menu-bar item or AI Matrx \
                 on the web."
            ),
        }
    }
    drop(guard);

    let use_tray = options.tray && cfg!(any(target_os = "macos", target_os = "windows"));
    if use_tray {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        {
            let supervisor_for_tray = Arc::clone(&supervisor);
            // The event loop owns the main thread; the runtime keeps running on its own threads.
            // It is deliberately not dropped: dropping it here would stop the relay the tray is
            // reporting on.
            let runtime = Box::leak(Box::new(runtime));
            let _ = runtime;
            tray::run(Arc::clone(&status), commands_tx, move || {
                supervisor_for_tray.is_paused()
            });
            // `tray::run` only returns if the event loop stopped without exiting the process.
            let _ = paths::remove_quietly(&control_path);
            return ExitCode::SUCCESS;
        }
    }

    // No tray: print the same status the tray would show, then wait.
    let snapshot = status.snapshot();
    if !engine_mode {
        println!("{}", snapshot.title_line());
        println!("{}", snapshot.to_pretty());
    }
    runtime.block_on(async move {
        tokio::select! {
            _ = supervisor::wait_for_shutdown(engine_mode) => {}
            _ = commands_task => {}
        }
    });
    ticker.abort();
    if !engine_mode {
        let _ = paths::remove_quietly(&control_path);
    }
    // The five states have no word for "stopped", and leaving "connected" behind would be a
    // status file that lies about a process which no longer exists. `signed_out` with the true
    // sentence is what anything tailing the file should read.
    status.set_error(
        State::SignedOut,
        "The AI Matrx Home Connection is not running on this computer.",
        "Start it again to lend this computer's internet connection.",
    );
    ExitCode::SUCCESS
}

fn read_token_from_stdin(options: &Options) -> Result<keychain::StoredDevice, ExitCode> {
    if !options.token_stdin {
        eprintln!(
            "{NAME}: run mode needs --token-stdin, and the first line of stdin must be this \
             computer's connection token."
        );
        return Err(ExitCode::from(2));
    }
    let mut line = String::new();
    if let Err(e) = std::io::BufRead::read_line(&mut std::io::stdin().lock(), &mut line) {
        eprintln!("{NAME}: could not read the connection token from stdin: {e}");
        return Err(ExitCode::from(1));
    }
    let token = line.trim().to_string();
    if token.is_empty() {
        eprintln!("{NAME}: stdin carried no connection token, so there is nothing to connect with.");
        return Err(ExitCode::from(2));
    }
    // `mxe_<device_id>_<secret>` — the device id is in the token, so run mode needs nothing else.
    let device_id = token
        .strip_prefix("mxe_")
        .and_then(|rest| rest.split('_').next())
        .unwrap_or_default()
        .to_string();
    if device_id.is_empty() {
        eprintln!(
            "{NAME}: that connection token is not one this understands (it should start with \
             `mxe_`)."
        );
        return Err(ExitCode::from(2));
    }
    Ok(keychain::StoredDevice {
        v: keychain::StoredDevice::VERSION,
        device_id,
        device_token: token,
        display_name: options
            .name
            .clone()
            .unwrap_or_else(identity::hostname),
        server: options
            .server
            .clone()
            .unwrap_or_else(|| config::DEFAULT_SERVER.to_string()),
        paired_at: status::now_rfc3339(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn no_arguments_is_standalone_with_a_tray() {
        let options = parse_args(&[]).map_err(|(m, _)| m).expect("parse");
        assert_eq!(options.verb, Verb::Standalone);
        assert!(options.tray);
        assert_eq!(options.world, World::default_for_build());
    }

    #[test]
    fn every_verb_the_contract_names_is_understood() {
        for (word, verb) in [
            ("pair", Verb::Pair),
            ("run", Verb::Run),
            ("status", Verb::Status),
            ("pause", Verb::Pause),
            ("resume", Verb::Resume),
            ("sign-out", Verb::SignOut),
            ("install", Verb::Install),
            ("uninstall", Verb::Uninstall),
        ] {
            let options = parse_args(&args(&[word])).map_err(|(m, _)| m).expect(word);
            assert_eq!(options.verb, verb, "{word}");
        }
    }

    #[test]
    fn run_mode_reads_every_flag_the_contract_gives_it() {
        let options = parse_args(&args(&[
            "run",
            "--token-stdin",
            "--server",
            "https://example.test",
            "--status-file",
            "/tmp/egress-status.json",
            "--no-tray",
            "--name",
            "The kitchen Mac",
        ]))
        .map_err(|(m, _)| m)
        .expect("parse");
        assert_eq!(options.verb, Verb::Run);
        assert!(options.token_stdin);
        assert_eq!(options.server.as_deref(), Some("https://example.test"));
        assert_eq!(
            options.status_file.as_deref(),
            Some(std::path::Path::new("/tmp/egress-status.json"))
        );
        assert!(!options.tray);
        assert_eq!(options.name.as_deref(), Some("The kitchen Mac"));
    }

    #[test]
    fn a_child_of_the_desktop_app_never_puts_a_second_icon_in_the_menu_bar() {
        let options = parse_args(&args(&["run", "--token-stdin"]))
            .map_err(|(m, _)| m)
            .expect("parse");
        assert!(!options.tray, "run mode defaulted to showing a tray");
    }

    #[test]
    fn a_world_is_never_guessed_and_a_bad_one_exits_two() {
        let options = parse_args(&args(&["--world", "dev"]))
            .map_err(|(m, _)| m)
            .expect("parse");
        assert_eq!(options.world, World::Dev);
        let (message, code) = parse_args(&args(&["--world", "production"])).expect_err("refused");
        assert_eq!(code, 2);
        assert!(message.contains("live"), "{message}");
    }

    #[test]
    fn an_unknown_verb_or_option_prints_the_help_and_exits_two() {
        for bad in [vec!["frobnicate"], vec!["--frobnicate"]] {
            let (message, code) = parse_args(&args(&bad)).expect_err("refused");
            assert_eq!(code, 2);
            assert!(message.contains("USAGE"), "{message}");
        }
    }

    #[test]
    fn a_flag_with_no_value_says_which_flag() {
        let (message, code) = parse_args(&args(&["--server"])).expect_err("refused");
        assert_eq!(code, 2);
        assert!(message.contains("--server"), "{message}");
    }

    #[test]
    fn version_and_help_exit_zero() {
        for flag in ["-V", "--version", "-h", "--help"] {
            let (message, code) = parse_args(&args(&[flag])).expect_err("prints and exits");
            assert_eq!(code, 0, "{flag}");
            assert!(!message.is_empty(), "{flag}");
        }
    }

    #[test]
    fn the_help_names_every_command_in_plain_words() {
        let text = help();
        for word in ["pair", "run", "status", "pause", "resume", "sign-out", "install"] {
            assert!(text.contains(word), "the help never mentions {word}");
        }
        // `matrx-egress` is what a person types, not copy; every OTHER use of the word would be
        // jargon in a sentence, so the command name is removed before the check.
        let prose = text.replace(NAME, "<command>");
        for jargon in ["egress", "residential", "proxy", "WebSocket", "relay"] {
            assert!(!prose.contains(jargon), "the help says {jargon:?}");
        }
    }
}
