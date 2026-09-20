//! Bounded, value-free call from the Rust host into the signed Swift exchange ABI.
use std::ffi::c_void;

pub const MAX_RESPONSE_BYTES: usize = 64 * 1024;

unsafe extern "C" {
    fn matrx_vault_exchange_dispatch(
        input: *const u8,
        length: isize,
        window: *mut c_void,
        output: *mut u8,
        capacity: isize,
    ) -> isize;
}

/// Must be called on the macOS main thread. The Swift ABI refuses smaller buffers.
pub fn dispatch(input: &[u8], window: *mut c_void) -> Option<Vec<u8>> {
    let mut output = vec![0u8; MAX_RESPONSE_BYTES];
    let length = unsafe {
        matrx_vault_exchange_dispatch(
            input.as_ptr(),
            input.len() as isize,
            window,
            output.as_mut_ptr(),
            output.len() as isize,
        )
    };
    if !(0..=MAX_RESPONSE_BYTES as isize).contains(&length) {
        return None;
    }
    output.truncate(length as usize);
    Some(output)
}
