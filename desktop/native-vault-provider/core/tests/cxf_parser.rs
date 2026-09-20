use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use native_vault_core::{
    cxf::{CxfError, parse_cxf_v1, parse_cxf_v1_with_cancel},
    export_source_v1_pkcs8,
};
use p256::{
    SecretKey,
    elliptic_curve::sec1::ToEncodedPoint,
    pkcs8::{DecodePrivateKey, EncodePrivateKey},
};
use zeroize::Zeroizing;

fn fixture() -> (Vec<u8>, SecretKey) {
    let secret = SecretKey::from_slice(&[7; 32]).unwrap();
    let der = secret.to_pkcs8_der().unwrap();
    let doc = serde_json::json!({
        "version":{"major":1,"minor":0}, "exporterRpId":"example.com", "exporterDisplayName":"Example", "timestamp":0,
        "accounts":[{"id":URL_SAFE_NO_PAD.encode(b"account"),"username":"","email":"","collections":[],"items":[{
            "id":URL_SAFE_NO_PAD.encode(b"item"),"title":"Example passkey","credentials":[{
                "type":"passkey","credentialId":URL_SAFE_NO_PAD.encode([1_u8;16]),"rpId":"example.com","username":"","userDisplayName":"","userHandle":URL_SAFE_NO_PAD.encode(b"user"),"key":URL_SAFE_NO_PAD.encode(der.as_bytes())
            }]
        }]}]
    });
    (serde_json::to_vec(&doc).unwrap(), secret)
}

#[test]
fn strict_cxf_converts_pkcs8_and_keeps_original_public_key() {
    let (bytes, original) = fixture();
    let inventory = parse_cxf_v1(Zeroizing::new(bytes)).unwrap();
    assert_eq!(inventory.total_items(), 1);
    assert_eq!(inventory.unsupported_items(), 0);
    assert_eq!(inventory.candidates().len(), 1);
    assert_eq!(inventory.candidates()[0].title(), "Example passkey");
    let export =
        export_source_v1_pkcs8(inventory.candidates()[0].canonical_source(), 65_536).unwrap();
    let restored = SecretKey::from_pkcs8_der(export.pkcs8_der()).unwrap();
    assert_eq!(
        restored.public_key().to_encoded_point(false),
        original.public_key().to_encoded_point(false)
    );
}

#[test]
fn duplicate_unknown_extensions_and_noncanonical_binary_refuse() {
    let (bytes, _) = fixture();
    let duplicate = String::from_utf8(bytes.clone()).unwrap().replacen(
        "\"title\":\"Example passkey\"",
        "\"title\":\"Example passkey\",\"tit\\u006ce\":\"other\"",
        1,
    );
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(duplicate.into_bytes())),
        Err(CxfError::UnsupportedFormat)
    ));
    let extension = String::from_utf8(bytes.clone()).unwrap().replacen(
        "\"key\":",
        "\"fido2Extensions\":{},\"key\":",
        1,
    );
    assert!(parse_cxf_v1(Zeroizing::new(extension.into_bytes())).is_ok());
    let extension = String::from_utf8(bytes.clone()).unwrap().replacen(
        "\"key\":",
        "\"fido2Extensions\":{\"credBlob\":\"AA\"},\"key\":",
        1,
    );
    let inventory = parse_cxf_v1(Zeroizing::new(extension.into_bytes())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.unsupported_items(),
            inventory.candidates().len()
        ),
        (1, 1, 0)
    );
    let noncanonical = String::from_utf8(bytes).unwrap().replacen(
        "\"credentialId\":\"AQEBAQEBAQEBAQEBAQEBAQ\"",
        "\"credentialId\":\"AQEBAQEBAQEBAQEBAQEBAQ==\"",
        1,
    );
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(noncanonical.into_bytes())),
        Err(CxfError::UnsupportedFormat)
    ));
}

