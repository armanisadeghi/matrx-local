"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const hookPath = require.resolve("./build-native-vault-provider-hook.cjs");
const source = fs.readFileSync(hookPath, "utf8");

function runHook(platform) {
  let buildCalls = 0;
  const sandbox = {
    console: { log() {} },
    process: {
      env: { TAURI_ENV_PLATFORM: platform },
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
          buildCalls += 1;
          assert.equal(command, "bash");
          assert.equal(args.length, 1);
          assert.equal(args[0], "scripts/build-native-vault-provider.sh");
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

assert.equal(runHook("darwin"), 1, "Tauri's darwin target must build the provider");
assert.equal(runHook("macos"), 1, "the macos target alias must build the provider");
assert.equal(runHook("windows"), 0, "non-macOS targets must skip the provider");
assert.equal(runHook("linux"), 0, "non-macOS targets must skip the provider");

console.log("native Vault provider hook platform checks passed");
