"use strict";

// Tauri executes `beforeBundleCommand` through each platform's native shell.
// Keep its configuration argument-free: cmd.exe passes inline `node -e` source
// with a leading quote, turning a valid script into an unterminated string.
if (process.env.TAURI_ENV_PLATFORM !== "macos") {
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
