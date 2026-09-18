"""ONE usage shape for every coding-agent provider.

The Usage tab shows one table with the provider as a facet — "same shapes,
same table, not four different screens" (Arman, 2026-09-17). So every local
source is normalized here into :class:`UsageReport`, and the tab never learns
a provider's native record layout:

* **Claude Code** — per-turn usage from its transcripts, aggregated by the
  persisted index (:mod:`claude_usage`, :mod:`claude_index_store`); limits
  from the utilization cache Claude Code itself keeps in ``.claude.json``;
  cost from the platform's model price catalog cached in ``ai_models``.
* **Codex** — the existing rollout collector and allowance reader
  (:mod:`app.services.codex_usage`), re-keyed into the same rows.
* **Cursor** — its state database records suggested/accepted LINES per day
  and the plan tier; it records no tokens locally (30,000 newest chat bubbles
  all carry ``tokenCount`` 0/0, measured 2026-09-17), and the report says so.
* **VS Code** — nothing local. The report says exactly that, never a blank.

What a provider does not expose is a STATE in the report (``metrics``,
``source.kind == "none"``, ``cost.reason``, ``limits.reason``) — never an
empty table pretending to be zero.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.coding_sessions.claude_usage import USAGE_FIELDS

Provider = Literal["claude_code", "codex", "cursor", "vscode"]
PROVIDERS: tuple[Provider, ...] = ("claude_code", "codex", "cursor", "vscode")

# Anthropic bills a 5-minute cache write at 1.25x the input rate; the catalog
# carries input/output/cached-read only, so writes are priced from input.
CACHE_WRITE_MULTIPLIER = 1.25

# The main/sub-agent split every row carries beside its own totals.
SPLIT_FIELDS = (
    "main_requests",
    "main_total_tokens",
    "subagent_requests",
    "subagent_total_tokens",
)


class UsageRow(BaseModel):
    key: str
    label: str
    model: str | None = None
    project: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    # The main/sub-agent split of the two headline measures. "Sub-agent turns
    # ARE the session's spend… show the split (main vs sub-agents) as a column
    # so nobody mistakes it" (Arman, 2026-09-18). ``main_* + subagent_*`` is
    # always the row's own total; a provider that runs no sub-agents (or does
    # not record them — see ``metrics.subagents``) leaves them at zero and the
    # tab shows no column rather than a zero pretending to be a measurement.
    main_requests: int = 0
    main_total_tokens: int = 0
    subagent_requests: int = 0
    subagent_total_tokens: int = 0
    # In the report's cost unit; None when any part of the row is unpriced.
    cost: float | None = None
    # Provider-specific measures that are not tokens (Cursor's lines).
    extra: dict[str, int] = Field(default_factory=dict)


class UsageSource(BaseModel):
    kind: Literal["local_transcripts", "local_rollouts", "local_state_db", "none"]
    description: str
    complete: bool
    can_resume: bool = False
    pending_sessions: int | None = None
    updated_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class UsageMetrics(BaseModel):
    tokens: bool
    requests: bool
    cost: bool
    lines: bool
    # Whether this provider records sub-agent turns separately on this Mac.
    # False means "not recorded", never "none happened".
    subagents: bool = False


class UsageCost(BaseModel):
    available: bool
    unit: Literal["usd", "credits"] | None
    label: str
    reason: str | None = None
    unpriced_models: list[str] = Field(default_factory=list)


class UsageLimitWindow(BaseModel):
    label: str
    used_percent: float | None = None
    remaining_percent: float | None = None
    window_minutes: float | None = None
    resets_at: str | None = None


class UsageLimits(BaseModel):
    status: Literal["available", "unavailable"]
    observed_at: str | None = None
    reason: str | None = None
    plan: str | None = None
    windows: list[UsageLimitWindow] = Field(default_factory=list)


class UsageRange(BaseModel):
    start: str
    end: str


class UsageReport(BaseModel):
    provider: Provider
    generated_at: str
    range: UsageRange
    tz_offset_minutes: int
    source: UsageSource
    metrics: UsageMetrics
    totals: UsageRow
    by_day: list[UsageRow]
    by_model: list[UsageRow]
    by_session: list[UsageRow]
    by_project: list[UsageRow]
    cost: UsageCost
    limits: UsageLimits


# ── shared helpers ──────────────────────────────────────────────────────────


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_stamp(value: str, name: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def local_day(when: dt.datetime, tz_offset_minutes: int) -> str:
    """The calendar day ``when`` falls in for a viewer ``tz_offset_minutes``
    ahead of UTC (JavaScript's ``-getTimezoneOffset()``)."""
    shifted = when.astimezone(dt.timezone.utc) + dt.timedelta(minutes=tz_offset_minutes)
    return shifted.strftime("%Y-%m-%d")


def _sum_rows(rows: list[UsageRow], *, key: str, label: str) -> UsageRow:
    total = UsageRow(key=key, label=label)
    extra: Counter[str] = Counter()
    priced = True
    cost = 0.0
    for row in rows:
        for name in (*USAGE_FIELDS, *SPLIT_FIELDS, "total_tokens", "requests"):
            setattr(total, name, getattr(total, name) + getattr(row, name))
        extra.update(row.extra)
        if row.cost is None:
            priced = False
        else:
            cost += row.cost
    total.extra = dict(extra)
    total.cost = cost if priced else None
    return total


def _fold(
    cells: list[dict[str, Any]],
    key_of: Any,
    label_of: Any,
    *,
    price: Any = None,
    model_of: Any = None,
    project_of: Any = None,
) -> list[UsageRow]:
    """Group cells, sum their tokens, price each group; largest first."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        groups[str(key_of(cell))].append(cell)
    rows: list[UsageRow] = []
    for key, members in groups.items():
        first = members[0]
        row = UsageRow(
            key=key,
            label=str(label_of(first)),
            model=model_of(first) if model_of else None,
            project=project_of(first) if project_of else None,
        )
        priced = price is not None
        cost = 0.0
        for cell in members:
            for name in USAGE_FIELDS:
                setattr(row, name, getattr(row, name) + int(cell.get(name, 0)))
            row.requests += int(cell.get("requests", 0))
            # A cell says which lane it was spent in; a provider that does not
            # record lanes leaves the split at zero.
            lane = str(cell.get("lane") or "")
            if lane:
                tokens = sum(int(cell.get(name, 0)) for name in USAGE_FIELDS)
                requests = int(cell.get("requests", 0))
                if lane == "subagent":
                    row.subagent_requests += requests
                    row.subagent_total_tokens += tokens
                else:
                    row.main_requests += requests
                    row.main_total_tokens += tokens
            if price is not None:
                value = price(cell)
                if value is None:
                    priced = False
                else:
                    cost += value
        row.total_tokens = (
            row.input_tokens + row.output_tokens + row.cache_read_tokens + row.cache_creation_tokens
        )
        row.cost = cost if priced else None
        # A group with nothing measured (Codex's model-less activity rows,
        # an empty hour) is noise in a usage grid, not a zero worth a line.
        if row.total_tokens or row.requests or any(row.extra.values()):
            rows.append(row)
    return sorted(rows, key=lambda item: (-item.total_tokens, item.key))


# ── Claude Code ─────────────────────────────────────────────────────────────


def claude_config_file() -> Path | None:
    """Where Claude Code caches its account and utilization state."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser() / ".claude.json")
    candidates.append(Path.home() / ".claude.json")
    for path in candidates:
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and (
                "cachedUsageUtilization" in payload or "oauthAccount" in payload
            ):
                return path
    return None


_WINDOW_LABELS = {
    "five_hour": "Session (5-hour)",
    "seven_day": "Weekly (all models)",
    "seven_day_opus": "Weekly (Opus)",
    "seven_day_sonnet": "Weekly (Sonnet)",
    "session": "Session (5-hour)",
    "weekly_all": "Weekly (all models)",
    "weekly_scoped": "Weekly (scoped)",
}
_WINDOW_MINUTES = {"five_hour": 300.0, "session": 300.0}


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(100.0, float(value)))


