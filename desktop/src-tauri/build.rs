fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        build_native_vault_exchange();
    }
    // whisper-cpp-plus-sys builds whisper.cpp via cmake, which detects and enables
    // OpenMP on Linux. We must explicitly link libgomp so the linker resolves
    // GOMP_parallel, omp_get_thread_num, etc.
    #[cfg(target_os = "linux")]
    println!("cargo:rustc-link-lib=gomp");

    tauri_build::build()
}

fn build_native_vault_exchange() {
    use std::{path::PathBuf, process::Command};
    let desktop = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .parent()
        .unwrap()
        .to_owned();
    let output = PathBuf::from(std::env::var_os("OUT_DIR").unwrap()).join("native-vault-exchange");
    let target = std::env::var("TARGET").unwrap();
    let custody_guard = desktop
        .parent()
        .unwrap()
        .join("scripts/check-native-vault-custody.py");
    println!("cargo:rerun-if-changed={}", custody_guard.display());
    for path in ["src", "src-tauri/src"] {
        println!("cargo:rerun-if-changed={}", desktop.join(path).display());
    }
    let guard = Command::new("python3")
        .arg(&custody_guard)
        .status()
        .expect("Python is required for the native Vault custody check");
    assert!(guard.success(), "Native Vault custody check failed");
    for path in [
        "native-vault-provider/core/src",
        "native-vault-provider/core/vendor",
        "native-vault-provider/core/Cargo.toml",
        "native-vault-provider/core/Cargo.lock",
        "scripts/build-native-vault-exchange.sh",
        "scripts/build-native-vault-bridge.sh",
    ] {
        println!("cargo:rerun-if-changed={}", desktop.join(path).display());
    }
    for entry in std::fs::read_dir(desktop.join("native-vault-provider"))
        .expect("Native Vault sources are required")
    {
        let path = entry.expect("Native Vault source entry").path();
        if path
            .extension()
            .is_some_and(|extension| extension == "swift")
        {
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }
    let status = Command::new("bash")
        .arg(desktop.join("scripts/build-native-vault-exchange.sh"))
        .arg(&target)
        .arg(&output)
        .status()
        .expect("Could not start native Vault build");
    assert!(status.success(), "Native Vault exchange build failed");
    let swift = Command::new("xcrun")
        .args(["--find", "swiftc"])
        .output()
        .expect("Swift toolchain is required");
    assert!(swift.status.success(), "Swift compiler lookup failed");
    let swift = PathBuf::from(String::from_utf8(swift.stdout).unwrap().trim());
    let toolchain = swift.parent().unwrap().parent().unwrap();
    println!("cargo:rustc-link-search=native={}", output.display());
    println!(
        "cargo:rustc-link-search=native={}",
        toolchain.join("lib/swift/macosx").display()
    );
    println!("cargo:rustc-link-search=native=/usr/lib/swift");
    println!(
        "cargo:rustc-link-search=native={}",
        desktop
            .join(format!(
                "native-vault-provider/core/target/{target}/release"
            ))
            .display()
    );
    println!("cargo:rustc-link-lib=static=matrx_vault_exchange");
    println!("cargo:rustc-link-lib=static=native_vault_core");
    for framework in [
        "AppKit",
        "AuthenticationServices",
        "CryptoKit",
        "LocalAuthentication",
        "Security",
    ] {
        println!("cargo:rustc-link-lib=framework={framework}");
    }
    // Mach-O object LC_LINKER_OPTION records carry Swift autolink directives.
    println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
}
