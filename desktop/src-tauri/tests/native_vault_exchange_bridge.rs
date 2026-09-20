// Regression: the historical 2 KiB Rust caller was rejected by Swift's 64 KiB ABI floor.
#[path = "../src/native_vault_exchange_bridge.rs"]
mod native_vault_exchange_bridge;

use std::ffi::c_void;

unsafe extern "C" {
    fn matrx_vault_exchange_dispatch(
        input: *const u8,
        length: isize,
        window: *mut c_void,
        output: *mut u8,
        capacity: isize,
    ) -> isize;
}

fn main() {
    let input = br#"{"action":"invalidate"}"#;
    let mut legacy_output = vec![0u8; 2 * 1024];
    let legacy_length = unsafe {
        matrx_vault_exchange_dispatch(
            input.as_ptr(),
            input.len() as isize,
            std::ptr::null_mut(),
            legacy_output.as_mut_ptr(),
            legacy_output.len() as isize,
        )
    };
    assert_eq!(
        legacy_length, -1,
        "the historical 2 KiB caller must be refused"
    );

    let response = native_vault_exchange_bridge::dispatch(input, std::ptr::null_mut())
        .expect("the production Rust caller must satisfy Swift's bounded ABI");
    assert_eq!(
        response, b"{}",
        "invalidate must complete through the actual Swift ABI"
    );
    println!("PASS native Vault Rust caller rejects legacy 2 KiB capacity and accepts its 64 KiB bounded response");
}