def _window(key: str, percent: float, resets_at: Any, *, label: str | None = None) -> UsageLimitWindow:
    minutes = _WINDOW_MINUTES.get(key, 10_080.0 if ("seven_day" in key or "weekly" in key) else None)
    return UsageLimitWindow(
        label=label or _WINDOW_LABELS.get(key, key.replace("_", " ")),
        used_percent=percent,
        remaining_percent=100.0 - percent,
        window_minutes=minutes,
        resets_at=str(resets_at) if resets_at else None,
    )


def claude_windows(utilization: dict[str, Any]) -> list[UsageLimitWindow]:
    """The rate-limit windows in Claude Code's utilization cache.

    Newer caches carry a ``limits`` list (kind, percent, resets_at, scope);
    older ones only the ``five_hour`` / ``seven_day`` objects. Internal
    experiment keys (``nimbus_quill``, ``tangelo``…) are not limits and are
    never shown. ``extra_usage`` is the monthly credit cap, shown as its own
    window with the amounts in its label.
    """
    windows: list[UsageLimitWindow] = []
    limits = utilization.get("limits")
    if isinstance(limits, list) and limits:
        for item in limits:
            if not isinstance(item, dict):
                continue
            percent = _percent(item.get("percent"))
            if percent is None:
                continue
            kind = str(item.get("kind") or item.get("group") or "window")
            label = _WINDOW_LABELS.get(kind)
            scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
            if kind == "weekly_scoped" and model.get("display_name"):
                label = f"Weekly ({model['display_name']})"
            windows.append(_window(kind, percent, item.get("resets_at"), label=label))
    else:
        for key in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
            value = utilization.get(key)
            if not isinstance(value, dict):
                continue
            percent = _percent(value.get("utilization"))
            if percent is None:
                continue
            windows.append(_window(key, percent, value.get("resets_at")))
    extra = utilization.get("extra_usage")
    if isinstance(extra, dict) and _percent(extra.get("utilization")) is not None:
        percent = _percent(extra.get("utilization")) or 0.0
        places = int(extra.get("decimal_places") or 2)
        currency = str(extra.get("currency") or "USD")
        parts = []
        used, limit = extra.get("used_credits"), extra.get("monthly_limit")
        if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and not isinstance(used, bool):
            parts.append(f"{used / 10**places:,.2f} of {limit / 10**places:,.2f} {currency}")
        if extra.get("is_enabled") is False:
            parts.append(f"disabled ({extra.get('disabled_reason') or 'off'})" if extra.get("disabled_reason") else "disabled")
        windows.append(
            _window("extra_usage", percent, None, label="Extra usage credits (monthly)" + (f" · {' · '.join(parts)}" if parts else ""))
        )
    return windows


