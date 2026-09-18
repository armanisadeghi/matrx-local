//! `matrx-egress pair` — connecting this computer to somebody's account, with no password on this
//! machine and no inbound anything.
//!
//! 1. `POST /egress/pairings` with this computer's self-description → a short code and a page.
//! 2. Print the code and open the page in the browser. The person approves it while signed in.
//! 3. Poll `GET /egress/pairings/{id}` with the pairing secret until the answer changes.
//! 4. Put the device token in the OS keychain. It is the only copy on this computer, and it never
//!    touches disk.
//!
//! The secret and the token are never printed — not in the code line, not in an error.

use crate::config::Server;
use crate::http::{Api, PairingPoll};
use crate::identity::Registration;
use crate::keychain::{Keychain, StoredDevice};
use crate::status::now_rfc3339;
use std::fmt;
use std::time::Duration;

/// How long to keep polling before giving up, whatever the server said about expiry.
pub const PAIRING_DEADLINE: Duration = Duration::from_secs(15 * 60);

/// Why pairing did not finish.
#[derive(Debug)]
pub enum PairError {
    /// The server refused or could not be reached.
    Api(crate::http::ApiError),
    /// The person said this computer is not theirs.
    Denied,
    /// The code ran out before anybody approved it.
    Expired,
    /// The approval arrived but carried no token, so there is nothing to save.
    NoToken,
    /// The token could not be put in the OS credential store.
    Keychain(crate::keychain::KeychainError),
}

impl PairError {
    /// What the person should do about it.
    pub fn remedy(&self) -> String {
        match self {
            PairError::Api(e) => e.remedy.clone(),
            PairError::Denied => {
                "If that was a mistake, run Connect again and approve it while signed in to the \
                 right account."
                    .to_string()
            }
            PairError::Expired => "Run Connect again — a code is only good for 15 minutes.".to_string(),
            PairError::NoToken => {
                "Run Connect again. If it happens twice, the AI Matrx website can remove this \
                 computer and start over."
                    .to_string()
            }
            PairError::Keychain(e) => e.remedy(),
        }
    }
}

impl fmt::Display for PairError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PairError::Api(e) => write!(f, "{e}"),
            PairError::Denied => f.write_str("that computer was not added to the account"),
            PairError::Expired => f.write_str("the connect code ran out before it was approved"),
            PairError::NoToken => {
                f.write_str("AI Matrx approved this computer but sent nothing to save")
            }
            PairError::Keychain(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for PairError {}

/// Run the whole flow and return the device this computer now is.
///
/// `announce` is called with the code and the page as soon as they exist, so the caller decides
/// how to show them (the CLI prints them; a future UI could show them in a window).
pub async fn pair(
    server: Server,
    keychain: &Keychain,
    name: Option<&str>,
    announce: impl FnOnce(&str, &str),
) -> Result<StoredDevice, PairError> {
    let api = Api::new(server.clone()).map_err(PairError::Api)?;
    let registration = Registration::for_this_computer(name);
    let started = api
        .start_pairing(&registration)
        .await
        .map_err(PairError::Api)?;

    announce(&started.user_code, &started.connect_url);
    if let Err(e) = open::that_detached(&started.connect_url) {
        // Not fatal: the URL is on screen and the person can open it themselves. Saying so is the
        // difference between a helpful line and a silent failure.
        eprintln!(
            "[egress] this computer could not open a browser ({e}). Open the address above \
             yourself to finish connecting."
        );
    }

    let interval = Duration::from_secs(started.poll_seconds.clamp(1, 30));
    let deadline = tokio::time::Instant::now() + PAIRING_DEADLINE;
    loop {
        if tokio::time::Instant::now() >= deadline {
            return Err(PairError::Expired);
        }
        tokio::time::sleep(interval).await;
        let poll: PairingPoll = match api
            .poll_pairing(&started.pairing_id, &started.pairing_secret)
            .await
        {
            Ok(poll) => poll,
            Err(e) if e.status.is_some_and(|s| s >= 500) => {
                // A server hiccup mid-pairing is not a failed pairing: keep polling until the
                // deadline rather than making the person start over.
                eprintln!("[egress] {e} — still waiting.");
                continue;
            }
            Err(e) => return Err(PairError::Api(e)),
        };

        match poll.status.as_str() {
            "pending" => continue,
            "denied" => return Err(PairError::Denied),
            "expired" => return Err(PairError::Expired),
            "approved" => {
                let (Some(device_id), Some(device_token)) = (poll.device_id, poll.device_token)
                else {
                    return Err(PairError::NoToken);
                };
                let device = StoredDevice {
                    v: StoredDevice::VERSION,
                    device_id,
                    device_token,
                    display_name: poll
                        .display_name
                        .unwrap_or_else(|| registration.instance_name.clone()),
                    server: server.base().to_string(),
                    paired_at: now_rfc3339(),
                };
                keychain.save(&device).map_err(PairError::Keychain)?;
                return Ok(device);
            }
            other => {
                // An answer this build does not know is not silently treated as "keep waiting":
                // it stops with a sentence naming what came back.
                return Err(PairError::Api(crate::http::ApiError {
                    what: "AI Matrx answered with something this version does not understand"
                        .into(),
                    status: None,
                    detail: format!("it said the connection is {other:?}"),
                    remedy: "Update the AI Matrx Home Connection and run Connect again.".into(),
                }));
            }
        }
    }
}

/// The lines the CLI prints while waiting. Kept here so the wording is tested rather than buried
/// in `main`.
pub fn announcement(user_code: &str, connect_url: &str) -> String {
    format!(
        "Connect this computer to AI Matrx\n\
         \n\
         1. A browser page should have opened. If it did not, go to:\n   \
              {connect_url}\n\
         2. Sign in to AI Matrx if you are not already.\n\
         3. Check that the code on the page is:  {user_code}\n\
         4. Choose Connect.\n\
         \n\
         Waiting for you to approve it…"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_instructions_name_the_code_and_the_page_and_nothing_technical() {
        let text = announcement("ABCD-2345", "https://aimatrx.com/connect-computer?code=ABCD-2345");
        assert!(text.contains("ABCD-2345"));
        assert!(text.contains("https://aimatrx.com/connect-computer?code=ABCD-2345"));
        for jargon in ["token", "bearer", "egress", "proxy", "websocket", "pairing_secret"] {
            assert!(!text.to_lowercase().contains(jargon), "it says {jargon:?}");
        }
    }

    #[test]
    fn every_failure_says_what_to_do_next() {
        let cases = [
            PairError::Denied,
            PairError::Expired,
            PairError::NoToken,
            PairError::Api(crate::http::ApiError {
                what: "could not reach AI Matrx".into(),
                status: None,
                detail: "connection refused".into(),
                remedy: "Check this computer's internet connection and try again.".into(),
            }),
            PairError::Keychain(crate::keychain::KeychainError {
                operation: "save",
                cause: "locked".into(),
            }),
        ];
        for case in cases {
            let remedy = case.remedy();
            assert!(remedy.len() > 20, "{case:?} → {remedy}");
            assert!(!case.to_string().is_empty(), "{case:?}");
        }
    }
}
