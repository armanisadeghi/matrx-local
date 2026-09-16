import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { readFileSync } from "fs";
import { devSyncdBridge } from "./vite-plugins/dev-syncd-bridge";
import { bridgeEnabled } from "./src/lib/harness-bridge-contract";

const host = process.env.TAURI_DEV_HOST;
const pyproject = readFileSync(path.resolve(__dirname, "../pyproject.toml"), "utf-8");
const appVersion = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!appVersion) {
  throw new Error("Unable to read the canonical version from pyproject.toml");
}

export default defineConfig(async ({ command, mode }) => ({
  // `devSyncdBridge` is `apply: "serve"` — it exists on the dev server only, never in a build
  // (MXL-D-091). It is what lets the browser-mode E2E harness reach the dev-world sync daemon.
  plugins: [react(), devSyncdBridge()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    // MXL-D-091. A LITERAL `true`/`false`, never an expression: in a production bundle the
    // harness sign-in path is compiled off and can never become reachable at run time. It also
    // decides the engine port band, so a harness build stays on 22240–22259 and can never scan
    // the installed app's 22140–22159 (Hard Rule 9). Guard: `bridgeEnabled`'s own unit test.
    __MATRX_HARNESS_BRIDGE__: JSON.stringify(bridgeEnabled(command, mode)),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    ...(host
      ? {
          hmr: {
            protocol: "ws",
            host,
            port: 1421,
          },
        }
      : {}),
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
}));
