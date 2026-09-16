"use strict";

// Tauri executes `beforeBundleCommand` through each platform's native shell.
// Keep its configuration argument-free: cmd.exe passes inline `node -e` source
// with a leading quote, turning a valid script into an unterminated string.
// Tauri reports macOS as Rust's target OS name (`macos`) in some releases
// and Node's platform name (`darwin`) in others. Treat both as the same target;
// skipping on `darwin` leaves the configured .appex path missing and makes the
// bundle fail only after the full release binary has compiled.
const targetPlatform = process.env.TAURI_ENV_PLATFORM;
if (targetPlatform !== "macos" && targetPlatform !== "darwin") {
  process.exit(0);
}

if (process.env.MATRX_NATIVE_VAULT_PROVIDER === "absent") {
  console.log(
    "native Vault provider NOT built: release inputs absent, host bundles without it",
  );
  process.exit(0);
}

require("node:child_process").execFileSync(
  "bash",
  ["scripts/build-native-vault-provider.sh"],
  { stdio: "inherit" },
);
