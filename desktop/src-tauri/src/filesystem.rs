use std::path::{Path, PathBuf};
use std::process::Command;

fn validated_path(raw: &str) -> Result<PathBuf, String> {
    if raw.trim().is_empty() {
        return Err("filesystem path must not be empty".to_string());
    }
    let path = Path::new(raw);
    if !path.is_absolute() {
        return Err("filesystem path must be absolute".to_string());
    }
    std::fs::metadata(path)
        .map_err(|error| format!("filesystem path is unavailable: {error}"))?;
    Ok(path.to_path_buf())
}

#[cfg(target_os = "macos")]
fn launch(path: &Path, reveal: bool) -> std::io::Result<()> {
    let mut command = Command::new("open");
    if reveal {
        command.arg("-R");
    }
    command.arg(path).spawn().map(|_| ())
}

#[cfg(target_os = "windows")]
fn launch(path: &Path, reveal: bool) -> std::io::Result<()> {
    if reveal {
        return Command::new("explorer.exe")
            .arg(format!("/select,{}", path.display()))
            .spawn()
            .map(|_| ());
    }

    // Keep the PowerShell program fixed and pass the user-controlled path only
    // through the child environment. This invokes the registered file handler
    // without exposing an arbitrary shell command surface to the webview.
    Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Start-Process -FilePath $env:MATRX_OPEN_TARGET",
        ])
        .env("MATRX_OPEN_TARGET", path)
        .spawn()
        .map(|_| ())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn launch(path: &Path, reveal: bool) -> std::io::Result<()> {
    let target = if reveal {
        path.parent().unwrap_or(path)
    } else {
        path
    };
    match Command::new("xdg-open").arg(target).spawn() {
        Ok(_) => Ok(()),
        Err(first_error) if first_error.kind() == std::io::ErrorKind::NotFound => {
            Command::new("gio").arg("open").arg(target).spawn().map(|_| ())
        }
        Err(error) => Err(error),
    }
}

/// Open a validated existing path with its registered application, or reveal
/// it in the OS file manager. Unlike plugin-shell this command exposes no
/// arbitrary executable, flags, or URL surface to the renderer.
#[tauri::command]
pub fn open_filesystem_path(path: String, reveal: bool) -> Result<(), String> {
    let path = validated_path(&path)?;
    launch(&path, reveal).map_err(|error| {
        let action = if reveal { "reveal" } else { "open" };
        format!("failed to {action} filesystem path: {error}")
    })
}

#[cfg(test)]
mod tests {
    use super::validated_path;

    #[test]
    fn rejects_relative_and_missing_paths() {
        assert!(validated_path("relative/file.txt").is_err());
        let missing = std::env::temp_dir().join("matrx-native-open-missing-target");
        assert!(validated_path(&missing.to_string_lossy()).is_err());
    }

    #[test]
    fn accepts_existing_absolute_paths() {
        let path = std::env::temp_dir();
        assert!(validated_path(&path.to_string_lossy()).is_ok());
    }
}
