//! `matrx-syncd` — the AI Matrx folder-sync daemon.
//!
//! **Scaffold only (spike FS-C1).** It starts nothing, watches nothing, and
//! talks to nothing. It answers `--version` and `--help` and exits, which is
//! exactly what the packaging spike needs to prove: that the binary builds for
//! every release target, bundles as a Tauri `externalBin` sidecar, survives
//! macOS signing, and is never spawned by the app.
//!
//! Spec: `common-docs/projects/folder-sync/SCOPE.md`.

const NAME: &str = env!("CARGO_PKG_NAME");
const VERSION: &str = env!("CARGO_PKG_VERSION");

fn help() -> String {
    format!(
        "{NAME} {VERSION}\n\
         AI Matrx folder-sync daemon.\n\n\
         USAGE:\n    \
             {NAME} [OPTIONS]\n\n\
         OPTIONS:\n    \
             -V, --version    Print the version and exit\n    \
             -h, --help       Print this help and exit\n\n\
         STATUS:\n    \
             Scaffold (spike FS-C1). No sync engine yet; nothing is started,\n    \
             nothing is watched, and the desktop app does not spawn this binary.\n"
    )
}

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();

    for arg in &args {
        match arg.as_str() {
            "-V" | "--version" => {
                println!("{NAME} {VERSION} (matrx-sync {})", matrx_sync::VERSION);
                return std::process::ExitCode::SUCCESS;
            }
            "-h" | "--help" => {
                print!("{}", help());
                return std::process::ExitCode::SUCCESS;
            }
            other => {
                eprintln!("{NAME}: unrecognised argument '{other}'");
                eprint!("{}", help());
                return std::process::ExitCode::from(2);
            }
        }
    }

    // No arguments: say so loudly rather than pretending to be a daemon.
    // "Nothing fails silently" — a stand-in announces itself.
    print!("{}", help());
    std::process::ExitCode::SUCCESS
}
