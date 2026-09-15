//! The current user's SID, for C6's per-user pipe name.
//!
//! Windows only. The SID rather than the username because two accounts can share a display name
//! across a domain trust while their SIDs never collide — and D18's whole claim is that a second
//! OS user gets *nothing*.

use std::io;

/// This process's user SID in string form (`S-1-5-21-…`).
pub fn current_user_sid() -> io::Result<String> {
    use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE};
    use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
    use windows_sys::Win32::Security::{GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER};
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    // SAFETY: every call below is the documented sequence for reading the current process token's
    // user SID. Each pointer is either a stack local whose size is passed alongside it, or a
    // buffer this function allocated to the length Windows asked for; both handles are closed and
    // the string SID is freed with `LocalFree`, which is what `ConvertSidToStringSidW` documents.
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return Err(io::Error::last_os_error());
        }

        // The documented two-call pattern: ask for the size, then read into a buffer that size.
        let mut needed: u32 = 0;
        GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut needed);
        if needed == 0 {
            let e = io::Error::last_os_error();
            CloseHandle(token);
            return Err(e);
        }
        let mut buffer = vec![0u8; needed as usize];
        if GetTokenInformation(
            token,
            TokenUser,
            buffer.as_mut_ptr().cast(),
            needed,
            &mut needed,
        ) == 0
        {
            let e = io::Error::last_os_error();
            CloseHandle(token);
            return Err(e);
        }
        CloseHandle(token);

        let token_user = &*(buffer.as_ptr() as *const TOKEN_USER);
        let mut wide: *mut u16 = std::ptr::null_mut();
        if ConvertSidToStringSidW(token_user.User.Sid, &mut wide) == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut len = 0usize;
        while *wide.add(len) != 0 {
            len += 1;
        }
        let sid = String::from_utf16_lossy(std::slice::from_raw_parts(wide, len));
        LocalFree(wide.cast());
        Ok(sid)
    }
}
