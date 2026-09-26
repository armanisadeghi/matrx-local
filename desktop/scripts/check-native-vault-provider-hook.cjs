"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const hookPath = require.resolve("./build-native-vault-provider-hook.cjs");
const source = fs.readFileSync(hookPath, "utf8");

function runHook(platform, environment = {}) {
  const buildCalls = [];
  const sandbox = {
    console: { log() {} },
    process: {
      env: { TAURI_ENV_PLATFORM: platform, ...environment },
      exit(code) {
        const error = new Error(`exit:${code}`);
        error.exitCode = code;
        throw error;
      },
    },
    require(id) {
      assert.equal(id, "node:child_process");
      return {
        execFileSync(command, args) {
          buildCalls.push(args[0]);
          assert.equal(command, "bash");
          assert.equal(args.length, 1);
        },
      };
    },
  };

  try {
    vm.runInNewContext(source, sandbox, { filename: hookPath });
  } catch (error) {
    if (error.exitCode !== 0) throw error;
  }
  return buildCalls;
}

assert.deepEqual(runHook("darwin"), ["scripts/build-native-vault-provider.sh"], "Tauri's darwin target must build the provider");
assert.deepEqual(runHook("macos"), ["scripts/build-native-vault-provider.sh"], "the macos target alias must build the provider");
assert.deepEqual(runHook("darwin", { MATRX_NATIVE_VAULT_PROVIDER: "absent" }), [], "the host-only build must skip the provider");
assert.deepEqual(runHook("darwin", { MATRX_SAFARI_WEB_EXTENSION: "sealed" }), ["scripts/build-native-vault-provider.sh", "scripts/build-safari-web-extension.sh"], "the sealed release must build both nested extensions");
assert.deepEqual(runHook("darwin", { MATRX_SAFARI_WEB_EXTENSION: "absent" }), ["scripts/build-native-vault-provider.sh"], "the explicit Safari-absent mode must preserve ordinary builds");
assert.throws(() => runHook("darwin", { MATRX_SAFARI_WEB_EXTENSION: "unexpected" }), /must be sealed/);
assert.deepEqual(runHook("windows"), [], "non-macOS targets must skip the provider");
assert.deepEqual(runHook("linux"), [], "non-macOS targets must skip the provider");

console.log("native Vault provider hook platform checks passed");