#[test]
fn depth_and_document_limits_fail_before_retaining_candidates() {
    let (bytes, _) = fixture();
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(vec![b' '; 16 * 1024 * 1024 + 1])),
        Err(CxfError::TransferLimit)
    ));
    let too_many = String::from_utf8(bytes).unwrap().replacen(
        "\"accounts\":[",
        &format!("\"accounts\":[{}", "{} ,".repeat(33)),
        1,
    );
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(too_many.into_bytes())),
        Err(CxfError::TransferLimit) | Err(CxfError::UnsupportedFormat)
    ));
}

#[test]
fn unsupported_credential_type_after_nested_private_shape_marks_the_whole_item() {
    // Removing the probe and using the old type-order branch would reject this password
    // object or retain a sibling passkey. The real walker must account for one whole item.
    let (bytes, _) = fixture();
    let text = String::from_utf8(bytes).unwrap().replacen(
        r#""credentials":[{"type":"passkey""#,
        r#""credentials":[{"password":{"value":"not retained","nested":[{"x":true}]},"type":"password"},{"type":"passkey""#,
        1,
    );
    let inventory = parse_cxf_v1(Zeroizing::new(text.into_bytes())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.unsupported_items(),
            inventory.candidates().len()
        ),
        (1, 1, 0)
    );
}

#[test]
fn cancellation_before_first_item_drops_the_operation() {
    let (bytes, _) = fixture();
    let result = parse_cxf_v1_with_cancel(Zeroizing::new(bytes), || true);
    assert!(matches!(result, Err(CxfError::Cancelled)));
}

fn value_fixture() -> serde_json::Value {
    let (bytes, _) = fixture();
    serde_json::from_slice(&bytes).unwrap()
}

#[test]
fn known_optional_hierarchy_is_validated_but_scoped_passkey_is_accounted() {
    let mut doc = value_fixture();
    let account = &mut doc["accounts"][0];
    account["collections"] = serde_json::json!([{
        "id": URL_SAFE_NO_PAD.encode(b"collection"), "title": "Archive",
        "items": [{"item": URL_SAFE_NO_PAD.encode(b"item")}],
        "subCollections": [{"id": URL_SAFE_NO_PAD.encode(b"sub"), "title": "Sub", "items": []}]
    }]);
    let item = &mut account["items"][0];
    item["creationAt"] = serde_json::json!(0);
    item["modifiedAt"] = serde_json::json!(0);
    item["subtitle"] = serde_json::json!("");
    item["favorite"] = serde_json::json!(false);
    item["tags"] = serde_json::json!(["personal"]);
    item["scope"] = serde_json::json!({"urls": ["https://example.com"], "androidApps": [{"bundleId": "com.example.app", "certificate": {"fingerprint": URL_SAFE_NO_PAD.encode([1_u8; 32]), "hashAlg": "sha256"}}]});
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.unsupported_items(),
            inventory.candidates().len()
        ),
        (1, 1, 0)
    );
}

#[test]
fn duplicate_escaped_extension_key_and_trailing_data_refuse_the_entire_document() {
    let (bytes, _) = fixture();
    let duplicate = String::from_utf8(bytes.clone()).unwrap().replacen(
        r#""key":"#,
        r#""fido2Extensions":{"x":1,"\u0078":2},"key":"#,
        1,
    );
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(duplicate.into_bytes())),
        Err(CxfError::UnsupportedFormat)
    ));
    let mut trailing = bytes;
    trailing.extend_from_slice(b" true");
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(trailing)),
        Err(CxfError::UnsupportedFormat)
    ));
}

#[test]
fn counters_reject_the_33rd_account_or_33rd_credential_before_retention() {
    let mut accounts = value_fixture();
    let account = accounts["accounts"][0].clone();
    accounts["accounts"] = serde_json::Value::Array(vec![account; 33]);
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&accounts).unwrap())),
        Err(CxfError::TransferLimit)
    ));

    let mut credentials = value_fixture();
    let item = &mut credentials["accounts"][0]["items"][0];
    item["credentials"] = serde_json::Value::Array(
        (0..33)
            .map(|_| serde_json::json!({"type":"note","content":{"value":"not imported"}}))
            .collect(),
    );
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&credentials).unwrap())),
        Err(CxfError::TransferLimit)
    ));
}

