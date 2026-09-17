//! The daemon must advertise the version of the desktop payload that bundled it.

use std::process::Command;

#[test]
fn cli_version_reports_the_baked_desktop_payload_version() {
    let output = Command::new(env!("CARGO_BIN_EXE_matrx-syncd"))
        .arg("--version")
        .output()
        .expect("run matrx-syncd --version");

    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).expect("version output is UTF-8");
    assert!(
        stdout.starts_with(&format!("matrx-syncd {} ", env!("MATRX_SYNCD_APP_VERSION"))),
        "unexpected version output: {stdout:?}"
    );
}