def claude_limits(path: Path | None = None) -> UsageLimits:
    """Claude Code's own cached view of the account's rate-limit windows.

    Read from ``.claude.json`` exactly as Claude Code wrote it — never
    fetched. Account-identifying fields (email, names, ids) are never copied
    out; only the plan tier words and the window percentages are.
    """
    source = path or claude_config_file()
    if source is None:
        return UsageLimits(
            status="unavailable",
            reason="Claude Code has not cached its account limits on this Mac (no .claude.json with a utilization cache).",
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return UsageLimits(status="unavailable", reason=f"Could not read {source.name}: {exc}")
    account = payload.get("oauthAccount") if isinstance(payload.get("oauthAccount"), dict) else {}
    plan_parts = [
        str(account.get(key))
        for key in ("billingType", "userRateLimitTier", "organizationRateLimitTier")
        if account.get(key)
    ]
    plan = " · ".join(dict.fromkeys(plan_parts)) or None
    cached = payload.get("cachedUsageUtilization")
    if not isinstance(cached, dict) or not isinstance(cached.get("utilization"), dict):
        return UsageLimits(
            status="unavailable",
            plan=plan,
            reason="Claude Code has not cached a utilization reading yet; it appears after Claude Code checks the account.",
        )
    fetched = cached.get("fetchedAtMs")
    observed = (
        dt.datetime.fromtimestamp(float(fetched) / 1000, dt.timezone.utc).isoformat(timespec="seconds")
        if isinstance(fetched, (int, float)) and not isinstance(fetched, bool)
        else None
    )
    windows = claude_windows(cached["utilization"])
    if not windows:
        return UsageLimits(
            status="unavailable",
            plan=plan,
            observed_at=observed,
            reason="Claude Code's utilization cache holds no readable window.",
        )
    return UsageLimits(status="available", observed_at=observed, plan=plan, windows=windows)


def load_local_prices(db_path: Path | None = None) -> dict[str, tuple[float, float, float]]:
    """model name -> (input, output, cached-read) USD per million tokens.

    From the ``ai_models`` cache the SyncEngine keeps from the platform's
    model catalog. Empty when the cache is absent or unsynced — the report
    then says cost is unavailable and why.
    """
    if db_path is None:
        from app.config import LOCAL_DB_PATH

        db_path = Path(LOCAL_DB_PATH)
    if not db_path.is_file():
        return {}
    prices: dict[str, tuple[float, float, float]] = {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT name, raw_json FROM ai_models WHERE name LIKE 'claude%'"
            ).fetchall()
    except sqlite3.Error:
        return {}
    for name, raw in rows:
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            continue
        pricing = payload.get("pricing") if isinstance(payload, dict) else None
        if not isinstance(pricing, list) or not pricing or not isinstance(pricing[0], dict):
            continue
        tier = pricing[0]
        try:
            prices[str(name)] = (
                float(tier["input_price"]),
                float(tier["output_price"]),
                float(tier.get("cached_input_price", tier["input_price"])),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def match_price(model: str, prices: dict[str, tuple[float, float, float]]) -> tuple[float, float, float] | None:
    """Exact catalog name, else the longest catalog name the model id extends
    (``claude-fable-5-1`` -> ``claude-fable-5``). Never a looser guess."""
    if model in prices:
        return prices[model]
    best: str | None = None
    for name in prices:
        if model.startswith(name + "-") and (best is None or len(name) > len(best)):
            best = name
    return prices[best] if best else None


def price_claude_cell(cell: dict[str, Any], prices: dict[str, tuple[float, float, float]]) -> float | None:
    rates = match_price(str(cell.get("model") or ""), prices)
    if rates is None:
        return None
    input_rate, output_rate, cached_rate = rates
    return (
        int(cell.get("input_tokens", 0)) * input_rate
        + int(cell.get("cache_read_tokens", 0)) * cached_rate
        + int(cell.get("cache_creation_tokens", 0)) * input_rate * CACHE_WRITE_MULTIPLIER
        + int(cell.get("output_tokens", 0)) * output_rate
    ) / 1_000_000


def claude_report(
    cells: list[dict[str, Any]],
    *,
    start: dt.datetime,
    end: dt.datetime,
    tz_offset_minutes: int,
    status: dict[str, Any],
    prices: dict[str, tuple[float, float, float]],
    limits: UsageLimits,
    refreshing: bool = False,
) -> UsageReport:
    """Normalize the store's usage cells for one range."""
    price = (lambda cell: price_claude_cell(cell, prices)) if prices else None

    def day_of(cell: dict[str, Any]) -> str:
        when = dt.datetime.strptime(cell["hour"], "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
        return local_day(when, tz_offset_minutes)

    by_day = sorted(_fold(cells, day_of, day_of, price=price), key=lambda row: row.key)
    by_model = _fold(cells, lambda c: c["model"], lambda c: c["model"], price=price, model_of=lambda c: c["model"])
    by_session = _fold(
        cells,
        lambda c: c["session_id"],
        lambda c: c.get("title") or f"Session {str(c['session_id'])[:8]}",
        price=price,
        project_of=lambda c: c.get("project"),
    )
    by_project = _fold(
        cells,
        lambda c: c.get("project") or "unknown",
        lambda c: c.get("project") or "Project unavailable",
        price=price,
    )
    totals = _sum_rows(by_model, key="total", label="Total")
    unpriced = sorted({row.model for row in by_model if row.model and row.cost is None})
    if not prices:
        cost = UsageCost(
            available=False,
            unit=None,
            label="Estimated cost (USD, list price)",
            reason="No model prices are cached on this Mac yet: the platform's model catalog has not synced (Settings → Diagnostics shows the catalog sync).",
        )
    elif unpriced:
        cost = UsageCost(
            available=True,
            unit="usd",
            label="Estimated cost (USD, list price)",
            reason=f"No list price in the platform catalog for: {', '.join(unpriced)}. Their rows and the total show no cost rather than a guess.",
            unpriced_models=unpriced,
        )
    else:
        cost = UsageCost(available=True, unit="usd", label="Estimated cost (USD, list price)")
    pending = status.get("pending_sessions")
    notes = [
        "Each turn is counted once per message (Claude Code writes one line per streamed block).",
        "A session's total includes every sub-agent it ran; the Sub-agents column is that share of it.",
        "Hour resolution: a range edge inside an hour includes that whole hour.",
        f"Cache writes are priced at {CACHE_WRITE_MULTIPLIER}x the input rate (the 5-minute rate); 1-hour writes cost more than shown.",
    ]
    if pending:
        notes.insert(0, f"{pending} transcript(s) still being read in the background; totals grow until this reaches 0.")
    if not status.get("built"):
        notes.insert(0, "The first read of this Mac's transcripts has not finished; refresh in a moment.")
    if refreshing:
        notes.append("A refresh is running now.")
    return UsageReport(
        provider="claude_code",
        generated_at=_now(),
        range=UsageRange(start=start.isoformat(), end=end.isoformat()),
        tz_offset_minutes=tz_offset_minutes,
        source=UsageSource(
            kind="local_transcripts",
            description="Per-turn usage from Claude Code's own session transcripts on this Mac.",
            complete=bool(status.get("built")) and not pending,
            can_resume=bool(pending),
            pending_sessions=pending,
            updated_at=status.get("updated_at"),
            notes=notes,
        ),
        metrics=UsageMetrics(tokens=True, requests=True, cost=cost.available, lines=False, subagents=True),
        totals=totals,
        by_day=by_day,
        by_model=by_model,
        by_session=by_session,
        by_project=by_project,
        cost=cost,
        limits=limits,
    )


# ── Codex ───────────────────────────────────────────────────────────────────


def _codex_cell(row: dict[str, Any]) -> dict[str, Any]:
    cached = int(row.get("cached_input_tokens", 0))
    return {
        "input_tokens": max(0, int(row.get("input_tokens", 0)) - cached),
        "cache_read_tokens": cached,
        "cache_creation_tokens": 0,
        "output_tokens": int(row.get("output_tokens", 0)),
        "requests": int(row.get("response_count", 0)),
        "model": row.get("model"),
        "effort": row.get("effort"),
        "project": row.get("project"),
        "conversation_id": row.get("conversation_id"),
        "conversation_title": row.get("conversation_title"),
        "start": row.get("start"),
        "credits": row.get("estimated_standard_credits"),
        "credit_rate_known": row.get("credit_rate_known"),
    }


def _codex_price(cell: dict[str, Any]) -> float | None:
    if cell.get("credit_rate_known") is True and cell.get("credits") is not None:
        return float(cell["credits"])
    # A cell with nothing in it costs nothing; an unpriced cell with tokens is unknown.
    return 0.0 if not any(cell.get(name, 0) for name in USAGE_FIELDS) else None


def codex_report(
    snapshot: dict[str, Any],
    allowance: dict[str, Any],
    *,
    tz_offset_minutes: int,
) -> UsageReport:
    """The Codex collector's snapshot, re-keyed into the shared shape."""
    cells = [_codex_cell(row) for row in snapshot.get("cells", [])]
    bins = [_codex_cell(row) for row in snapshot.get("bins", [])]

    def day_of(cell: dict[str, Any]) -> str:
        return local_day(parse_stamp(str(cell["start"]), "bin"), tz_offset_minutes)

    by_day = sorted(_fold(bins, day_of, day_of, price=_codex_price), key=lambda row: row.key)
    by_model = _fold(cells, lambda c: c["model"], lambda c: c["model"], price=_codex_price, model_of=lambda c: c["model"])
    by_session = _fold(
        cells,
        lambda c: c.get("conversation_id") or "unknown",
        lambda c: c.get("conversation_title") or f"Conversation {str(c.get('conversation_id') or '')[:8]}",
        price=_codex_price,
        project_of=lambda c: c.get("project"),
    )
    by_project = _fold(
        cells,
        lambda c: c.get("project") or "unknown",
        lambda c: c.get("project") or "Project unavailable",
        price=_codex_price,
    )
    totals = _sum_rows(by_model, key="total", label="Total")
    credits = snapshot.get("credits") or {}
    unknown = [str(model) for model in credits.get("unknown_models") or []]
    coverage = snapshot.get("coverage") or {}
    limits = UsageLimits(
        status="available" if allowance.get("status") == "available" else "unavailable",
        observed_at=allowance.get("observed_at"),
        reason=allowance.get("reason"),
        plan=None,
        windows=[
            UsageLimitWindow(
                label=str(limit.get("bucket") or f"Window {index + 1}"),
                used_percent=limit.get("used_percent"),
                remaining_percent=limit.get("remaining_percent"),
                window_minutes=limit.get("window_minutes"),
                resets_at=(
                    dt.datetime.fromtimestamp(float(limit["resets_at"]), dt.timezone.utc).isoformat(timespec="seconds")
                    if isinstance(limit.get("resets_at"), (int, float)) and not isinstance(limit.get("resets_at"), bool)
                    else None
                ),
            )
            for index, limit in enumerate(allowance.get("limits") or [])
            if isinstance(limit, dict)
        ],
    )
    return UsageReport(
        provider="codex",
        generated_at=_now(),
        range=UsageRange(start=str(snapshot["range"]["start"]), end=str(snapshot["range"]["end"])),
        tz_offset_minutes=tz_offset_minutes,
        source=UsageSource(
            kind="local_rollouts",
            description="Token usage records from Codex's own rollout files on this Mac.",
            complete=bool(coverage.get("complete")),
            can_resume=bool(coverage.get("can_resume")),
            pending_sessions=(
                max(0, int(coverage.get("total_candidates", 0)) - int(coverage.get("completed_candidates", 0)))
                if coverage
                else None
            ),
            updated_at=snapshot.get("collected_at"),
            notes=[str(note) for note in coverage.get("notes") or []]
            + ["Ten-minute resolution: a range edge inside a ten-minute bin includes that bin."],
        ),
        metrics=UsageMetrics(tokens=True, requests=True, cost=True, lines=False, subagents=False),
        totals=totals,
        by_day=by_day,
        by_model=by_model,
        by_session=by_session,
        by_project=by_project,
        cost=UsageCost(
            available=True,
            unit="credits",
            label=str(credits.get("label") or "Estimated standard credits"),
            reason=(f"No credit rate known for: {', '.join(unknown)}." if unknown else None),
            unpriced_models=unknown,
        ),
        limits=limits,
    )


# ── Cursor ──────────────────────────────────────────────────────────────────


def cursor_state_db() -> Path:
    """Cursor's global state database, per platform. Read-only, never copied."""
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/Cursor"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Cursor"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "Cursor"
    return base / "User/globalStorage/state.vscdb"


_CURSOR_LINE_FIELDS = {
    "composerSuggestedLines": "composer_suggested_lines",
    "composerAcceptedLines": "composer_accepted_lines",
    "tabSuggestedLines": "tab_suggested_lines",
    "tabAcceptedLines": "tab_accepted_lines",
}


def cursor_report(
    *,
    start: dt.datetime,
    end: dt.datetime,
    tz_offset_minutes: int,
    db_path: Path | None = None,
) -> UsageReport:
    """What Cursor records on this Mac: lines per day and the plan tier."""
    path = db_path or cursor_state_db()
    first_day = local_day(start, tz_offset_minutes)
    last_day = local_day(end - dt.timedelta(microseconds=1), tz_offset_minutes)
    rows: list[UsageRow] = []
    plan: str | None = None
    notes = [
        "Cursor records suggested and accepted lines per day on this Mac; it keeps token and request usage on cursor.com (Settings → Usage), not locally.",
    ]
    complete = False
    if not path.is_file():
        notes.append(f"Cursor's state database was not found at {path}.")
    else:
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                stats = connection.execute(
                    "SELECT key, value FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats.%'"
                ).fetchall()
                membership = connection.execute(
                    "SELECT value FROM ItemTable WHERE key = 'cursorAuth/stripeMembershipType'"
                ).fetchone()
            complete = True
        except sqlite3.Error as exc:
            stats, membership = [], None
            notes.append(f"Cursor's state database could not be read: {exc}. Quit Cursor and refresh if this persists.")
        if membership and membership[0]:
            plan = str(membership[0])
        for key, value in stats:
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                continue
            day = str(payload.get("date") or str(key).rsplit(".", 1)[-1])
            if not (first_day <= day <= last_day):
                continue
            extra = {
                name: int(payload.get(source, 0) or 0)
                for source, name in _CURSOR_LINE_FIELDS.items()
                if isinstance(payload.get(source, 0), (int, float)) and not isinstance(payload.get(source), bool)
            }
            extra["suggested_lines"] = extra.get("composer_suggested_lines", 0) + extra.get("tab_suggested_lines", 0)
            extra["accepted_lines"] = extra.get("composer_accepted_lines", 0) + extra.get("tab_accepted_lines", 0)
            rows.append(UsageRow(key=day, label=day, extra=extra))
    rows.sort(key=lambda row: row.key)
    totals = _sum_rows(rows, key="total", label="Total")
    totals.cost = None
    return UsageReport(
        provider="cursor",
        generated_at=_now(),
        range=UsageRange(start=start.isoformat(), end=end.isoformat()),
        tz_offset_minutes=tz_offset_minutes,
        source=UsageSource(
            kind="local_state_db",
            description="Daily suggested/accepted lines from Cursor's state database on this Mac.",
            complete=complete,
            notes=notes,
        ),
        metrics=UsageMetrics(tokens=False, requests=False, cost=False, lines=True, subagents=False),
        totals=totals,
        by_day=rows,
        by_model=[],
        by_session=[],
        by_project=[],
        cost=UsageCost(
            available=False,
            unit=None,
            label="Cost",
            reason="Cursor keeps no token or cost record on this Mac; see cursor.com → Settings → Usage.",
        ),
        limits=UsageLimits(
            status="unavailable",
            plan=plan,
            reason="Cursor keeps its request quotas on cursor.com; nothing on this Mac records them."
            + (f" Plan on this Mac: {plan}." if plan else ""),
        ),
    )


# ── VS Code ─────────────────────────────────────────────────────────────────


def vscode_report(*, start: dt.datetime, end: dt.datetime, tz_offset_minutes: int, extension_detected: bool) -> UsageReport:
    """VS Code writes no local usage ledger; say so, with the one remedy."""
    reason = (
        "VS Code exposes no local usage data. The AI Matrx VS Code extension is installed but does not write a usage ledger yet; usage will appear here when it does."
        if extension_detected
        else "VS Code exposes no local usage data, and the AI Matrx VS Code extension is not installed on this Mac. Install it from the Settings tab to start recording sessions; usage will appear here when the extension reports it."
    )
    empty = UsageRow(key="total", label="Total")
    return UsageReport(
        provider="vscode",
        generated_at=_now(),
        range=UsageRange(start=start.isoformat(), end=end.isoformat()),
        tz_offset_minutes=tz_offset_minutes,
        source=UsageSource(kind="none", description="No local usage source.", complete=True, notes=[reason]),
        metrics=UsageMetrics(tokens=False, requests=False, cost=False, lines=False, subagents=False),
        totals=empty,
        by_day=[],
        by_model=[],
        by_session=[],
        by_project=[],
        cost=UsageCost(available=False, unit=None, label="Cost", reason=reason),
        limits=UsageLimits(status="unavailable", reason=reason),
    )


# ── the one entry point the route calls ─────────────────────────────────────


async def build_report(
    provider: str,
    *,
    start: dt.datetime,
    end: dt.datetime,
    refresh: bool,
    tz_offset_minutes: int,
) -> dict[str, Any]:
    """One provider's UsageReport for one range. Raises ValueError on bad input."""
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    if start >= end:
        raise ValueError("start must be before end")
    if provider == "codex":
        from app.services.codex_usage import snapshot_service
        from app.services.codex_usage.allowance import allowance_service

        snapshot, allowance = await asyncio.gather(
            snapshot_service.read(start, end, refresh), allowance_service.read(refresh)
        )
        return codex_report(snapshot, allowance, tz_offset_minutes=tz_offset_minutes).model_dump()
    if provider == "claude_code":
        from app.services.coding_sessions import claude_overview

        if refresh:
            try:
                await claude_overview.refresh_index()
            except Exception:  # noqa: BLE001 — refresh_index logged it; read what we have
                pass
        else:
            claude_overview.start_index_refresh()
        store = claude_overview.index_store()
        start_hour = start.strftime("%Y-%m-%dT%H")
        end_hour = (end + dt.timedelta(hours=1) - dt.timedelta(microseconds=1)).strftime("%Y-%m-%dT%H")
        cells, status, prices, limits = await asyncio.gather(
            asyncio.to_thread(store.usage_rows, start_hour, end_hour),
            asyncio.to_thread(store.usage_status),
            asyncio.to_thread(load_local_prices),
            asyncio.to_thread(claude_limits),
        )
        return claude_report(
            cells,
            start=start,
            end=end,
            tz_offset_minutes=tz_offset_minutes,
            status=status,
            prices=prices,
            limits=limits,
            refreshing=claude_overview.index_refreshing(),
        ).model_dump()
    if provider == "cursor":
        return (
            await asyncio.to_thread(cursor_report, start=start, end=end, tz_offset_minutes=tz_offset_minutes)
        ).model_dump()
    from app.services.coding_sessions.provider_readiness import get_provider_readiness

    detected = False
    try:
        adapter = get_provider_readiness()._adapter("vscode")  # noqa: SLF001 — same package
        detected = bool(adapter.get("detected"))
    except Exception:  # noqa: BLE001 — readiness is advisory here
        detected = False
    return vscode_report(start=start, end=end, tz_offset_minutes=tz_offset_minutes, extension_detected=detected).model_dump()


__all__ = [
    "PROVIDERS",
    "UsageReport",
    "UsageRow",
    "build_report",
    "claude_limits",
    "claude_report",
    "claude_windows",
    "codex_report",
    "cursor_report",
    "load_local_prices",
    "match_price",
    "parse_stamp",
    "vscode_report",
]