#[test]
fn mixed_unsupported_items_have_exact_whole_item_accounting() {
    let mut doc = value_fixture();
    let item = doc["accounts"][0]["items"][0].clone();
    let unsupported = serde_json::json!({
        "id": URL_SAFE_NO_PAD.encode(b"item-2"), "title": "Non-passkey",
        "credentials": [{"content":{"deep":[{"v":"never retained"}]},"type":"note"}]
    });
    doc["accounts"][0]["items"] = serde_json::Value::Array(vec![item, unsupported]);
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.unsupported_items(),
            inventory.candidates().len()
        ),
        (2, 1, 1)
    );
}

#[test]
fn cancellation_during_nested_credential_scan_prevents_an_inventory() {
    use std::cell::Cell;
    let (bytes, _) = fixture();
    let calls = Cell::new(0usize);
    let result = parse_cxf_v1_with_cancel(Zeroizing::new(bytes), || {
        calls.set(calls.get() + 1);
        calls.get() >= 4
    });
    assert!(matches!(result, Err(CxfError::Cancelled)));
}

fn nested_object(levels: usize) -> serde_json::Value {
    let mut value = serde_json::json!("bounded");
    for _ in 0..levels {
        value = serde_json::json!({"nested": value});
    }
    value
}

#[test]
fn nested_unknown_credential_data_stops_at_depth_32() {
    let mut admitted = value_fixture();
    admitted["accounts"][0]["items"][0]["credentials"] =
        serde_json::json!([{"type":"note", "content": nested_object(24)}]);
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&admitted).unwrap())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.unsupported_items(),
            inventory.candidates().len()
        ),
        (1, 1, 0)
    );

    let mut rejected = value_fixture();
    rejected["accounts"][0]["items"][0]["credentials"] =
        serde_json::json!([{"type":"note", "content": nested_object(25)}]);
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&rejected).unwrap())),
        Err(CxfError::TransferLimit)
    ));
}

#[test]
fn decoded_binary_boundaries_are_accepted_and_over_bounds_refuse() {
    let mut doc = value_fixture();
    let credential = vec![3_u8; 1024];
    let handle = vec![4_u8; 64];
    doc["accounts"][0]["items"][0]["credentials"][0]["credentialId"] =
        serde_json::json!(URL_SAFE_NO_PAD.encode(&credential));
    doc["accounts"][0]["items"][0]["credentials"][0]["userHandle"] =
        serde_json::json!(URL_SAFE_NO_PAD.encode(&handle));
    let result = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap()));
    assert!(result.is_ok(), "{:?}", result.err());
    doc["accounts"][0]["items"][0]["credentials"][0]["credentialId"] =
        serde_json::json!(URL_SAFE_NO_PAD.encode(vec![3_u8; 1025]));
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap())),
        Err(CxfError::UnsupportedFormat)
    ));
}

#[test]
fn multi_credential_item_is_one_ordered_unsupported_preview_slot() {
    let mut doc = value_fixture();
    let duplicate = doc["accounts"][0]["items"][0]["credentials"][0].clone();
    doc["accounts"][0]["items"][0]["credentials"] =
        serde_json::json!([duplicate.clone(), duplicate]);
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap())).unwrap();
    assert_eq!(
        (
            inventory.total_items(),
            inventory.candidates().len(),
            inventory.unsupported_items()
        ),
        (1, 0, 1)
    );
    assert_eq!(inventory.unsupported()[0].index(), 0);
    assert_eq!(inventory.unsupported()[0].title(), "Example passkey");
    assert_eq!(
        inventory.unsupported()[0].reason(),
        native_vault_core::cxf::CxfUnsupportedReason::MixedOrMultipleCredentials
    );
}

#[test]
fn fixed_scratch_capacity_is_input_bounded_and_clone_preserves_it() {
    let escaped = br#""aaaaaaaa\u0061""#;
    let p = jiter::Jiter::with_bounded_tape_capacity(escaped, escaped.len());
    assert_eq!(p.bounded_tape_capacity(), Some(escaped.len()));
    let mut clone = p.clone();
    assert_eq!(clone.bounded_tape_capacity(), Some(escaped.len()));
    assert_eq!(
        clone
            .next_bounded_str(escaped.len(), escaped.len())
            .unwrap(),
        "aaaaaaaaa"
    );
}

