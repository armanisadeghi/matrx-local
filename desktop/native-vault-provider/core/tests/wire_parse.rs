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
    let complete = format!("{}\n", register());
    let mut reader = FrameReader::new(complete.as_bytes());
    assert!(reader.next_frame().unwrap().is_ok());
    assert_eq!(reader.next_frame(), None);
    let partial_input = register();
    let mut partial = FrameReader::new(partial_input.as_bytes());
    assert_eq!(partial.next_frame(), Some(Err(WireError::Invalid)));
    let valid = format!("{}\n", register());
    let mut bytes = vec![b'x'; MAX_FRAME_BYTES];
    bytes.push(b'\n');
    bytes.extend(valid.bytes());
    let mut oversized = FrameReader::new(bytes.as_slice());
    assert_eq!(oversized.next_frame(), Some(Err(WireError::Invalid)));
    assert!(oversized.next_frame().unwrap().is_ok());
}
