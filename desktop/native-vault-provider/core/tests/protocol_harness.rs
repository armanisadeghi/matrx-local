#![cfg(feature = "protocol-test-harness")]

use serde::Deserialize;
use std::io::Write;
use std::process::{Command, Stdio};

#[derive(Deserialize)]
struct Response {
    id: Option<String>,
    ok: bool,
    code: Option<String>,
    credential: Option<Credential>,
}
#[derive(Deserialize)]
struct Credential {
    id: String,
    #[serde(rename = "type")]
    ty: String,
}

fn register(id: &str, persistence: &str) -> String {
    format!(
        r#"{{"command":"register","id":"{id}","options":{{"rp":{{"id":"example.com","name":"Example"}},"user":{{"id":"dXNlcg","name":"user","displayName":"User"}},"challenge":"Y2hhbGxlbmdl","pubKeyCredParams":[{{"type":"public-key","alg":-7}}],"authenticatorSelection":{{"residentKey":"required","userVerification":"required"}},"attestation":"none"}},"origin":"https://example.com","uv":true,"persistence":"{persistence}"}}"#
    )
}
fn authenticate() -> String {
    r#"{"command":"authenticate","id":"get-1","options":{"challenge":"Z2V0LWNoYWxsZW5nZQ","rpId":"example.com","userVerification":"required"},"origin":"https://example.com","uv":true}"#.into()
}
fn run(input: String) -> Vec<Response> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_protocol-harness"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[test]
fn process_registers_then_authenticates_without_source_output() {
    let out = run(format!(
        "{}\n{}\n",
        register("make-1", "success"),
        authenticate()
    ));
    assert_eq!(out.len(), 2);
    assert!(out[0].ok && out[1].ok);
    assert_eq!(out[0].id.as_deref(), Some("make-1"));
    assert_eq!(out[1].id.as_deref(), Some("get-1"));
    assert_eq!(out[0].credential.as_ref().unwrap().ty, "public-key");
    assert_eq!(
        out[1].credential.as_ref().unwrap().id,
        out[0].credential.as_ref().unwrap().id
    );
}

#[test]
fn process_reports_fixed_failures_and_never_commits_failed_or_cancelled_registration() {
    let cancel = register("cancel-1", "success")
        .replace("\"register\"", "\"cancel_register\"")
        .replace(",\"persistence\":\"success\"", "");
    let out = run(format!(
        "{}\n{}\n{{bad}}\n{}\n",
        register("fail-1", "failure"),
        cancel,
        authenticate()
    ));
    assert_eq!(out.len(), 4);
    assert_eq!(out[0].code.as_deref(), Some("PersistenceFailed"));
    assert_eq!(out[1].code.as_deref(), Some("VerificationDenied"));
    assert_eq!(out[2].id, None);
    assert_eq!(out[2].code.as_deref(), Some("InvalidRequest"));
    assert_eq!(out[3].code.as_deref(), Some("NoCredentials"));
    assert!(
        out.iter()
            .filter_map(|r| r.credential.as_ref())
            .all(|c| !c.id.contains("private"))
    );
}

#[test]
fn process_keeps_a_valid_id_when_strict_option_validation_fails() {
    let invalid_origin =
        register("echo-me", "success").replace("https://example.com", "http://example.com");
    let out = run(format!("{invalid_origin}\n"));
    assert_eq!(out.len(), 1);
    assert_eq!(out[0].id.as_deref(), Some("echo-me"));
    assert_eq!(out[0].code.as_deref(), Some("InvalidRequest"));
}