fn json_escape_ascii(value: &str) -> String {
    value.bytes().map(|byte| format!("\\u{byte:04x}")).collect()
}

#[test]
fn accepts_six_times_raw_spelling_at_credential_and_handle_binary_ceilings() {
    // The parser must apply binary ceilings after JSON unescaping/base64 decoding.
    let mut doc = value_fixture();
    let credential = URL_SAFE_NO_PAD.encode(vec![3_u8; 1024]);
    let handle = URL_SAFE_NO_PAD.encode(vec![4_u8; 64]);
    doc["accounts"][0]["items"][0]["credentials"][0]["credentialId"] =
        serde_json::json!(&credential);
    doc["accounts"][0]["items"][0]["credentials"][0]["userHandle"] = serde_json::json!(&handle);
    let raw = String::from_utf8(serde_json::to_vec(&doc).unwrap()).unwrap();
    let raw = raw
        .replacen(&credential, &json_escape_ascii(&credential), 1)
        .replacen(&handle, &json_escape_ascii(&handle), 1);
    let inventory = parse_cxf_v1(Zeroizing::new(raw.into_bytes())).unwrap();
    assert_eq!(
        (inventory.total_items(), inventory.candidates().len()),
        (1, 1)
    );
}

#[test]
fn reports_one_note_as_unsupported_credential_not_mixed() {
    let mut doc = value_fixture();
    doc["accounts"][0]["items"][0]["credentials"] =
        serde_json::json!([{"type":"note", "content":"not a passkey"}]);
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&doc).unwrap())).unwrap();
    assert_eq!(inventory.unsupported().len(), 1);
    assert_eq!(
        inventory.unsupported()[0].reason(),
        native_vault_core::cxf::CxfUnsupportedReason::UnsupportedCredential
    );
}

#[test]
fn supported_and_unsupported_algorithms_keep_ordered_inventory() {
    let (bytes, _) = fixture();
    let mut document: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    let mut unsupported = document["accounts"][0]["items"][0].clone();
    unsupported["title"] = "P384 unsupported".into();
    // Synthetic P-384 scalar 7, encoded by cryptography's maintained PKCS8 encoder.
    unsupported["credentials"][0]["key"] = "MIG2AgEAMBAGByqGSM49AgEGBSuBBAAiBIGeMIGbAgEBBDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAehZANiAAQoPB1zZc5HiPKfjr8jTt_-rW_pl_vqX_otWMyd-nscUIsFUm9VueuyBA8FtI-20OGUdcmQYeQbiLpS79uMFpBHGmHYZ-15lynZySzQHb0iVjDYTt4yp4-eZGZM2sUS74w".into();
    document["accounts"][0]["items"]
        .as_array_mut()
        .unwrap()
        .insert(0, unsupported);
    let inventory = parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&document).unwrap())).unwrap();
    assert_eq!(inventory.total_items(), 2);
    assert_eq!(inventory.candidates().len(), 1);
    assert_eq!(inventory.unsupported_items(), 1);
    assert_eq!(inventory.unsupported()[0].index(), 0);
    assert_eq!(inventory.unsupported()[0].title(), "P384 unsupported");
    assert_eq!(
        inventory.unsupported()[0].reason(),
        native_vault_core::cxf::CxfUnsupportedReason::UnsupportedCredential
    );
    let key = document["accounts"][0]["items"][0]["credentials"][0]["key"]
        .as_str()
        .unwrap();
    let mut malformed = URL_SAFE_NO_PAD.decode(key).unwrap();
    malformed.pop();
    document["accounts"][0]["items"][0]["credentials"][0]["key"] =
        URL_SAFE_NO_PAD.encode(malformed).into();
    assert!(matches!(
        parse_cxf_v1(Zeroizing::new(serde_json::to_vec(&document).unwrap())),
        Err(CxfError::UnsupportedFormat)
    ));
}
