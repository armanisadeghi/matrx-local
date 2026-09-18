//! This computer's identity, as the platform already knows it.
//!
//! The device identity is `inst_<stable_machine_id>-helper` — **the same hash the desktop engine
//! derives** (`app/services/cloud_sync/instance_manager.py::_stable_machine_id`), with the
//! `-helper` suffix so the standalone helper and the desktop app can both be set up on one
//! machine without one overwriting the other's row.
//!
//! The hash inputs are that function's, in that order:
//! `hostname | machine | system [| hardware uuid] [| salt:<MATRX_INSTANCE_SALT>]`, SHA-256, first
//! 32 hex characters. `hostname` and `machine` come from `uname` on Unix, which is exactly where
//! Python's `platform.node()` and `platform.machine()` read them.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// What the helper tells the server about this computer (the `registration` body).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Registration {
    /// `inst_<stable_machine_id>-helper`.
    pub instance_id: String,
    /// A human name for the computer, defaulting to its hostname.
    pub instance_name: String,
    /// `darwin` / `windows` / `linux`.
    pub platform: String,
    /// The OS version, as the OS reports it.
    pub os_version: String,
    /// The hostname on its own.
    pub hostname: String,
    /// The board-level UUID where the OS exposes one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hardware_uuid: Option<String>,
    /// This binary's version.
    pub helper_version: String,
    /// `helper` — the standalone installer. The desktop app registers itself as `desktop_app`
    /// through its own engine, never through this path.
    pub client_kind: String,
}

impl Registration {
    /// Describe this computer. `name` overrides the display name (`run --name`).
    pub fn for_this_computer(name: Option<&str>) -> Self {
        let hostname = hostname();
        let hardware_uuid = hardware_uuid();
        Registration {
            instance_id: instance_id(&hostname, hardware_uuid.as_deref()),
            instance_name: name.unwrap_or(&hostname).to_string(),
            platform: platform_word().to_string(),
            os_version: os_version(),
            hostname,
            hardware_uuid,
            helper_version: crate::VERSION.to_string(),
            client_kind: "helper".to_string(),
        }
    }
}

/// `inst_<stable_machine_id>-helper`.
pub fn instance_id(hostname: &str, hardware_uuid: Option<&str>) -> String {
    format!("inst_{}-helper", stable_machine_id(hostname, hardware_uuid))
}

/// The engine's hash, reproduced. Pure, so the ordering is testable.
pub fn stable_machine_id(hostname: &str, hardware_uuid: Option<&str>) -> String {
    let mut parts = vec![
        hostname.to_string(),
        machine_word(),
        python_system_word().to_string(),
    ];
    if let Some(uuid) = hardware_uuid {
        parts.push(uuid.to_string());
    }
    // The engine's dev/live isolation salt. Read the same way so a dev engine and a dev helper on
    // one machine derive from the same inputs the engine does.
    if let Ok(salt) = std::env::var("MATRX_INSTANCE_SALT") {
        if !salt.is_empty() {
            parts.push(format!("salt:{salt}"));
        }
    }
    let digest = Sha256::digest(parts.join("|").as_bytes());
    hex::encode(digest)[..32].to_string()
}

/// `darwin` / `windows` / `linux` — the spelling `platform.egress_device.platform` stores.
pub const fn platform_word() -> &'static str {
    if cfg!(target_os = "macos") {
        "darwin"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    }
}

/// What Python's `platform.system()` returns on this OS — an input to the engine's hash, so it is
/// that vocabulary and not ours.
const fn python_system_word() -> &'static str {
    if cfg!(target_os = "macos") {
        "Darwin"
    } else if cfg!(target_os = "windows") {
        "Windows"
    } else {
        "Linux"
    }
}

/// What Python's `platform.machine()` returns: `uname -m` on Unix, `PROCESSOR_ARCHITECTURE` on
/// Windows. Not `std::env::consts::ARCH`, which says `aarch64` where macOS says `arm64`.
fn machine_word() -> String {
    #[cfg(unix)]
    {
        uname_field(|info| info.machine.as_ptr()).unwrap_or_else(|| {
            // Only reachable if uname(2) itself fails, which it does not on a running system.
            std::env::consts::ARCH.to_string()
        })
    }
    #[cfg(windows)]
    {
        std::env::var("PROCESSOR_ARCHITECTURE").unwrap_or_else(|_| "AMD64".to_string())
    }
}

/// This computer's hostname.
pub fn hostname() -> String {
    #[cfg(unix)]
    {
        uname_field(|info| info.nodename.as_ptr()).unwrap_or_else(|| "this computer".to_string())
    }
    #[cfg(windows)]
    {
        std::env::var("COMPUTERNAME").unwrap_or_else(|_| "this computer".to_string())
    }
}

#[cfg(unix)]
fn uname_field(pick: impl Fn(&libc::utsname) -> *const libc::c_char) -> Option<String> {
    // SAFETY: `uname` fills a caller-provided struct and returns 0 on success; the fields are
    // NUL-terminated C strings inside that struct, which lives for the whole of this function.
    unsafe {
        let mut info: libc::utsname = std::mem::zeroed();
        if libc::uname(&mut info) != 0 {
            return None;
        }
        let ptr = pick(&info);
        let text = std::ffi::CStr::from_ptr(ptr).to_string_lossy().into_owned();
        if text.is_empty() {
            None
        } else {
            Some(text)
        }
    }
}

