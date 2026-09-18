//! `matrx-egress install` / `uninstall` — start with the user's session, or stop doing so.
//!
//! | OS | What is written |
//! |---|---|
//! | macOS | `~/Library/LaunchAgents/com.aimatrx.home-connection.plist` |
//! | Windows | the `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `AI Matrx Home Connection` |
//! | Linux | `~/.config/systemd/user/matrx-egress.service` |
//!
//! Always per-USER, never system-wide: the helper lends *this person's* internet connection and
//! holds *this person's* keychain item. A LaunchDaemon or a machine-wide service would run as
//! root, with no keychain and no browser to pair in.
//!
//! **Windows and Linux are written against their documentation and have not been run** — this
//! machine is a Mac. They are named as unproven in `README.md` rather than implied to work.

use std::io;
use std::path::PathBuf;

/// The macOS launch-agent label and the Windows Run value, one name in one place.
pub const SERVICE_LABEL: &str = "com.aimatrx.home-connection";
/// What the Windows registry value is called, and what Task Manager shows. Only the Windows
/// branches read it; it is declared unconditionally so the one name lives in one place and the
/// test below can assert it on every platform.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub const DISPLAY_NAME: &str = "AI Matrx Home Connection";

/// What `install` / `uninstall` did, in words the CLI prints.
pub struct Outcome {
    /// One sentence saying what happened.
    pub message: String,
    /// Where the thing that was written lives, when there is a path.
    pub path: Option<PathBuf>,
}

/// Start the helper when this person signs in.
pub fn install(world: crate::world::World) -> io::Result<Outcome> {
    let exe = std::env::current_exe()?;
    install_for(world, &exe)
}

/// Stop starting it.
pub fn uninstall(world: crate::world::World) -> io::Result<Outcome> {
    #[cfg(target_os = "macos")]
    {
        let path = launch_agent_path()?;
        let _ = std::process::Command::new("/bin/launchctl")
            .args(["unload", "-w"])
            .arg(&path)
            .output();
        crate::paths::remove_quietly(&path)?;
        let _ = world;
        Ok(Outcome {
            message: "The AI Matrx Home Connection will no longer start when you sign in."
                .to_string(),
            path: Some(path),
        })
    }
    #[cfg(target_os = "windows")]
    {
        let _ = world;
        let output = std::process::Command::new("reg")
            .args([
                "delete",
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                "/v",
                DISPLAY_NAME,
                "/f",
            ])
            .output()?;
        // A value that was not there is not a failure.
        let _ = output;
        Ok(Outcome {
            message: "The AI Matrx Home Connection will no longer start when you sign in."
                .to_string(),
            path: None,
        })
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = world;
        let path = systemd_unit_path()?;
        let _ = std::process::Command::new("systemctl")
            .args(["--user", "disable", "--now", "matrx-egress.service"])
            .output();
        crate::paths::remove_quietly(&path)?;
        Ok(Outcome {
            message: "The AI Matrx Home Connection will no longer start when you sign in."
                .to_string(),
            path: Some(path),
        })
    }
}

/// The installable form, with the executable named explicitly — what the tests drive.
pub fn install_for(world: crate::world::World, exe: &std::path::Path) -> io::Result<Outcome> {
    #[cfg(target_os = "macos")]
    {
        let path = launch_agent_path()?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&path, launch_agent_plist(world, exe))?;
        // `load -w` is what a user session understands; a failure here is not fatal — the file is
        // written and the next sign-in honours it — so it is reported, never swallowed.
        let loaded = std::process::Command::new("/bin/launchctl")
            .args(["load", "-w"])
            .arg(&path)
            .output();
        let message = match loaded {
            Ok(output) if output.status.success() => {
                "The AI Matrx Home Connection will now start when you sign in, and it is running \
                 now."
                    .to_string()
            }
            _ => "The AI Matrx Home Connection will start the next time you sign in.".to_string(),
        };
        Ok(Outcome {
            message,
            path: Some(path),
        })
    }
    #[cfg(target_os = "windows")]
    {
        let _ = world;
        let command = format!("\"{}\"", exe.display());
        let output = std::process::Command::new("reg")
            .args([
                "add",
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                "/v",
                DISPLAY_NAME,
                "/t",
                "REG_SZ",
                "/d",
                &command,
                "/f",
            ])
            .output()?;
        if !output.status.success() {
            return Err(io::Error::other(format!(
                "Windows would not record the AI Matrx Home Connection as a start-up item: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(Outcome {
            message: "The AI Matrx Home Connection will now start when you sign in.".to_string(),
            path: None,
        })
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let path = systemd_unit_path()?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&path, systemd_unit(world, exe))?;
        let _ = std::process::Command::new("systemctl")
            .args(["--user", "daemon-reload"])
            .output();
        let enabled = std::process::Command::new("systemctl")
            .args(["--user", "enable", "--now", "matrx-egress.service"])
            .output();
        let message = match enabled {
            Ok(output) if output.status.success() => {
                "The AI Matrx Home Connection will now start when you sign in, and it is running \
                 now."
                    .to_string()
            }
            _ => "The AI Matrx Home Connection will start the next time you sign in.".to_string(),
        };
        Ok(Outcome {
            message,
            path: Some(path),
        })
    }
}

