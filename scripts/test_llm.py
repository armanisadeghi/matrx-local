"""
Local LLM test script — runs llama-server directly and tests inference.

Usage:
    uv run python scripts/test_llm.py                   # auto-pick first available model
    uv run python scripts/test_llm.py --model qwen3-4b  # pick model by short name
    uv run python scripts/test_llm.py --hf              # skip llama-server, hit HuggingFace API instead
    uv run python scripts/test_llm.py --chat            # interactive chat loop after single test

What this tests:
  1. Can llama-server start at all (dylib loading)
  2. Does the /health endpoint become reachable
  3. Can we get a streamed completion from the OpenAI-compatible /v1/chat/completions
  4. (--hf mode) Can we reach HuggingFace Inference API as a baseline
"""

import argparse
import os
import subprocess
import sys
import time
import threading
import json
import urllib.request
import urllib.error
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY = REPO_ROOT / "desktop/src-tauri/binaries/llama-server-aarch64-apple-darwin"
DYLIB_DIR = REPO_ROOT / "desktop/src-tauri/binaries"
MODELS_DIR = Path.home() / "Library/Application Support/com.aimatrx.desktop/models"

MODEL_ALIASES = {
    "qwen3-4b": "Qwen3-4B-Q4_K_M.gguf",
    "qwen3-8b": "Qwen3-8B-Q4_K_M.gguf",
    "qwen2.5-14b": "qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf",
    "phi4": "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
    "mistral": "Mistral-Small-3.1-24B-Instruct-2503-Q4_K_M.gguf",
}

SERVER_PORT = 11434
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
HEALTH_TIMEOUT_S = 120

# HuggingFace Inference API baseline — free tier, no key needed for public models
HF_API_URL = "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct/v1/chat/completions"


# ── Helpers ───────────────────────────────────────────────────────────────────


def find_model(alias: str | None) -> Path:
    if alias:
        filename = MODEL_ALIASES.get(alias.lower())
        if not filename:
            print(f"[error] Unknown alias '{alias}'. Known: {list(MODEL_ALIASES)}")
            sys.exit(1)
        p = MODELS_DIR / filename
        if not p.exists():
            print(f"[error] Model not found at {p}")
            sys.exit(1)
        return p

    # Auto-pick first available model, prefer smallest
    for alias_name in ["qwen3-4b", "phi4", "qwen3-8b", "qwen2.5-14b", "mistral"]:
        p = MODELS_DIR / MODEL_ALIASES[alias_name]
        if p.exists():
            print(f"[auto] Using {p.name}")
            return p

    print(f"[error] No models found in {MODELS_DIR}")
    print("  Download a model from the Local Models tab first.")
    sys.exit(1)


def http_get(url: str, timeout: int = 5) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"__http_error": e.code, "body": body}
    except Exception:
        return None


def http_post(url: str, payload: dict, timeout: int = 30) -> dict | None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"__http_error": e.code, "body": body}
    except Exception as exc:
        return {"__exception": str(exc)}


def http_post_stream(url: str, payload: dict):
    """POST with stream=True, yield decoded SSE text chunks."""
    payload = {**payload, "stream": True}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# ── llama-server tests ────────────────────────────────────────────────────────


def start_server(model_path: Path) -> subprocess.Popen:
    if not BINARY.exists():
        print(f"[error] llama-server binary not found at {BINARY}")
        print(
            "  Expected: desktop/src-tauri/binaries/llama-server-aarch64-apple-darwin"
        )
        sys.exit(1)

    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = str(DYLIB_DIR)
    env["LD_LIBRARY_PATH"] = str(DYLIB_DIR)

    args = [
        str(BINARY),
        "-m",
        str(model_path),
        "-ngl",
        "99",  # All layers to Metal GPU
        "-c",
        "4096",  # Context window
        "-t",
        "4",  # CPU threads (used for non-GPU ops)
        "--host",
        "127.0.0.1",
        "--port",
        str(SERVER_PORT),
        "--jinja",  # Required for Qwen tool calling
    ]

    print(f"\n[server] Starting llama-server on port {SERVER_PORT}")
    print(f"  Binary : {BINARY.name}")
    print(f"  Model  : {model_path.name}")
    print(f"  Dylibs : {DYLIB_DIR}")
    print(f"  Args   : {' '.join(args[1:])}\n")

    proc = subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream server output to terminal in background
    def _drain():
        for line in proc.stdout:
            print(f"  [llama] {line}", end="", flush=True)

    threading.Thread(target=_drain, daemon=True).start()
    return proc