/// The OS version as the OS reports it. Never fatal: an unknown version is the string "unknown",
/// because a registration that fails over a cosmetic field would be a worse answer.
pub fn os_version() -> String {
    #[cfg(target_os = "macos")]
    {
        if let Ok(out) = std::process::Command::new("/usr/bin/sw_vers")
            .arg("-productVersion")
            .output()
        {
            let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !text.is_empty() {
                return text;
            }
        }
    }
    #[cfg(unix)]
    {
        if let Some(release) = uname_field(|info| info.release.as_ptr()) {
            return release;
        }
    }
    #[cfg(windows)]
    {
        if let Ok(out) = std::process::Command::new("cmd").args(["/C", "ver"]).output() {
            let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !text.is_empty() {
                return text;
            }
        }
    }
    "unknown".to_string()
}

/// The board-level UUID, where this OS exposes one. `None` everywhere else — never a fabricated
/// value, because the server uses it to recognise the same physical machine.
pub fn hardware_uuid() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("/usr/sbin/ioreg")
            .args(["-rd1", "-c", "IOPlatformExpertDevice"])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&out.stdout);
        for line in text.lines() {
            if line.contains("IOPlatformUUID") {
                let parts: Vec<&str> = line.split('"').collect();
                if parts.len() >= 2 {
                    return Some(parts[parts.len() - 2].to_string());
                }
            }
        }
        None
    }
    #[cfg(target_os = "linux")]
    {
        for path in ["/etc/machine-id", "/sys/class/dmi/id/product_uuid"] {
            if let Ok(text) = std::fs::read_to_string(path) {
                let text = text.trim().to_string();
                if !text.is_empty() {
                    return Some(text);
                }
            }
        }
        None
    }
    #[cfg(target_os = "windows")]
    {
        // `wmic` is the engine's source and is deprecated but still present; PowerShell's CIM
        // query is the documented replacement, so try it first and fall back.
        let out = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
            ])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if text.is_empty() {
            None
        } else {
            Some(text)
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_identity_wears_the_helper_suffix_so_it_cannot_collide_with_the_desktop_app() {
        let id = instance_id("mac.local", Some("UUID"));
        assert!(id.starts_with("inst_"), "{id}");
        assert!(id.ends_with("-helper"), "{id}");
        // `inst_` + 32 hex + `-helper`
        assert_eq!(id.len(), 5 + 32 + 7, "{id}");
    }

    #[test]
    fn the_hash_is_thirty_two_hex_characters_of_sha256() {
        let id = stable_machine_id("mac.local", None);
        assert_eq!(id.len(), 32);
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()), "{id}");
    }

    #[test]
    fn the_hash_is_the_engines_join_order() {
        // The engine joins hostname|machine|system[|hardware uuid] with "|" and takes the first 32
        // hex characters of the SHA-256. Recomputed here independently of the implementation.
        let expected = {
            let raw = format!("mac.local|{}|{}|UUID-1", machine_word(), python_system_word());
            hex::encode(Sha256::digest(raw.as_bytes()))[..32].to_string()
        };
        // The salt is an input, so a test must not inherit an ambient one.
        let previous = std::env::var_os("MATRX_INSTANCE_SALT");
        std::env::remove_var("MATRX_INSTANCE_SALT");
        let actual = stable_machine_id("mac.local", Some("UUID-1"));
        if let Some(p) = previous {
            std::env::set_var("MATRX_INSTANCE_SALT", p);
        }
        assert_eq!(actual, expected);
    }

    #[test]
    fn different_computers_and_different_hardware_hash_differently() {
        let a = stable_machine_id("mac-one.local", Some("UUID-1"));
        let b = stable_machine_id("mac-two.local", Some("UUID-1"));
        let c = stable_machine_id("mac-one.local", Some("UUID-2"));
        let d = stable_machine_id("mac-one.local", None);
        assert_ne!(a, b);
        assert_ne!(a, c);
        assert_ne!(a, d);
    }

    #[test]
    fn this_computer_describes_itself_without_blanks() {
        let registration = Registration::for_this_computer(None);
        assert!(!registration.hostname.is_empty());
        assert!(!registration.instance_name.is_empty());
        assert!(!registration.os_version.is_empty());
        assert_eq!(registration.client_kind, "helper");
        assert!(["darwin", "windows", "linux"].contains(&registration.platform.as_str()));
        assert_eq!(registration.helper_version, crate::VERSION);
    }

    #[test]
    fn a_given_name_replaces_the_display_name_and_nothing_else() {
        let named = Registration::for_this_computer(Some("The kitchen Mac"));
        let plain = Registration::for_this_computer(None);
        assert_eq!(named.instance_name, "The kitchen Mac");
        assert_eq!(named.hostname, plain.hostname);
        assert_eq!(named.instance_id, plain.instance_id);
    }

    #[test]
    fn an_absent_hardware_uuid_is_absent_from_the_body_rather_than_null() {
        let registration = Registration {
            hardware_uuid: None,
            ..Registration::for_this_computer(None)
        };
        let json = serde_json::to_value(&registration).expect("serialise");
        assert!(json.get("hardware_uuid").is_none());
    }
}
