#!/usr/bin/env bash

# Graceful teardown for the daemon owned by one isolated packaged smoke run.
# This never discovers by process name or uses the installed application's home.

smoke_syncd_pid_alive() {
  local pid="$1"
  case "${OS:-}" in
    windows)
      powershell -NoProfile -Command "if (Get-Process -Id $pid -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1
      ;;
    *) kill -0 "$pid" 2>/dev/null ;;
  esac
}

smoke_shutdown_private_syncd() { # <private-home> [seconds]
  local private_home="$1" budget_s="${2:-25}" live_home dev_home
  local discovery tokens socket private_run_dir metadata pid port world published_socket status waited=0

  private_home="$(cd "$private_home" 2>/dev/null && pwd -P)" || {
    echo "smoke syncd cleanup refused a missing private home" >&2
    return 1
  }
  live_home="$(cd "${HOME}/.matrx" 2>/dev/null && pwd -P || printf '%s' "${HOME}/.matrx")"
  dev_home="$(cd "${HOME}/.matrx-dev" 2>/dev/null && pwd -P || printf '%s' "${HOME}/.matrx-dev")"
  if [ "$private_home" = "$live_home" ] || [ "$private_home" = "$dev_home" ]; then
    echo "smoke syncd cleanup refused a non-private home" >&2
    return 1
  fi
  private_run_dir="$(cd "$private_home/run" 2>/dev/null && pwd -P)" || {
    echo "smoke syncd cleanup found no private run directory" >&2
    return 1
  }
  discovery="$private_home/syncd.json"
  tokens="$private_home/syncd.token"
  socket="$private_run_dir/syncd.sock"
  if [ ! -f "$discovery" ]; then
    if [ -e "$tokens" ] || { [ "${OS:-}" != "windows" ] && [ -e "$socket" ]; }; then
      echo "smoke syncd cleanup found an incomplete private daemon boundary" >&2
      return 1
    fi
    return 0
  fi
  if [ ! -f "$tokens" ]; then
    echo "smoke syncd cleanup found discovery without its private token file" >&2
    return 1
  fi

  metadata="$(node -e '
const fs = require("fs");
const [discovery, privateRunDir] = process.argv.slice(1);
const d = JSON.parse(fs.readFileSync(discovery, "utf8"));
if (d.world !== "dev" || !Number.isInteger(d.pid) || d.pid <= 0 ||
    !Number.isInteger(d.tcp_port) || d.tcp_port < 22260 || d.tcp_port > 22279) process.exit(2);
if (process.platform === "win32") {
  if (!/^\\\\\.\\pipe\\matrx-syncd-dev-[0-9a-f]{12}$/.test(d.socket_path)) process.exit(2);
} else if (d.socket_path !== `${privateRunDir}/syncd.sock`) process.exit(2);
process.stdout.write([d.pid, d.tcp_port, d.world, d.socket_path].join("\t"));
' "$discovery" "$private_run_dir" 2>/dev/null)" || {
    echo "smoke syncd cleanup refused a daemon outside its private dev boundary" >&2
    return 1
  }
  IFS=$'\t' read -r pid port world published_socket <<< "$metadata"
  # Keep the control token inside this short-lived Node process.  A curl
  # header would expose it to `ps` as a process argument while the request runs.
  status="$(node -e '
const fs = require("fs");
const http = require("http");
const [tokens, port] = process.argv.slice(1);
const control = fs.readFileSync(tokens, "utf8").split(/\r?\n/, 1)[0].trim();
if (!control) process.exit(2);
const request = http.request({
  host: "127.0.0.1", port: Number(port), path: "/v1/shutdown", method: "POST",
  headers: {
    Authorization: `Bearer ${control}`,
    "X-Matrx-Client": "smoke",
    Host: `127.0.0.1:${port}`,
  },
  timeout: 5000,
}, (response) => {
  response.resume();
  response.on("end", () => process.stdout.write(String(response.statusCode || "")));
});
request.on("timeout", () => request.destroy());
request.on("error", () => process.exit(1));
request.end();
' "$tokens" "$port" 2>/dev/null)" || status=""
  if [ "$status" != "202" ]; then
    echo "smoke syncd cleanup request was not accepted" >&2
    return 1
  fi

  while [ "$waited" -lt "$budget_s" ]; do
    if ! smoke_syncd_pid_alive "$pid" && [ ! -e "$discovery" ] && [ ! -e "$tokens" ] && \
       { [ "${OS:-}" = "windows" ] || [ ! -e "$socket" ]; }; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "smoke syncd cleanup did not remove its private daemon boundary" >&2
  return 1
}
