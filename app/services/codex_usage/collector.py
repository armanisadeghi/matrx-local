"""Read bounded Codex telemetry without retaining conversation contents.

The collector deliberately exposes attribution and counter fields only.  It
never returns prompts, response text, tool arguments, tool output, or any path
under the user's private ``.arman`` directory.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
ACTIVITY_FIELDS = ("peer_message_call_ids", "peer_message_invocations", "child_call_ids", "child_invocations")
MAX_SECONDS = 60
MAX_BYTES = 8 * 1024**3
MAX_FILES = 400
STANDARD_RATES: dict[str, tuple[float, float, float]] = {
    "gpt-6-astra": (250, 25, 1250), "gpt-5.6-sol": (100, 10, 500),
    "gpt-5.6-terra": (50, 5, 300), "gpt-5.6-luna": (5, 0.5, 30),
    "gpt-5.5": (125, 12.5, 750), "gpt-5.4": (62.5, 6.25, 375),
    "gpt-5.4-mini": (18.75, 1.875, 113), "gpt-5.3-codex": (43.75, 4.375, 350),
}


def _stamp(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _metrics() -> collections.Counter[str]:
    return collections.Counter({field: 0 for field in (*FIELDS, *ACTIVITY_FIELDS)} | {"response_count": 0})


def _plain(value: collections.Counter[str]) -> dict[str, int]:
    result = {field: int(value.get(field, 0)) for field in (*FIELDS, *ACTIVITY_FIELDS, "response_count")}
    result["uncached_input_tokens"] = max(0, result["input_tokens"] - result["cached_input_tokens"])
    return result


def _project_for(cwd: Any, codex_home: Path) -> str:
    try:
        path = Path(str(cwd)).resolve()
        code = Path.home() / "code"
        worktrees = codex_home / "worktrees"
        if path.is_relative_to(worktrees):
            parts = path.relative_to(worktrees).parts
            return parts[1] if len(parts) > 1 else "Worktree (project unavailable)"
        relative = path.relative_to(code)
        return relative.parts[0] if relative.parts else "code"
    except (ValueError, OSError):
        return "unknown"


def _call_name(payload: dict[str, Any]) -> str:
    function = payload.get("function")
    return str(payload.get("name") or payload.get("tool_name") or (function.get("name") if isinstance(function, dict) else "") or "")


def _call_args(payload: dict[str, Any]) -> Any:
    function = payload.get("function")
    return payload.get("arguments", payload.get("input", function.get("arguments", {}) if isinstance(function, dict) else {}))


def _target_thread_id(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    for key in ("threadId", "thread_id", "targetThreadId", "target_thread_id"):
        if value.get(key):
            return str(value[key])
    return None


_EXEC_SEND = re.compile(r"tools\.mcp__codex_app__send_message_to_thread\s*\(\s*\{(?P<body>.{0,12000}?)\}\s*\)", re.S)
_EXEC_TARGET = re.compile(r"(?:threadId|thread_id)\s*:\s*['\"](?P<id>[0-9a-f-]{36})['\"]")
_EXEC_CHILD = re.compile(r"tools\.collaboration\.(?:spawn_agent|followup_task|send_message)\s*\(")


def _embedded_activity(value: Any) -> tuple[list[str | None], int]:
    """Parse actual call syntax from an exec input, never result text."""
    if not isinstance(value, str):
        return [], 0
    targets = [_EXEC_TARGET.search(match.group("body")).group("id") if _EXEC_TARGET.search(match.group("body")) else None for match in _EXEC_SEND.finditer(value)]
    return targets, len(_EXEC_CHILD.findall(value))


def _metadata(codex_home: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    state = codex_home / "state_5.sqlite"
    if not state.is_file():
        return {}, {}
    try:
        with sqlite3.connect(f"file:{state}?mode=ro", uri=True) as db:
            rows = db.execute("select id, name, cwd, rollout_path, created_at, updated_at, project_id from threads").fetchall()
            edges = dict(db.execute("select child_thread_id, parent_thread_id from thread_spawn_edges"))
            projects = dict(db.execute("select id, name from projects"))
    except sqlite3.Error:
        return {}, {}
    threads = {
        str(row[0]): {
            "title": str(row[1] or f"Untitled conversation {str(row[0])[:8]}"),
            "cwd": row[2], "rollout_path": row[3], "created_at": row[4], "updated_at": row[5],
            "project": projects.get(row[6]) or _project_for(row[2], codex_home),
        }
        for row in rows if row[0] and row[3]
    }
    for tid, item in threads.items():
        if tid not in edges and item["project"] == "code":
            item["project"] = "Workspace (multiple projects)"
    return threads, {str(k): str(v) for k, v in edges.items() if k and v}


def _estimate(row: dict[str, Any]) -> dict[str, Any]:
    rates = STANDARD_RATES.get(str(row["model"]))
    if rates is None:
        return {**row, "estimated_standard_credits": None, "credit_rate_known": False}
    uncached = max(0, int(row["input_tokens"]) - int(row["cached_input_tokens"]))
    # output_tokens already includes reasoning_output_tokens. Never add it twice.
    estimate = uncached * rates[0] / 1_000_000 + int(row["cached_input_tokens"]) * rates[1] / 1_000_000 + int(row["output_tokens"]) * rates[2] / 1_000_000
    return {**row, "estimated_standard_credits": estimate, "credit_rate_known": True}


def _aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], collections.Counter[str]] = collections.defaultdict(_metrics)
    for row in rows:
        bucket = buckets[tuple(row[key] for key in keys)]
        for field in (*FIELDS, *ACTIVITY_FIELDS, "response_count"):
            bucket[field] += int(row.get(field, 0))
    result = []
    for key, value in buckets.items():
        item = dict(zip(keys, key)) | _plain(value)
        # A model aggregation retains pricing; project/conversation rows include mixed models.
        item["model"] = item.get("model", "mixed")
        result.append(_estimate(item))
    return sorted(result, key=lambda row: row["total_tokens"], reverse=True)


def collect_usage(start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """Return sanitized aggregate activity for the requested half-open UTC window."""
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start and end must be ordered timezone-aware datetimes")
    start, end = start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)
    began, codex_home = time.monotonic(), Path.home() / ".codex"
    threads, edges = _metadata(codex_home)
    coverage: dict[str, Any] = {
        "indexed_files": 0, "scanned_files": 0, "missing_files": 0, "skipped_files": 0,
        "truncated_files": 0, "bytes_read": 0, "max_seconds": MAX_SECONDS, "max_bytes": MAX_BYTES,
        "max_indexed_files": MAX_FILES, "candidate_overflow": False, "budget_exhausted": False,
        "complete": False, "notes": [],
    }
    if not threads:
        coverage["notes"].append("Codex local state is unavailable or contains no readable conversations.")
        return _result(start, end, coverage, [], [], [], 0, 0)

    def lineage(tid: str) -> list[str]:
        chain: list[str] = []; seen: set[str] = set(); current: str | None = tid
        while current and current not in seen:
            chain.append(current); seen.add(current); current = edges.get(current)
        return chain

    def root_for(tid: str) -> str:
        chain = lineage(tid)
        return chain[-1] if chain else tid

    candidates = []
    for tid, item in threads.items():
        try:
            active = float(item["created_at"]) < end.timestamp() and float(item["updated_at"]) >= start.timestamp()
        except (TypeError, ValueError):
            active = True
        if active:
            candidates.append((tid, item))
    candidates.sort(key=lambda item: Path(str(item[1]["rollout_path"])).stat().st_size if Path(str(item[1]["rollout_path"])).is_file() else 0)
    coverage["candidate_overflow"] = len(candidates) > MAX_FILES
    candidates = candidates[:MAX_FILES]; coverage["indexed_files"] = len(candidates)
    cells: dict[tuple[str, str, str], collections.Counter[str]] = collections.defaultdict(_metrics)
    bins: dict[tuple[dt.datetime, str, str, str], collections.Counter[str]] = collections.defaultdict(_metrics)
    seen_responses: set[str] = set(); duplicates = foreign_records = 0
    seen_tool_calls: set[str] = set()
    target_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)

    for tid, info in candidates:
        if time.monotonic() - began >= MAX_SECONDS:
            coverage["budget_exhausted"] = True; coverage["notes"].append("time budget reached"); break
        path = Path(str(info["rollout_path"]))
        # The ownership filter is deliberate: private operator storage is never a candidate.
        if ".arman" in path.parts or not path.is_file():
            coverage["missing_files"] += 1; continue
        try:
            size = path.stat().st_size
        except OSError:
            coverage["missing_files"] += 1; continue
        if coverage["bytes_read"] + size > MAX_BYTES:
            coverage["skipped_files"] += 1; coverage["budget_exhausted"] = True; continue
        contexts: dict[str, tuple[str, str]] = {}; usages: list[tuple[dt.datetime, dict[str, Any]]] = []
        try:
            with path.open("rb") as source:
                for raw in source:
                    coverage["bytes_read"] += len(raw)
                    if time.monotonic() - began >= MAX_SECONDS:
                        coverage["budget_exhausted"] = True; coverage["truncated_files"] += 1; break
                    if not any(tag in raw[:500] for tag in (b'"turn_context"', b'"token_usage_record"', b'"custom_tool_call"', b'"function_call"')):
                        continue
                    try:
                        record = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    payload = record.get("payload") or {}; kind = record.get("type"); when = _stamp(record.get("timestamp"))
                    if kind == "turn_context" and payload.get("turn_id"):
                        contexts[str(payload["turn_id"])] = (str(payload.get("model") or "unknown"), str(payload.get("reasoning_effort", payload.get("effort")) or "unknown")); continue
                    if not when or not (start <= when < end):
                        continue
                    if kind == "token_usage_record": usages.append((when, payload))
                    elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                        name, args = _call_name(payload), _call_args(payload)
                        call_id = str(payload.get("call_id") or payload.get("id") or "")
                        if call_id and call_id in seen_tool_calls:
                            continue
                        if call_id:
                            seen_tool_calls.add(call_id)
                        peer_targets: list[str | None] = []
                        child_invocations = 0
                        if "send_message_to_thread" in name:
                            peer_targets = [_target_thread_id(args)]
                        elif name == "exec":
                            peer_targets, child_invocations = _embedded_activity(args)
                        elif any(part in name for part in ("spawn_agent", "followup_task", "collaboration.send_message")):
                            child_invocations = 1
                        if peer_targets or child_invocations:
                            # Activity belongs to this source conversation only;
                            # no output or tool-description strings participate.
                            activity = cells[(tid, "unknown", "unknown")]
                            if peer_targets:
                                activity["peer_message_call_ids"] += 1
                                activity["peer_message_invocations"] += len(peer_targets)
                                for target in peer_targets:
                                    if target:
                                        target_counts[tid][target] += 1
                            if child_invocations:
                                activity["child_call_ids"] += 1
                                activity["child_invocations"] += child_invocations
            coverage["scanned_files"] += 1
        except OSError:
            coverage["missing_files"] += 1; continue
        for _when, payload in usages:
            if str(payload.get("thread_id")) != tid:
                foreign_records += 1; continue
            response_id = payload.get("response_id")
            if not response_id:
                continue
            if str(response_id) in seen_responses:
                duplicates += 1; continue
            seen_responses.add(str(response_id))
            model, effort = contexts.get(str(payload.get("turn_id")), ("unknown", "unknown"))
            bucket = _when.replace(minute=(_when.minute // 10) * 10, second=0, microsecond=0)
            for group in (cells[(tid, model, effort)], bins[(bucket, tid, model, effort)]):
                group["response_count"] += 1
                for field in FIELDS:
                    value = (payload.get("usage") or {}).get(field, 0)
                    if isinstance(value, int) and value >= 0: group[field] += value

    rows = []
    for (tid, model, effort), metrics in cells.items():
        root = root_for(tid); owner = threads.get(tid, {})
        rows.append({"conversation_id": tid, "conversation_title": owner.get("title", f"Conversation {tid[:8]}"), "root_id": root,
                     "project": owner.get("project", "unknown"), "model": model, "effort": effort, **_plain(metrics)})
    # Tool activity is attributed to the source conversation, while model rows
    # remain truthful when a tool event has no corresponding token response.
    for tid in {row["conversation_id"] for row in rows}:
        thread_rows = [row for row in rows if row["conversation_id"] == tid]
        activity_row = next((row for row in thread_rows if row["model"] == "unknown" and any(row[field] for field in ACTIVITY_FIELDS)), None)
        if activity_row is None:
            continue
        anchor = max((row for row in thread_rows if row is not activity_row), key=lambda row: row["total_tokens"], default=activity_row)
        if anchor is not activity_row:
            for field in ACTIVITY_FIELDS:
                anchor[field] += activity_row[field]
            rows.remove(activity_row)
        if tid in target_counts:
            anchor["peer_targets"] = [{"conversation_id": target, "title": threads[target]["title"], "invocations": count}
                                      for target, count in target_counts[tid].most_common() if target in threads]
    coverage["complete"] = not any((coverage["budget_exhausted"], coverage["missing_files"], coverage["truncated_files"], coverage["candidate_overflow"]))
    bin_rows = [{"start": bucket.isoformat(), "conversation_id": tid, "model": model, "effort": effort, **_plain(metrics)}
                for (bucket, tid, model, effort), metrics in sorted(bins.items())]
    task_rows = []
    for tid in sorted({row["conversation_id"] for row in rows}):
        owner = threads.get(tid, {}); root = root_for(tid)
        task_rows.append({"id": tid, "title": owner.get("title", f"Conversation {tid[:8]}"), "project": owner.get("project", "unknown"), "root_id": root,
                          "is_worker": tid != root, **{field: sum(row[field] for row in rows if row["conversation_id"] == tid) for field in ACTIVITY_FIELDS}})
    return _result(start, end, coverage, rows, bin_rows, task_rows, duplicates, foreign_records)


def _result(start: dt.datetime, end: dt.datetime, coverage: dict[str, Any], rows: list[dict[str, Any]], bins: list[dict[str, Any]], tasks: list[dict[str, Any]], duplicates: int, foreign_records: int) -> dict[str, Any]:
    total = _plain(collections.Counter({field: sum(int(row.get(field, 0)) for row in rows) for field in (*FIELDS, *ACTIVITY_FIELDS, "response_count")}))
    model_effort = [_estimate(row) for row in _aggregate(rows, ("model", "effort"))]
    models = [_estimate(row) for row in _aggregate(rows, ("model",))]
    projects = _aggregate(rows, ("project",))
    conversations = sorted(rows, key=lambda row: row["total_tokens"], reverse=True)
    workers = [row for row in conversations if row["conversation_id"] != row["root_id"]]
    estimated = sum(float(row["estimated_standard_credits"] or 0) for row in models)
    return {"collected_at": dt.datetime.now(dt.timezone.utc).isoformat(), "range": {"start": start.isoformat(), "end": end.isoformat()},
            "coverage": coverage, "totals": total | {"estimated_standard_credits": estimated},
            "credits": {"estimated_standard": estimated, "measured_allowance": None,
                        "label": "Estimated standard credits — not actual Pro allowance debits",
                        "unknown_models": sorted({str(row["model"]) for row in models if not row["credit_rate_known"]})},
            "models": models, "model_effort": model_effort, "projects": projects,
            # Canonical primitive rows stay in the response so native and web
            # drilldowns reconcile to the same values instead of re-collecting.
            "cells": conversations, "tasks": tasks, "bins": bins,
            "conversations": conversations, "workers": workers, "selected_view_groups": {"model": models, "model_effort": model_effort},
            "activity": {"classification": "heuristic metadata classification; inbound wakes and causal cost are unknown without recipient provenance", "outbound_peer_calls": total["peer_message_invocations"], "child_calls": total["child_invocations"], "inbound_peer_wakes": "unknown", "causal_cost": "unknown"},
            "qualification": ["Local telemetry is not account billing or quota usage.", "Reasoning output is included in output tokens and is never double-counted.", f"Deduplicated response_id globally across selected files: {duplicates} ignored.", f"Embedded owner filter excluded {foreign_records} copied or foreign records.", "Peer activity is proved from function calls and embedded functions.exec JavaScript only; tool outputs and descriptions are not counted.", "Inbound peer wakes and causal cost are unknown without recipient provenance.", "No prompts, response text, tool arguments, tool output, credential values, or recipient identities are returned."]}


class CodexUsageSnapshotService:
    """Range-keyed snapshot cache with a single collector at a time.

    Collection reads potentially large local JSONL files. A normal view only
    receives a matching cached snapshot; explicit refreshes share one bounded
    collection rather than starting competing scans.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._collection_lock = asyncio.Lock()
        self._inflight: dict[tuple[str, str], asyncio.Task[dict[str, Any]]] = {}

    async def read(self, start: dt.datetime, end: dt.datetime, refresh: bool = False) -> dict[str, Any]:
        key = (start.astimezone(dt.timezone.utc).isoformat(), end.astimezone(dt.timezone.utc).isoformat())
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not refresh:
                return cached | {"collection": {"state": "cached", "in_progress": False}}
            task = self._inflight.get(key)
            if task is None:
                async def collect_once() -> dict[str, Any]:
                    # One disk scan at a time across every range. This avoids
                    # competing bounded readers on the user's machine.
                    async with self._collection_lock:
                        return await asyncio.to_thread(collect_usage, start, end)
                task = asyncio.create_task(collect_once())
                self._inflight[key] = task
        try:
            snapshot = await task
            async with self._lock:
                self._cache[key] = snapshot
            return snapshot | {"collection": {"state": "refreshed", "in_progress": False}}
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)



snapshot_service = CodexUsageSnapshotService()
