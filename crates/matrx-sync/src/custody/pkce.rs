//! The PKCE transaction (S1, S5).
//!
//! The code verifier is generated **inside the daemon**, never leaves it, and is taken before the
//! first network await. The authorization code alone is worthless without it, so even a fully
//! compromised webview cannot complete a sign-in it did not start.

use super::error::{CustodyError, Result};
use super::world::World;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use rand::TryRngCore;
use sha2::{Digest, Sha256};
use serde::Serialize;
use std::time::Duration;

/// S5: a transaction is single-use and expires ten minutes after it is created.
pub const TRANSACTION_TTL: Duration = Duration::from_secs(600);

/// Which redirect URI a transaction was opened against.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RedirectKind {
    /// `aimatrx://auth/callback` — the packaged app is the OS handler (S2).
    DeepLink,
    /// `http://localhost:2216{1}/oauth/callback` — the daemon binds it itself (S3).
    Loopback,
}

impl RedirectKind {
    /// The wire spelling.
    pub const fn as_str(self) -> &'static str {
        match self {
            RedirectKind::DeepLink => "deep_link",
            RedirectKind::Loopback => "loopback",
        }
    }
}

/// A live sign-in transaction. **Held in daemon memory only** — never serialised, never written to
/// disk, never handed to any caller. Only `transaction_id` ever leaves the process.
#[derive(Debug, Clone)]
pub struct Transaction {
    /// The opaque id the control API returns.
    pub transaction_id: String,
    /// 32 bytes from the OS CSPRNG, base64url. Never leaves the daemon (S1).
    pub code_verifier: String,
    /// 256 bits from the OS CSPRNG, compared in constant time (S5).
    pub state: String,
    /// The exact redirect URI sent in the authorization request; the token exchange must repeat it
    /// verbatim (Supabase matches exactly, §3.1).
    pub redirect_uri: String,
    /// Which leg this transaction took.
    pub redirect_kind: RedirectKind,
    /// Monotonic reading at creation; the TTL is measured against it, never against a wall clock.
    pub created_mono: Duration,
}

/// 32 random bytes from the OS CSPRNG, base64url-encoded without padding.
fn random_b64(bytes: usize) -> Result<String> {
    let mut buf = vec![0u8; bytes];
    rand::rngs::OsRng
        .try_fill_bytes(&mut buf)
        .map_err(|e| CustodyError::Configuration {
            setting: "operating system random number generator",
            detail: e.to_string(),
        })?;
    Ok(URL_SAFE_NO_PAD.encode(&buf))
}

/// The S256 challenge for a verifier.
pub fn code_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

/// Constant-time comparison of two `state` values (S5).
///
/// Hand-written rather than pulled from a crate: it is four lines, and the comparison is over
/// ASCII base64url of a known length.
pub fn state_matches(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.bytes().zip(b.bytes()) {
        diff |= x ^ y;
    }
    diff == 0
}

impl Transaction {
    /// Open a transaction against `redirect_uri`.
    pub fn open(redirect_uri: String, redirect_kind: RedirectKind, now_mono: Duration) -> Result<Self> {
        Ok(Transaction {
            transaction_id: random_b64(16)?,
            code_verifier: random_b64(32)?,
            state: random_b64(32)?,
            redirect_uri,
            redirect_kind,
            created_mono: now_mono,
        })
    }

    /// Whether this transaction has outlived S5's TTL, measured monotonically.
    pub fn expired(&self, now_mono: Duration) -> bool {
        now_mono.saturating_sub(self.created_mono) > TRANSACTION_TTL
    }

    /// The authorization URL the host opens in the SYSTEM browser.
    ///
    /// `openid` is deliberately omitted from the scope: with Supabase's default HS256 key it makes
    /// the authorize call fail with "Error generating ID token" (VERIFIED
    /// `desktop/src/lib/oauth.ts`), and the access token is already a valid Supabase JWT.
    pub fn authorize_url(&self, supabase_url: &str, client_id: &str) -> String {
        let enc = urlencoding::encode;
        format!(
            "{}/auth/v1/oauth/authorize?response_type=code&client_id={}&redirect_uri={}&state={}\
             &code_challenge={}&code_challenge_method=S256&scope={}",
            supabase_url.trim_end_matches('/'),
            enc(client_id),
            enc(&self.redirect_uri),
            enc(&self.state),
            enc(&code_challenge(&self.code_verifier)),
            enc("email profile"),
        )
    }
}

/// The deep-link redirect URI registered on the desktop OAuth client (S2). The same literal in
/// both worlds — it is the packaged app's scheme, and S14 covers a cross-world arrival.
pub const DEEP_LINK_REDIRECT_URI: &str = "aimatrx://auth/callback";

/// The redirect a world signs in through by default.
///
/// `dev` has no scheme handler on any OS from a source run (no `register_all()` call exists in
/// `desktop/src-tauri/src/`), so S3 makes loopback the ONLY dev redirect.
pub fn default_redirect(world: World) -> (String, RedirectKind) {
    match world {
        World::Live => (DEEP_LINK_REDIRECT_URI.to_string(), RedirectKind::DeepLink),
        World::Dev => (world.loopback_redirect_uri(), RedirectKind::Loopback),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_challenge_is_the_s256_of_the_verifier() {
        // RFC 7636 appendix B's vector.
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        assert_eq!(
            code_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn state_comparison_rejects_every_difference() {
        assert!(state_matches("abc", "abc"));
        assert!(!state_matches("abc", "abd"));
        assert!(!state_matches("abc", "ab"));
        assert!(!state_matches("", "a"));
    }

    #[test]
    fn a_transaction_expires_on_the_monotonic_clock() {
        let t = Transaction::open("x".into(), RedirectKind::Loopback, Duration::from_secs(10))
            .expect("open");
        assert!(!t.expired(Duration::from_secs(609)));
        assert!(t.expired(Duration::from_secs(611)));
    }

    #[test]
    fn two_transactions_never_share_a_verifier_or_state() {
        let a = Transaction::open("x".into(), RedirectKind::Loopback, Duration::ZERO).unwrap();
        let b = Transaction::open("x".into(), RedirectKind::Loopback, Duration::ZERO).unwrap();
        assert_ne!(a.code_verifier, b.code_verifier);
        assert_ne!(a.state, b.state);
        assert_ne!(a.transaction_id, b.transaction_id);
    }

    #[test]
    fn the_authorize_url_omits_openid_and_carries_s256() {
        let t = Transaction::open(
            "http://localhost:22261/oauth/callback".into(),
            RedirectKind::Loopback,
            Duration::ZERO,
        )
        .unwrap();
        let url = t.authorize_url("https://db.matrxserver.com/", "af37ec97");
        assert!(url.starts_with("https://db.matrxserver.com/auth/v1/oauth/authorize?"));
        assert!(url.contains("code_challenge_method=S256"));
        assert!(url.contains("scope=email%20profile"));
        assert!(!url.contains("openid"));
        assert!(url.contains("redirect_uri=http%3A%2F%2Flocalhost%3A22261%2Foauth%2Fcallback"));
        // The verifier itself never appears in the URL — only its hash.
        assert!(!url.contains(&t.code_verifier));
    }

    #[test]
    fn the_dev_world_signs_in_over_loopback_and_live_over_the_deep_link() {
        assert_eq!(
            default_redirect(World::Dev),
            (
                "http://localhost:22261/oauth/callback".to_string(),
                RedirectKind::Loopback
            )
        );
        assert_eq!(
            default_redirect(World::Live),
            (DEEP_LINK_REDIRECT_URI.to_string(), RedirectKind::DeepLink)
        );
    }
}
