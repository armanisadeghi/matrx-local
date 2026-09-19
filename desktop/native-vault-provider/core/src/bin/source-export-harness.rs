//! Writes only synthetic test DER to a caller-owned private path.

use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use coset::{CborSerializable, iana};
use native_vault_core::export_source_v1_pkcs8;
use p256::{SecretKey, elliptic_curve::sec1::ToEncodedPoint, pkcs8::EncodePublicKey};
use serde_json::json;
use std::{env, fs, path::PathBuf};

fn fixture() -> Vec<u8> {
    let key = SecretKey::from_slice(&[7; 32]).expect("fixed scalar");
    let public = key.public_key().to_encoded_point(false);
    let cose = coset::CoseKeyBuilder::new_ec2_priv_key(
        iana::EllipticCurve::P_256,
        public.x().expect("x").to_vec(),
        public.y().expect("y").to_vec(),
        key.to_bytes().to_vec(),
    )
    .algorithm(iana::Algorithm::ES256)
    .build()
    .to_vec()
    .expect("cose");
    serde_json::to_vec(&json!({
        "version": 1,
        "credential_id": URL_SAFE_NO_PAD.encode([1_u8; 16]),
        "rp_id": "example.com",
        "user_handle": URL_SAFE_NO_PAD.encode(b"user"),
        "username": "synthetic-user",
        "display_name": "Synthetic user",
        "private_cose_key": URL_SAFE_NO_PAD.encode(cose),
        "counter": null,
        "extensions": {},
        "backup_eligible": true,
        "backup_state": true
    }))
    .expect("fixture")
}

fn main() {
    let output = env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .expect("output path");
    let public_output = env::args_os()
        .nth(2)
        .map(PathBuf::from)
        .expect("public output path");
    let source = fixture();
    let export = export_source_v1_pkcs8(&source, 4096).expect("export");
    fs::write(output, export.pkcs8_der()).expect("write private test artifact");
    let public = SecretKey::from_slice(&[7; 32])
        .expect("fixed scalar")
        .public_key()
        .to_public_key_der()
        .expect("public der");
    fs::write(public_output, public.as_bytes()).expect("write public test artifact");
}