/// `~/Library/LaunchAgents/com.aimatrx.home-connection.plist`.
#[cfg(target_os = "macos")]
pub fn launch_agent_path() -> io::Result<PathBuf> {
    let home = std::env::var_os("HOME").ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "HOME is not set, so there is no user Library folder to install into",
        )
    })?;
    Ok(PathBuf::from(home)
        .join("Library")
        .join("LaunchAgents")
        .join(format!("{SERVICE_LABEL}.plist")))
}

/// The launch agent. `RunAtLoad` + `KeepAlive` so the helper comes back if it ever stops, and
/// `ProcessType Background` so macOS does not throttle a long-lived socket.
#[cfg(target_os = "macos")]
pub fn launch_agent_plist(world: crate::world::World, exe: &std::path::Path) -> String {
    let world_args = match world {
        crate::world::World::Live => String::new(),
        crate::world::World::Dev => {
            "        <string>--world</string>\n        <string>dev</string>\n".to_string()
        }
    };
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
{world_args}    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
"#,
        exe = exe.display()
    )
}

/// `~/.config/systemd/user/matrx-egress.service`.
#[cfg(all(unix, not(target_os = "macos")))]
pub fn systemd_unit_path() -> io::Result<PathBuf> {
    let base = match std::env::var_os("XDG_CONFIG_HOME") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => {
            let home = std::env::var_os("HOME").ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "HOME is not set, so there is no user configuration folder to install into",
                )
            })?;
            PathBuf::from(home).join(".config")
        }
    };
    Ok(base.join("systemd").join("user").join("matrx-egress.service"))
}

/// The systemd user unit. No tray on Linux, so it runs headless and says so on stdout, which the
/// journal keeps.
#[cfg(all(unix, not(target_os = "macos")))]
pub fn systemd_unit(world: crate::world::World, exe: &std::path::Path) -> String {
    let world_args = match world {
        crate::world::World::Live => String::new(),
        crate::world::World::Dev => " --world dev".to_string(),
    };
    format!(
        "[Unit]\n\
         Description=AI Matrx Home Connection\n\
         After=network-online.target\n\
         \n\
         [Service]\n\
         ExecStart={exe}{world_args}\n\
         Restart=always\n\
         RestartSec=5\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        exe = exe.display()
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::World;

    #[cfg(target_os = "macos")]
    #[test]
    fn the_launch_agent_names_the_binary_and_carries_the_contracts_label() {
        let exe = std::path::Path::new("/Applications/AI Matrx Home Connection.app/matrx-egress");
        let plist = launch_agent_plist(World::Live, exe);
        assert!(plist.contains("com.aimatrx.home-connection"), "{plist}");
        assert!(plist.contains(&exe.display().to_string()), "{plist}");
        assert!(plist.contains("<key>RunAtLoad</key>"), "{plist}");
        // A live install must not carry a dev switch.
        assert!(!plist.contains("--world"), "{plist}");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn a_dev_install_says_which_world_it_is_so_it_cannot_take_the_installed_apps_place() {
        let plist = launch_agent_plist(World::Dev, std::path::Path::new("/tmp/matrx-egress"));
        assert!(plist.contains("<string>--world</string>"), "{plist}");
        assert!(plist.contains("<string>dev</string>"), "{plist}");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn the_launch_agent_is_the_users_own_and_never_a_system_daemon() {
        let path = launch_agent_path().expect("path");
        let text = path.display().to_string();
        assert!(text.contains("/Library/LaunchAgents/"), "{text}");
        assert!(!text.starts_with("/Library/"), "{text}");
        assert!(!text.contains("LaunchDaemons"), "{text}");
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    #[test]
    fn the_systemd_unit_restarts_and_is_a_user_unit() {
        let unit = systemd_unit(World::Live, std::path::Path::new("/usr/bin/matrx-egress"));
        assert!(unit.contains("ExecStart=/usr/bin/matrx-egress"), "{unit}");
        assert!(unit.contains("Restart=always"), "{unit}");
        assert!(unit.contains("WantedBy=default.target"), "{unit}");
        let path = systemd_unit_path().expect("path");
        assert!(path.display().to_string().contains("systemd/user"));
    }

    #[test]
    fn the_one_name_is_used_everywhere() {
        assert_eq!(SERVICE_LABEL, "com.aimatrx.home-connection");
        assert_eq!(DISPLAY_NAME, "AI Matrx Home Connection");
    }
}