def wait_for_health(timeout: int = HEALTH_TIMEOUT_S) -> bool:
    print(f"\n[health] Polling {SERVER_URL}/health (up to {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        result = http_get(f"{SERVER_URL}/health", timeout=2)
        if result is not None:
            status = result.get("status", "")
            if status == "ok":
                elapsed = time.time() - start
                print(f"[health] Server ready after {elapsed:.1f}s")
                return True
            elif status == "loading model":
                elapsed = time.time() - start
                print(f"  {elapsed:.0f}s — loading model...", end="\r", flush=True)
            else:
                print(f"  [health] {result}")
        time.sleep(1)
    return False


def test_completion(prompt: str = "Say hello in exactly 5 words.") -> bool:
    payload = {
        "model": "local",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "temperature": 0.7,
        "top_p": 0.8,
    }
    print(f"\n[inference] Prompt: {prompt!r}")
    print("[inference] Response: ", end="", flush=True)

    try:
        for token in http_post_stream(f"{SERVER_URL}/v1/chat/completions", payload):
            print(token, end="", flush=True)
        print()  # newline after response
        return True
    except Exception as exc:
        print(f"\n[error] Inference failed: {exc}")
        return False


def interactive_chat():
    print("\n[chat] Interactive mode — Ctrl+C to quit\n")
    history = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break
        if not user_input:
            continue
        history.append({"role": "user", "content": user_input})
        payload = {
            "model": "local",
            "messages": history,
            "max_tokens": 512,
            "temperature": 0.7,
            "top_p": 0.8,
        }
        print("Assistant: ", end="", flush=True)
        full_response = ""
        try:
            for token in http_post_stream(f"{SERVER_URL}/v1/chat/completions", payload):
                print(token, end="", flush=True)
                full_response += token
            print()
            history.append({"role": "assistant", "content": full_response})
        except Exception as exc:
            print(f"\n[error] {exc}")


# ── HuggingFace API baseline ──────────────────────────────────────────────────


def test_huggingface():
    """
    Hit the HuggingFace Inference API (free tier, no key for most public models).
    This isolates whether llama.cpp is the problem or the models themselves.
    If this works but llama-server doesn't, the issue is in the binary/dylibs.
    If this also fails, the issue is the model architecture or your network.
    """
    print("\n[hf] Testing HuggingFace Inference API (no local binary needed)")
    print(f"[hf] Endpoint: {HF_API_URL}")

    payload = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}],
        "max_tokens": 32,
        "temperature": 0.7,
        "stream": False,
    }

    hf_token = os.environ.get("HF_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
        print("[hf] Using HF_TOKEN from environment")
    else:
        print("[hf] No HF_TOKEN set — using anonymous access (may hit rate limits)")

    data = json.dumps(payload).encode()
    req = urllib.request.Request(HF_API_URL, data=data, method="POST", headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
            print(f"[hf] Response: {content!r}")
            print(
                "[hf] SUCCESS — HuggingFace API works. llama.cpp / dylib is the issue if local fails."
            )
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[hf] HTTP {e.code}: {body[:300]}")
        if e.code == 503:
            print("[hf] Model is loading on HF side — try again in 20s")
        elif e.code == 429:
            print(
                "[hf] Rate limited — set HF_TOKEN env var with a free HuggingFace token"
            )
        return False
    except Exception as exc:
        print(f"[hf] Error: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Local LLM test script")
    parser.add_argument(
        "--model",
        "-m",
        help="Model alias: qwen3-4b, qwen3-8b, qwen2.5-14b, phi4, mistral",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=11434,
        help="Port for llama-server (default 11434)",
    )
    parser.add_argument(
        "--hf", action="store_true", help="Test HuggingFace API instead of local server"
    )
    parser.add_argument(
        "--hf-also",
        action="store_true",
        help="Test HuggingFace API IN ADDITION to local server",
    )
    parser.add_argument(
        "--chat", action="store_true", help="Interactive chat loop after single test"
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Skip starting server — assume it is already running",
    )
    parser.add_argument(
        "--list-models", action="store_true", help="List downloaded models and exit"
    )
    args = parser.parse_args()

    global SERVER_PORT, SERVER_URL
    SERVER_PORT = args.port
    SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

    # ── List models ──
    if args.list_models:
        print(f"Models dir: {MODELS_DIR}")
        if not MODELS_DIR.exists():
            print("  (directory does not exist — no models downloaded yet)")
            return
        gguf_files = sorted(MODELS_DIR.glob("*.gguf"))
        if not gguf_files:
            print("  (no .gguf files found)")
        for f in gguf_files:
            size_gb = f.stat().st_size / 1e9
            print(f"  {f.name}  ({size_gb:.1f} GB)")
        return

    # ── HuggingFace-only mode ──
    if args.hf:
        test_huggingface()
        return

    # ── Local server mode ──
    model_path = find_model(args.model)
    proc = None

    if not args.no_server:
        proc = start_server(model_path)

    try:
        # Wait for server to be healthy
        if not args.no_server:
            if proc.poll() is not None:
                print(
                    f"\n[error] llama-server exited immediately (code {proc.returncode})"
                )
                print(
                    "  This usually means a dylib is missing. Check the [llama] output above."
                )
                sys.exit(1)

        ready = wait_for_health()
        if not ready:
            print(f"\n[error] Server did not become healthy within {HEALTH_TIMEOUT_S}s")
            if proc and proc.poll() is not None:
                print(f"  Process exited with code {proc.returncode}")
            sys.exit(1)

        # Single completion test
        success = test_completion()

        if args.hf_also:
            test_huggingface()

        # Interactive chat
        if args.chat and success:
            interactive_chat()

    finally:
        if proc and proc.poll() is None:
            print("\n[server] Stopping llama-server...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
