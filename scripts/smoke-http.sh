#!/usr/bin/env bash

# HTTP probes used by the isolated smoke harness.  The engine's discovery URL
# is always loopback; never let ambient proxy settings turn that into a remote
# request or hide the transport failure behind curl's quiet mode.

smoke_http_get() { # url output-file diagnostic-file timeout-seconds [bearer-token]
  local url="$1" output="$2" diagnostic="$3" timeout="$4" bearer="${5:-}"
  local body_tmp status_tmp curl_rc http_status publish_rc
  body_tmp="$(mktemp "${output}.tmp.XXXXXX")" || return 2
  status_tmp="$(mktemp "${diagnostic}.status.XXXXXX")" || { rm -f "$body_tmp"; return 2; }

  if [ -n "$bearer" ]; then
    curl --noproxy '*' --silent --show-error --max-time "$timeout" \
      -H "Authorization: Bearer $bearer" --output "$body_tmp" --write-out '%{http_code}' \
      "$url" >"$status_tmp" 2>"$diagnostic"
  else
    curl --noproxy '*' --silent --show-error --max-time "$timeout" \
      --output "$body_tmp" --write-out '%{http_code}' "$url" >"$status_tmp" 2>"$diagnostic"
  fi
  curl_rc=$?
  http_status="$(tr -dc '0-9' < "$status_tmp")"
  rm -f "$status_tmp"
  {
    printf 'url=%s\n' "$url"
    printf 'curl_exit=%s\n' "$curl_rc"
    printf 'http_status=%s\n' "${http_status:-unavailable}"
  } >>"$diagnostic"

  if [ "$curl_rc" -eq 0 ] && [[ "$http_status" =~ ^[0-3][0-9][0-9]$ ]]; then
    if mv "$body_tmp" "$output"; then
      return 0
    else
      publish_rc=$?
    fi
    printf 'publish_exit=%s\n' "$publish_rc" >>"$diagnostic"
    rm -f "$body_tmp"
    return 1
  fi
  rm -f "$body_tmp"
  return 1
}

smoke_http_diagnostic() { # diagnostic-file
  local diagnostic="$1"
  [ -s "$diagnostic" ] && tail -40 "$diagnostic" || echo "No curl diagnostic was captured."
}
