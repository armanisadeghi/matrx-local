# `crates/` — the Rust workspace crates

Added by the folder-sync **pre-G1 spike FS-C1**
(`common-docs/projects/folder-sync/REGISTER.md`, `SCOPE.md` §4, `DECISIONS.md` D1).

| Crate | What it is |
|---|---|
| `matrx-sync` | Library. The folder-sync engine. **Scaffold only** — `VERSION` plus an empty `journal` module whose doc comment points at the spec. No sync logic. |
| `matrx-syncd` | `[[bin]] matrx-syncd`. The daemon. **Scaffold only** — answers `--version` / `--help` and exits 0. Bundled as a Tauri `externalBin` sidecar. **The app does not spawn it.** |

The third workspace member is `desktop/src-tauri` (the Tauri app). The app and the
daemon therefore compile the *same* `matrx-sync` source — one crate, two
packagings, zero duplication (D1).

`ARCHITECTURE.md` (`docs/official/`) does not yet mention this workspace; it is due
an update and `docs/official/**` may not be edited without Arman's approval.

---

## The target-directory decision

**Decision: the target directory stays at `desktop/src-tauri/target`**, pinned by a
new repo-root `.cargo/config.toml`:

```toml
[build]
target-dir = "desktop/src-tauri/target"
```

**Why.** A Cargo workspace defaults its target directory to the workspace root, so
adding `Cargo.toml` at the repo root would silently move every artifact to
`<repo>/target`. Four things hardcode the old path, and all four fail *silently or
late*:

- `.github/workflows/release.yml` — the macOS artifact-signing verification step
  (`BUNDLE_DIR="desktop/src-tauri/target/$TRIPLE/release/bundle/macos"`). This runs
  **after** `tauri-action` has already uploaded assets to the draft release, so a
  wrong path means a release that ships unverified.
- `scripts/smoke.sh` — the isolated-port marker and all three `find_app_binary`
  branches (macOS `.app` discovery, Windows `.exe`, Linux executable).
- 24 GB of existing incremental build cache on a developer machine, which a move
  discards and which costs a full whisper.cpp / Tauri rebuild to regenerate.

Keeping the path **touched zero pipeline files** for the relocation. That is the
option with the fewest consumers to repoint, as the brief required.

**Alternatives considered and rejected**

1. *Workspace-root `target/`, repoint every consumer.* Six hardcoded path sites
   across two files, plus any developer muscle memory and any future script. Buys
   nothing: nobody reads artifacts by their "natural" workspace location here.
2. *Tauri's `build.targetDir` in `tauri.conf.json`.* Only affects builds driven by
   the Tauri CLI. A plain `cargo build -p matrx-syncd` — which is exactly what
   `scripts/build-syncd.sh` and CI do — would land somewhere else, giving two
   target directories instead of one.
3. *`CARGO_TARGET_DIR` env var.* Would have to be exported by every script, every
   CI step, every shell. A checked-in `.cargo/config.toml` needs no cooperation
   from the caller and is honoured by `cargo metadata`, which is how
   `scripts/build-syncd.sh` finds the artifact rather than mirroring this choice.

**What the decision does cost.** Cargo places the lockfile at the *workspace root*
unconditionally, and there is no config for it, so `desktop/src-tauri/Cargo.lock`
moved to `Cargo.lock`. That repoint was unavoidable under any target-dir choice and
was applied to `scripts/release.sh` (the `VERSION_FILES` list, the refresh message,
the `git add` list), `scripts/check-version-sync.sh`, and
`.github/workflows/ci.yml`'s `paths-ignore`.

