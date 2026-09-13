mod support;
use support::*;

fn register() -> String {
    r#"{"command":"register","id":"request-1","options":{"rp":{"id":"example.com","name":"Example"},"user":{"id":"dXNlcg","name":"user","displayName":"User"},"challenge":"Y2hhbGxlbmdl","pubKeyCredParams":[{"type":"public-key","alg":-7}],"authenticatorSelection":{"residentKey":"required","userVerification":"required"},"attestation":"none"},"origin":"https://example.com","uv":true,"persistence":"success"}"#.into()
}
#[test]
fn strict_nested_register_is_accepted() {
    assert!(matches!(
        parse(register().as_bytes()),
        Ok(Frame::Register { .. })
    ));
}
#[test]
fn rejects_unknown_duplicate_null_and_noncanonical_wire_fields() {
    for bad in [
        register().replace(
            "\"attestation\":\"none\"",
            "\"attestation\":\"none\",\"unknown\":true",
        ),
        register().replace(
            "\"id\":\"request-1\"",
            "\"id\":\"request-1\",\"id\":\"request-2\"",
        ),
        register().replace(
            "\"attestation\":\"none\"",
            "\"timeout\":null,\"attestation\":\"none\"",
        ),
        register().replace("\"dXNlcg\"", "\"dXNlcg=\""),
    ] {
        assert!(matches!(parse(bad.as_bytes()), Err(WireError::Invalid)));
    }
}
#[test]
fn rejects_policy_and_origin_deviations() {
    for bad in [
        register().replace("https://example.com", "http://example.com"),
        register().replace("\"required\"", "\"preferred\""),
        register().replace("\"persistence\":\"success\"", "\"persistence\":\"other\""),
    ] {
        assert!(matches!(parse(bad.as_bytes()), Err(WireError::Invalid)));
    }
}
#[test]
fn frame_reader_enforces_newline_and_one_mebibyte_including_newline() {
    assert_eq!(
        read_frames(format!("{}\n", register()).as_bytes())
            .unwrap()
            .len(),
        1
    );
    assert_eq!(read_frames(register().as_bytes()), Err(WireError::Invalid));
    assert_eq!(
        read_frames(vec![b'x'; MAX_FRAME_BYTES + 1].as_slice()),
        Err(WireError::Invalid)
    );
}
