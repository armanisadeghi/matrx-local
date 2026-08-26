"""Canonical Claude Code executable discovery and account probe.

Desktop GUI processes intentionally have a sparse ``PATH`` on macOS and
Windows. Claude's native installer puts the user-owned launcher in
``~/.local/bin``; relying on ``shutil.which`` alone therefore reports a real,
signed-in installation as missing. Every coding-session consumer uses this
module so history import and the local runtime cannot disagree.

The probe reads only Claude's documented local CLI status. Cloud-bound metadata
continues to use the deterministic account key and masked label. The loopback
desktop may additionally show ``local_display_identity`` so the person using
the machine can tell which of their own accounts Claude reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ACCOUNT_KEY_VERSION = 2
_ACCOUNT_KEY_NAMESPACE = "matrx:coding-session:claude-code-account:v2"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_TERMINATE_GRACE_SECONDS = 1.0
_DIAGNOSTIC_LIMIT = 240
_SUBSCRIPTION_AUTH_OVERRIDES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)

ProbeStatus = Literal[
    "ready",
    "not_found",
    "execution_failed",
    "timeout",
    "signed_out",
    "identity_unavailable",
]


@dataclass(frozen=True)
class AccountSnapshot:
    """Claude account state with separate cloud-safe and local-display fields."""

    available: bool
    account_key: str | None
    fingerprint: str | None
    client_version: str | None
    reason: str | None
    account_label: str | None = None
    executable_path: str | None = None
    probe_status: ProbeStatus | None = None
    diagnostic: str | None = None
    local_display_identity: str | None = None


@dataclass(frozen=True)
class _CommandResult:
    status: Literal["ok", "execution_failed", "timeout"]
    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int | None = None
    diagnostic: str | None = None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_account_key(
    *,
    api_provider: str | None,
    auth_method: str | None,
    org_id: str | None,
    email: str | None,
) -> str:
    """Return the canonical cross-machine Claude account correlation key."""

    material = "\0".join(
        value or "" for value in (api_provider, auth_method, org_id, email)
    )
    return _sha256_text(f"{_ACCOUNT_KEY_NAMESPACE}\0{material}")


def mask_email(email: str) -> str | None:
    """Reduce an email to a display-safe label, or return ``None``."""

    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return None
    domain_name, _, tld = domain.rpartition(".")
    if not domain_name or not tld:
        return None
    local_masked = local[0] + "***" + (local[-1] if len(local) > 1 else "")
    return f"{local_masked}@{domain_name[0]}***.{tld}"[:64]


def account_label(*, email: str | None, org_id: str | None) -> str | None:
    """Return a masked email, then an opaque organization prefix as fallback."""

    if email:
        masked = mask_email(email)
        if masked:
            return masked
    if org_id:
        return f"org:{org_id[:8]}"
    return None


def _default_candidates(
    *, home: Path, environ: Mapping[str, str], platform_name: str
) -> tuple[Path, ...]:
    """Known user and platform install locations, in stable preference order."""

    executable_name = "claude.exe" if platform_name == "win32" else "claude"
    candidates = [
        # Current official native installer.
        home / ".local" / "bin" / executable_name,
        # Retired native installer location still present on existing machines.
        home / ".claude" / "local" / executable_name,
    ]
    if platform_name == "win32":
        local_app_data = environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.extend(
                [
                    Path(local_app_data) / "Programs" / "claude" / executable_name,
                    Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / executable_name,
                ]
            )
    else:
        candidates.extend(
            [
                Path("/opt/homebrew/bin/claude"),
                Path("/usr/local/bin/claude"),
                Path("/usr/bin/claude"),
                Path("/snap/bin/claude"),
            ]
        )
    return tuple(candidates)


def _is_executable_file(path: Path, *, platform_name: str) -> bool:
    try:
        if not path.is_file():
            return False
        return platform_name == "win32" or os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_claude_executable(
    *,
    home: Path | None = None,
    search_path: str | None = None,
    candidates: Iterable[Path] | None = None,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> Path | None:
    """Resolve the user's Claude CLI even when a packaged GUI has a sparse PATH.

    ``home``, ``search_path``, and ``candidates`` are injectable so packaged-PATH
    behavior can be proven without mutating the process environment.
    """

    effective_environ = os.environ if environ is None else environ
    effective_platform = sys.platform if platform_name is None else platform_name
    effective_home = Path.home() if home is None else home
    effective_path = (
        effective_environ.get("PATH") if search_path is None else search_path
    )
    executable_name = "claude.exe" if effective_platform == "win32" else "claude"

    ordered: list[Path] = []
    found = shutil.which(executable_name, path=effective_path)
    if found:
        ordered.append(Path(found))
    ordered.extend(
        _default_candidates(
            home=effective_home,
            environ=effective_environ,
            platform_name=effective_platform,
        )
        if candidates is None
        else candidates
    )

    seen: set[str] = set()
    for raw_candidate in ordered:
        candidate = raw_candidate.expanduser()
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if _is_executable_file(candidate, platform_name=effective_platform):
            return candidate
    return None


def _bounded_diagnostic(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else value
    )
    compact = " ".join(text.split())
    return compact[:_DIAGNOSTIC_LIMIT] or None


async def _terminate_and_reap(process: Any, communicate: asyncio.Task[Any]) -> None:
    """Stop and reap a timed-out/cancelled direct child without leaving a zombie."""

    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(communicate), _TERMINATE_GRACE_SECONDS)
        return
    except asyncio.TimeoutError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
    await communicate


async def _run_command(
    executable: Path,
    *arguments: str,
    timeout: float,
    process_factory: Callable[..., Awaitable[Any]],
    env: Mapping[str, str] | None = None,
) -> _CommandResult:
    try:
        process = await process_factory(
            str(executable),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=None if env is None else dict(env),
        )
    except OSError as exc:
        return _CommandResult(
            "execution_failed",
            diagnostic=_bounded_diagnostic(f"{type(exc).__name__}: {exc}"),
        )

    communicate = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate), timeout=timeout
        )
    except asyncio.TimeoutError:
        await _terminate_and_reap(process, communicate)
        return _CommandResult("timeout", diagnostic=f"timed out after {timeout:g}s")
    except asyncio.CancelledError:
        await _terminate_and_reap(process, communicate)
        raise

    if process.returncode != 0:
        diagnostic = _bounded_diagnostic(stderr) or f"exit code {process.returncode}"
        return _CommandResult(
            "execution_failed",
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            diagnostic=diagnostic,
        )
    return _CommandResult(
        "ok",
        stdout=stdout,
        stderr=stderr,
        returncode=process.returncode,
    )


_DESKTOP_OAUTH_RECORD = Path.home() / ".claude.json"
_MAX_OAUTH_RECORD_BYTES = 33_554_432


def _desktop_oauth_identity(
    record_path: Path | None = None,
) -> tuple[str | None, str | None] | None:
    """(email, org_id) from the desktop app's OAuth record, or None.

    ``~/.claude.json`` `oauthAccount` is written by Claude's own client on
    sign-in and names the active subscription account. Returns None when the
    record is missing/unreadable or carries neither identity field.
    """
    path = record_path or _DESKTOP_OAUTH_RECORD
    try:
        if path.stat().st_size > _MAX_OAUTH_RECORD_BYTES:
            return None
        record = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    account = record.get("oauthAccount") if isinstance(record, dict) else None
    if not isinstance(account, dict):
        return None
    raw_email = account.get("emailAddress")
    email = (
        raw_email.strip().lower()
        if isinstance(raw_email, str) and raw_email.strip()
        else None
    )
    raw_org = account.get("organizationUuid")
    org_id = raw_org if isinstance(raw_org, str) and raw_org else None
    if email is None and org_id is None:
        return None
    return email, org_id


def _safe_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


async def read_account_snapshot(
    *,
    executable: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    resolver: Callable[[], Path | None] = resolve_claude_executable,
    process_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    oauth_record_path: Path | None = None,
) -> AccountSnapshot:
    """Probe Claude sign-in and reduce it to the canonical privacy-safe state."""

    resolved = executable if executable is not None else resolver()
    if resolved is None:
        return AccountSnapshot(
            False,
            None,
            None,
            None,
            "claude_not_installed",
            probe_status="not_found",
        )
    executable_path = str(resolved)
    # The coding-session identity is the user's Claude subscription account.
    # Developer shells may carry an Anthropic key (``uv run`` also loads the
    # root developer .env); Claude otherwise reports that key source with no
    # stable email/org and falsely marks the signed-in subscription unknown.
    subscription_env = dict(os.environ)
    for name in _SUBSCRIPTION_AUTH_OVERRIDES:
        subscription_env[name] = ""

    auth = await _run_command(
        resolved,
        "auth",
        "status",
        "--json",
        timeout=timeout,
        process_factory=process_factory,
        env=subscription_env,
    )
    auth_object = _safe_object(auth.stdout)
    # Claude emits structured logged-out state even when the command exits
    # non-zero. Inspect that state before classifying an execution failure —
    # but `auth status` is NOT the only identity source: the desktop app's
    # OAuth record (~/.claude.json `oauthAccount`) carries the same email and
    # organization the CLI reports when signed in, and stays readable when
    # `auth status` misreports (observed 2026-08-26 on Claude 2.1.228: status
    # says logged out while runs execute fine — and transiently mid-run while
    # the CLI refreshes credentials). The derivation constants below are the
    # CLI's own values ("firstParty"/"claude.ai"), verified byte-identical
    # against historical provider_account_key rows, so identity never forks.
    if auth_object is not None and auth_object.get("loggedIn") is not True:
        fallback = _desktop_oauth_identity(oauth_record_path)
        if fallback is not None:
            email, org_id = fallback
            account_key = derive_account_key(
                api_provider="firstParty",
                auth_method="claude.ai",
                org_id=org_id,
                email=email,
            )
            return AccountSnapshot(
                True,
                account_key,
                account_key[:12],
                None,
                None,
                account_label=account_label(email=email, org_id=org_id),
                executable_path=executable_path,
                probe_status="ready",
                diagnostic=(
                    "identity from the desktop OAuth record; `claude auth "
                    "status` reported signed out"
                ),
                local_display_identity=email or org_id,
            )
        return AccountSnapshot(
            False,
            None,
            None,
            None,
            "claude_not_signed_in",
            executable_path=executable_path,
            probe_status="signed_out",
        )
    if auth.status == "timeout":
        return AccountSnapshot(
            False,
            None,
            None,
            None,
            "claude_status_timeout",
            executable_path=executable_path,
            probe_status="timeout",
            diagnostic=auth.diagnostic,
        )
    if auth.status == "execution_failed" or auth_object is None:
        diagnostic = auth.diagnostic or "Claude returned invalid auth status JSON"
        return AccountSnapshot(
            False,
            None,
            None,
            None,
            "claude_status_execution_failed",
            executable_path=executable_path,
            probe_status="execution_failed",
            diagnostic=diagnostic,
        )

    # Version is useful diagnostics, but failure to print it must not turn a
    # verified login into a false account failure.
    version = await _run_command(
        resolved,
        "--version",
        timeout=timeout,
        process_factory=process_factory,
        env=subscription_env,
    )
    client_version = (
        _bounded_diagnostic(version.stdout)[:64]
        if version.status == "ok" and _bounded_diagnostic(version.stdout)
        else None
    )

    api_provider = auth_object.get("apiProvider")
    auth_method = auth_object.get("authMethod")
    org_id = auth_object.get("orgId")
    email_value = auth_object.get("email")
    email = (
        email_value.strip().lower()
        if isinstance(email_value, str) and email_value.strip()
        else None
    )
    normalized_org_id = org_id if isinstance(org_id, str) and org_id else None
    if normalized_org_id is None and email is None:
        return AccountSnapshot(
            False,
            None,
            None,
            client_version,
            "claude_account_identity_unavailable",
            executable_path=executable_path,
            probe_status="identity_unavailable",
            diagnostic="Claude is signed in but returned no stable organization or email identity",
        )

    account_key = derive_account_key(
        api_provider=api_provider if isinstance(api_provider, str) else None,
        auth_method=auth_method if isinstance(auth_method, str) else None,
        org_id=normalized_org_id,
        email=email,
    )
    version_diagnostic = version.diagnostic if version.status != "ok" else None
    return AccountSnapshot(
        True,
        account_key,
        account_key[:12],
        client_version,
        None,
        account_label=account_label(email=email, org_id=normalized_org_id),
        executable_path=executable_path,
        probe_status="ready",
        diagnostic=version_diagnostic,
        local_display_identity=email or normalized_org_id,
    )


__all__ = [
    "ACCOUNT_KEY_VERSION",
    "AccountSnapshot",
    "account_label",
    "derive_account_key",
    "mask_email",
    "read_account_snapshot",
    "resolve_claude_executable",
]
