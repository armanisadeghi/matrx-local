//! Feature-gated, pipe-only custody proof for a synthetic CXF import.
#[path = "../../tests/support/mod.rs"]
mod support;

use native_vault_core::cxf::parse_cxf_v1;
use serde::{Deserialize, Serialize};
use std::io::{self, Read, Write};
use zeroize::Zeroizing;
const PRIVATE_FD: &str = "NATIVE_VAULT_PROOF_PRIVATE_FD";
#[cfg(unix)]
fn private_file() -> Result<std::fs::File, ()> {
    use std::os::fd::FromRawFd;
    let fd = std::env::var(PRIVATE_FD).map_err(|_| ())?.parse::<i32>().map_err(|_| ())?;
    if fd < 3 { return Err(()); }
    Ok(unsafe { std::fs::File::from_raw_fd(fd) })
}
#[cfg(not(unix))]
fn private_file() -> Result<std::fs::File, ()> { Err(()) }
#[derive(Serialize)]
struct ParsedPublic<'a> { credential_id: &'a str, user_handle: &'a str, total: usize, unsupported: usize }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AssertRequest { challenge: support::CanonicalB64, credential_id: support::CanonicalB64 }
fn source_public(source: &[u8]) -> Result<(String, String), ()> {
    let value: serde_json::Value = serde_json::from_slice(source).map_err(|_| ())?;
    Ok((value.get("credential_id").and_then(|v| v.as_str()).ok_or(())?.to_owned(), value.get("user_handle").and_then(|v| v.as_str()).ok_or(())?.to_owned()))
}
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), ()> {
    match std::env::args().nth(1).as_deref() {
        Some("parse") => {
            let mut raw = Zeroizing::new(Vec::new()); io::stdin().read_to_end(&mut *raw).map_err(|_| ())?;
            let inventory = parse_cxf_v1(raw).map_err(|_| ())?;
            if inventory.candidates().len() != 1 { return Err(()); }
            let source = inventory.candidates()[0].canonical_source();
            let (credential_id, user_handle) = source_public(source)?;
            let mut private = private_file()?; private.write_all(source).map_err(|_| ())?; private.flush().map_err(|_| ())?;
            serde_json::to_writer(io::stdout(), &ParsedPublic { credential_id: &credential_id, user_handle: &user_handle, total: inventory.total_items(), unsupported: inventory.unsupported_items() }).map_err(|_| ())?;
        }
        Some("assert") => {
            let mut source = Zeroizing::new(Vec::new()); private_file()?.read_to_end(&mut *source).map_err(|_| ())?;
            let request: AssertRequest = serde_json::from_reader(io::stdin()).map_err(|_| ())?;
            let id = request.credential_id.0.clone();
            let frame = support::Frame::Authenticate { id: "import-rp-proof".into(), options: support::GetOptions { challenge: request.challenge, rp_id: "example.com".into(), allow: support::Optional(Some(vec![support::Descriptor { ty: "public-key".into(), id: support::CanonicalB64(id.clone()), transports: support::Optional(None) }])), user_verification: "required".into(), timeout: support::Optional(None), extensions: support::Optional(None), hints: support::Optional(None) }, origin: "https://example.com".into(), uv: true };
            let result = support::wire_assertion(frame, &source, &id).await.map_err(|_| ())?;
            serde_json::to_writer(io::stdout(), &result).map_err(|_| ())?;
        }
        _ => return Err(()),
    }
    Ok(())
}