**One more consumer the workspace itself broke, and how it was fixed.** On Windows
and Linux, `scripts/smoke.sh` finds the packaged app by globbing
`target/release` for `*.exe` / `*matrx*`. `matrx-syncd` is now a workspace sibling
landing in that same directory, and `head -1` would have picked it
nondeterministically — the exact failure mode the file's own comment records for
bundled `uv` ("adding bundled uv made the harness launch uv and report a phantom
app crash"). Both branches now exclude `matrx-syncd`. macOS is unaffected: it reads
`CFBundleExecutable` from the bundle's `Info.plist`.

---

## Sidecar packaging

`matrx-syncd` is declared in `bundle.externalBin` as `sidecar/matrx-syncd`:

- `desktop/src-tauri/tauri.conf.json` — the base array (inherited by **Windows and
  Linux**, whose overlays declare no `externalBin` at all, so nothing is replaced
  there).
- `desktop/src-tauri/tauri.macos.conf.json` — re-listed, because **Tauri overlay
  arrays are replaced wholesale, not merged** (`docs/official/build-lessons.md`).
  The macOS overlay deliberately omits `sidecar/matrx-engine` (the engine ships as
  a Helper-app bundle instead), so forgetting to re-list `matrx-syncd` here would
  have dropped it from every macOS build.

`scripts/build-syncd.sh` builds it and copies it to
`desktop/src-tauri/sidecar/matrx-syncd-<target-triple>[.exe]` — the
`-$TARGET_TRIPLE` naming rule Tauri requires. The file is **not** committed
(`desktop/.gitignore` ignores `src-tauri/sidecar/`). CI builds it in a new
`Build sync daemon sidecar (matrx-syncd)` step in `.github/workflows/release.yml`,
placed immediately before `tauri-action`, driven by a new `syncd_target` matrix
field (the two macOS legs cross-compile per triple; Linux and Windows build native).

**Signing.** The daemon is compiled by cargo on the runner, exactly like the app
binary, so Tauri's bundler signs it with the bundle. No separate re-sign step — that
requirement (`build-lessons.md`, Hard Rule 7) applies to *pre-built downloaded*
binaries such as `llama-server` and its dylibs, which arrive ad-hoc-signed.
`cloudflared` is the precedent for the unsigned-download case; `matrx-syncd` is not
in that class. It links no companion dylibs, so it needs no `bundle.resources`
glob and no rpath/`LD_LIBRARY_PATH` injection.

**Windows/Linux path correctness (reasoned, not run — no such machine here).**
`build-syncd.sh` appends `.exe` for `*windows*` triples, so the sidecar is
`matrx-syncd-x86_64-pc-windows-msvc.exe` and Tauri strips the triple to
`matrx-syncd.exe` next to the app binary. On Linux the file lands beside the app
binary in the `.deb`'s `/usr/lib/<app>/`. Both are the same mechanism that already
ships `cloudflared` and `uv`. The script uses only `uname`, `cargo`, `cp`, `chmod`
and `python3`, all present on the GitHub runners and under Git Bash.

**The name is safe against the engine sweeps — checked, not assumed.** The app's
orphan sweep matches the full command line against
`"Matrx Engine|matrx-engine|aimatrx-engine"` (`desktop/src-tauri/src/lib.rs`), and
Windows uses an explicit `taskkill /IM` list of four engine image names in both
`lib.rs` and `nsis/installer-hooks.nsh`. `matrx-syncd` contains none of those
substrings and is on none of those lists, so a running daemon is not murdered by
an app launch, an app quit, or an NSIS upgrade. Verified:

```
$ echo "matrx-syncd" | grep -E "Matrx Engine|matrx-engine|aimatrx-engine"
(no match)
```

---

## Spike proof (2026-09-13)

Every command below was run on this Mac (macOS 15, aarch64, cargo 1.93.1,
rustc 1.93.1) at commit `d98a0727b`. Nothing here is inferred.

| # | Command | Result |
|---|---|---|
| 1 | `cargo metadata --format-version 1 --no-deps` | `target_directory: /Users/armanisadeghi/code/matrx-local/desktop/src-tauri/target`; members `matrx-sync`, `matrx-syncd`, `aimatrx-desktop` — the `.cargo/config.toml` pin and the three-member workspace both confirmed |
| 2 | `cargo build --workspace` | `Finished dev profile ... in 1m 22s`; 8 pre-existing `aimatrx-desktop` dead-code warnings, no new ones, no errors |
| 3 | `cargo build -p matrx-syncd --release` | `Finished release profile ... in 3.31s` |
| 4 | `./desktop/src-tauri/target/release/matrx-syncd --version` | `matrx-syncd 0.1.0 (matrx-sync 0.1.0)`, exit `0` |
| 5 | `./desktop/src-tauri/target/release/matrx-syncd --help` | usage text, exit `0` |
| 6 | `./scripts/build-syncd.sh` | `OK: .../sidecar/matrx-syncd-aarch64-apple-darwin` → `matrx-syncd 0.1.0 (matrx-sync 0.1.0)` |
| 7 | `cd desktop && pnpm typecheck` | clean (`tsc --noEmit -p tsconfig.json`, no output) |
| 8 | `cd desktop && pnpm tauri build --debug --bundles app` | `Finished 1 bundle at .../target/debug/bundle/macos/AI Matrx.app`. The command then exits 1 on `TAURI_SIGNING_PRIVATE_KEY` for the updater tarball — a pre-existing local-only condition, after the `.app` is complete |
| 9 | `ls "AI Matrx.app/Contents/MacOS/"` | `aimatrx-desktop`, `cloudflared`, `llama-server`, **`matrx-syncd`** (427,504 bytes), `uv` — the sidecar lands where `externalBin` puts it |
| 10 | `"AI Matrx.app/Contents/MacOS/matrx-syncd" --version` | `matrx-syncd 0.1.0 (matrx-sync 0.1.0)` — runs from inside the bundle |
| 11 | `ls "AI Matrx.app/Contents/Frameworks/Matrx Engine.app/Contents/MacOS/"` | `Matrx Engine` (352,916,432 bytes) — the engine Helper-app is still exactly where `macos_helper_engine_path()` computes it (`build-lessons.md` "Spawning a Helper-app from Rust"). `Contents/PlugIns/AI Matrx Vault Provider.appex` also still present |
| 12 | `grep -rn "syncd" desktop/src-tauri/src/ desktop/src/` | no hits — the app contains no code that spawns, probes, or references the daemon |
| 13 | `./scripts/check-version-sync.sh` | `Version sync OK: 1.4.107` — reading the moved root `Cargo.lock` |
| 14 | `bash -n` on `smoke.sh`, `release.sh`, `build-syncd.sh`; `json.load` on both tauri configs; `yaml.safe_load` on `release.yml` and `ci.yml` | all parse |
| 15 | `./scripts/smoke.sh packaged` | see below |

<!-- SMOKE_RESULT -->

### What could NOT be proven here, and remains for CI

Signing and notarization need Apple secrets that exist only in the Release
workflow. Verified **by reading** `.github/workflows/release.yml`, not by running:

- The new `Build sync daemon sidecar (matrx-syncd)` step sits after
  `Install Rust stable` and before `Build and release`, so the
  `-$TARGET_TRIPLE` file exists when `tauri-action` bundles. Its `syncd_target`
  comes from the matrix; the two macOS legs pass the same triple their
  `--target` uses.
- The `Verify macOS artifact signing` step's `BUNDLE_DIR` still resolves, because
  the target directory did not move.

Still to be proven on the next CI release (run by the deploy agent — never by this
spike):

1. `matrx-syncd` cross-compiles for `x86_64-apple-darwin` on the macOS runner and
   for the Windows/Linux runners' native triples.
2. The Developer ID signature Tauri applies to the bundled `matrx-syncd` passes
   notarization and Gatekeeper on an end-user machine.
3. The `.deb` and the NSIS/MSI installers actually contain the daemon next to the
   app binary.
