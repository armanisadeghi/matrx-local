"""Bounded, resumable, local-only Codex usage collection."""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
ACTIVITY_FIELDS = ("peer_message_call_ids", "peer_message_invocations", "child_call_ids", "child_invocations")
MAX_SECONDS, MAX_BYTES, MAX_FILES, MAX_CACHE_STATES = 60, 8 * 1024**3, 400, 4
STANDARD_RATES = {"gpt-6-astra": (250, 25, 1250), "gpt-5.6-sol": (100, 10, 500), "gpt-5.6-terra": (50, 5, 300), "gpt-5.6-luna": (5, .5, 30), "gpt-5.5": (125, 12.5, 750), "gpt-5.4": (62.5, 6.25, 375), "gpt-5.4-mini": (18.75, 1.875, 113), "gpt-5.3-codex": (43.75, 4.375, 350)}
_EXEC_SEND = re.compile(r"tools\.mcp__codex_app__send_message_to_thread\s*\(\s*\{(?P<body>.{0,12000}?)\}\s*\)", re.S)
_EXEC_TARGET = re.compile(r"(?:threadId|thread_id)\s*:\s*['\"](?P<id>[0-9a-f-]{36})['\"]")
_EXEC_CHILD = re.compile(r"tools\.collaboration\.(?:spawn_agent|followup_task|send_message)\s*\(")


def _stamp(value: Any) -> dt.datetime | None:
    try: return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError): return None


def _metrics() -> collections.Counter[str]: return collections.Counter({key: 0 for key in (*FIELDS, *ACTIVITY_FIELDS, "response_count")})
def _plain(value: collections.Counter[str]) -> dict[str, int]:
    output = {key: int(value.get(key, 0)) for key in (*FIELDS, *ACTIVITY_FIELDS, "response_count")}
    output["uncached_input_tokens"] = max(0, output["input_tokens"] - output["cached_input_tokens"]); return output


def _project(cwd: Any, home: Path) -> str:
    try:
        path, code = Path(str(cwd)).resolve(), Path.home() / "code"
        if path.is_relative_to(home / "worktrees"):
            parts = path.relative_to(home / "worktrees").parts; return parts[1] if len(parts) > 1 else "Worktree (project unavailable)"
        relative = path.relative_to(code); return relative.parts[0] if relative.parts else "code"
    except (ValueError, OSError): return "unknown"


def _metadata(home: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    state = home / "state_5.sqlite"
    if not state.is_file(): return {}, {}
    try:
        with sqlite3.connect(f"file:{state}?mode=ro", uri=True) as db:
            rows = db.execute("select id,name,cwd,rollout_path,created_at,updated_at,project_id from threads").fetchall()
            edges, projects = dict(db.execute("select child_thread_id,parent_thread_id from thread_spawn_edges")), dict(db.execute("select id,name from projects"))
    except sqlite3.Error: return {}, {}
    threads = {str(row[0]): {"title": str(row[1] or f"Untitled conversation {str(row[0])[:8]}"), "cwd": row[2], "rollout_path": row[3], "created_at": row[4], "updated_at": row[5], "project": projects.get(row[6]) or _project(row[2], home)} for row in rows if row[0] and row[3]}
    for tid, item in threads.items():
        if tid not in edges and item["project"] == "code": item["project"] = "Workspace (multiple projects)"
    return threads, {str(key): str(value) for key, value in edges.items() if key and value}


def _call(payload: dict[str, Any]) -> tuple[str, Any, str]:
    function = payload.get("function") if isinstance(payload.get("function"), dict) else {}
    return str(payload.get("name") or payload.get("tool_name") or function.get("name") or ""), payload.get("arguments", payload.get("input", function.get("arguments", {}))), str(payload.get("call_id") or payload.get("id") or "")


def _target(value: Any) -> str | None:
    if isinstance(value, str):
        try: value = json.loads(value)
        except (TypeError, ValueError): return None
    if isinstance(value, dict):
        for key in ("threadId", "thread_id", "targetThreadId", "target_thread_id"):
            if value.get(key): return str(value[key])
    return None


def _embedded(value: Any) -> tuple[list[str | None], int]:
    if not isinstance(value, str): return [], 0
    targets = []
    for match in _EXEC_SEND.finditer(value):
        hit = _EXEC_TARGET.search(match.group("body")); targets.append(hit.group("id") if hit else None)
    return targets, len(_EXEC_CHILD.findall(value))


@dataclass(frozen=True)
class Candidate:
    thread_id: str; path: Path; frozen_size: int


@dataclass
class UsageScan:
    start: dt.datetime; end: dt.datetime; created_at: dt.datetime; threads: dict[str, dict[str, Any]]; edges: dict[str, str]; candidates: list[Candidate]
    cursor: int = 0; cells: dict[tuple[str, str, str], collections.Counter[str]] = field(default_factory=lambda: collections.defaultdict(_metrics)); bins: dict[tuple[dt.datetime, str, str, str], collections.Counter[str]] = field(default_factory=lambda: collections.defaultdict(_metrics))
    seen_responses: set[str] = field(default_factory=set); seen_calls: set[str] = field(default_factory=set); targets: dict[str, collections.Counter[str]] = field(default_factory=lambda: collections.defaultdict(collections.Counter))
    duplicates: int = 0; foreign_records: int = 0; missing: int = 0; skipped: int = 0; truncated: int = 0; bytes_read: int = 0; notes: list[str] = field(default_factory=list)

    def root(self, tid: str) -> str:
        seen: set[str] = set()
        while tid in self.edges and tid not in seen: seen.add(tid); tid = self.edges[tid]
        return tid

    @property
    def complete(self) -> bool: return self.cursor >= len(self.candidates)


def create_scan(start: dt.datetime, end: dt.datetime, home: Path | None = None) -> UsageScan:
    if start.tzinfo is None or end.tzinfo is None or start >= end: raise ValueError("start and end must be ordered timezone-aware datetimes")
    start, end, home = start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc), home or Path.home() / ".codex"
    threads, edges = _metadata(home); candidates: list[Candidate] = []
    for tid, item in threads.items():
        try: active = float(item["created_at"]) < end.timestamp() and float(item["updated_at"]) >= start.timestamp()
        except (TypeError, ValueError): active = True
        path = Path(str(item["rollout_path"]))
        if active and ".arman" not in path.parts:
            try: candidates.append(Candidate(tid, path, path.stat().st_size))
            except OSError: candidates.append(Candidate(tid, path, 0))
    # Stable ID order avoids a size-derived ranking. Every frozen candidate is
    # eventually visited across explicit continuations.
    candidates.sort(key=lambda item: item.thread_id)
    scan = UsageScan(start, end, dt.datetime.now(dt.timezone.utc), threads, edges, candidates)
    if not threads: scan.notes.append("Codex local state is unavailable or contains no readable conversations.")
    return scan


def _read_candidate(scan: UsageScan, candidate: Candidate, began: float, batch_bytes: int) -> tuple[bool, int, dict[tuple[str, str, str], collections.Counter[str]], dict[tuple[dt.datetime, str, str, str], collections.Counter[str]], list[tuple[str, dict[str, Any], dict[str, tuple[str, str]], dt.datetime]], list[tuple[str, list[str | None], int]]]:
    """Read one frozen file. A partial final line is discarded, never parsed."""
    empty_cells: dict[tuple[str, str, str], collections.Counter[str]] = collections.defaultdict(_metrics); empty_bins: dict[tuple[dt.datetime, str, str, str], collections.Counter[str]] = collections.defaultdict(_metrics)
    if not candidate.path.is_file() or candidate.frozen_size <= 0: return True, 0, empty_cells, empty_bins, [], []
    if candidate.frozen_size + batch_bytes > MAX_BYTES: return True, 0, empty_cells, empty_bins, [], []
    contexts: dict[str, tuple[str, str]] = {}; usage: list[tuple[str, dict[str, Any], dict[str, tuple[str, str]], dt.datetime]] = []; activity: list[tuple[str, list[str | None], int]] = []; read = 0
    try:
        with candidate.path.open("rb") as source:
            while read < candidate.frozen_size:
                if time.monotonic() - began >= MAX_SECONDS: return False, read, empty_cells, empty_bins, [], []
                raw = source.readline(candidate.frozen_size - read); read += len(raw)
                if not raw: break
                if read >= candidate.frozen_size and not raw.endswith(b"\n"): break
                if not any(tag in raw[:500] for tag in (b'"turn_context"', b'"token_usage_record"', b'"custom_tool_call"', b'"function_call"')): continue
                try: record = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError): continue
                payload, kind, when = record.get("payload") or {}, record.get("type"), _stamp(record.get("timestamp"))
                if kind == "turn_context" and payload.get("turn_id"):
                    contexts[str(payload["turn_id"])] = (str(payload.get("model") or "unknown"), str(payload.get("reasoning_effort", payload.get("effort")) or "unknown")); continue
                if not when or not (scan.start <= when < scan.end): continue
                if kind == "token_usage_record": usage.append((candidate.thread_id, payload, contexts, when)); continue
                if kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                    name, args, call_id = _call(payload); targets: list[str | None] = []; children = 0
                    if "send_message_to_thread" in name: targets = [_target(args)]
                    elif name == "exec": targets, children = _embedded(args)
                    elif any(key in name for key in ("spawn_agent", "followup_task", "collaboration.send_message")): children = 1
                    if targets or children: activity.append((call_id or f"{candidate.thread_id}:{read}", targets, children))
    except OSError: return True, read, empty_cells, empty_bins, [], []
    return True, read, empty_cells, empty_bins, usage, activity


def advance_scan(scan: UsageScan) -> None:
    began, batch_bytes, visited = time.monotonic(), 0, 0
    while scan.cursor < len(scan.candidates) and visited < MAX_FILES:
        if time.monotonic() - began >= MAX_SECONDS: scan.notes.append("time budget reached; continue collection to resume"); break
        candidate = scan.candidates[scan.cursor]
        if not candidate.path.is_file() or candidate.frozen_size <= 0:
            scan.missing += 1; scan.cursor += 1; visited += 1; continue
        if batch_bytes + candidate.frozen_size > MAX_BYTES:
            scan.skipped += 1; scan.notes.append(f"frozen file exceeded byte budget: {candidate.path.name}"); scan.cursor += 1; visited += 1; continue
        complete, read, _cells, _bins, usage, activity = _read_candidate(scan, candidate, began, batch_bytes)
        batch_bytes += read; scan.bytes_read += read
        if not complete:
            scan.truncated += 1; scan.notes.append("time budget reached while reading a file; no partial file data was counted"); break
        scan.cursor += 1; visited += 1
        for tid, payload, contexts, when in usage:
            if str(payload.get("thread_id")) != tid: scan.foreign_records += 1; continue
            response_id = str(payload.get("response_id") or "")
            if not response_id: continue
            if response_id in scan.seen_responses: scan.duplicates += 1; continue
            scan.seen_responses.add(response_id); model, effort = contexts.get(str(payload.get("turn_id")), ("unknown", "unknown")); usage_data = payload.get("usage") or {}
            bucket = when.replace(minute=(when.minute // 10) * 10, second=0, microsecond=0)
            for group in (scan.cells[(tid, model, effort)], scan.bins[(bucket, tid, model, effort)]):
                group["response_count"] += 1
                for key in FIELDS:
                    value = usage_data.get(key, 0)
                    if isinstance(value, int) and value >= 0: group[key] += value
        for call_id, targets, children in activity:
            if call_id in scan.seen_calls: continue
            scan.seen_calls.add(call_id); row = scan.cells[(candidate.thread_id, "unknown", "unknown")]
            if targets:
                row["peer_message_call_ids"] += 1; row["peer_message_invocations"] += len(targets)
                for target in targets:
                    if target: scan.targets[candidate.thread_id][target] += 1
            if children: row["child_call_ids"] += 1; row["child_invocations"] += children


def _estimate(row: dict[str, Any]) -> dict[str, Any]:
    rates = STANDARD_RATES.get(str(row["model"]))
    if rates is None: return row | {"estimated_standard_credits": None, "credit_rate_known": False}
    value = (max(0, row["input_tokens"] - row["cached_input_tokens"]) * rates[0] + row["cached_input_tokens"] * rates[1] + row["output_tokens"] * rates[2]) / 1_000_000
    return row | {"estimated_standard_credits": value, "credit_rate_known": True}


def _aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], collections.Counter[str]] = collections.defaultdict(_metrics)
    for row in rows:
        for key in (*FIELDS, *ACTIVITY_FIELDS, "response_count"): grouped[tuple(row[key] for key in keys)][key] += int(row.get(key, 0))
    return sorted([_estimate(dict(zip(keys, key)) | {"model": (dict(zip(keys, key)).get("model", "mixed"))} | _plain(value)) for key, value in grouped.items()], key=lambda row: row["total_tokens"], reverse=True)


def snapshot(scan: UsageScan) -> dict[str, Any]:
    rows = []
    for (tid, model, effort), values in scan.cells.items():
        item = {"conversation_id": tid, "conversation_title": scan.threads.get(tid, {}).get("title", f"Conversation {tid[:8]}"), "root_id": scan.root(tid), "project": scan.threads.get(tid, {}).get("project", "unknown"), "model": model, "effort": effort, **_plain(values)}; rows.append(item)
    for tid in {row["conversation_id"] for row in rows}:
        group = [row for row in rows if row["conversation_id"] == tid]; activity = next((row for row in group if row["model"] == "unknown" and any(row[key] for key in ACTIVITY_FIELDS)), None)
        if activity is not None:
            anchor = max((row for row in group if row is not activity), key=lambda row: row["total_tokens"], default=activity)
            if anchor is not activity:
                for key in ACTIVITY_FIELDS: anchor[key] += activity[key]
                rows.remove(activity)
            if tid in scan.targets: anchor["peer_targets"] = [{"conversation_id": target, "title": scan.threads[target]["title"], "invocations": count} for target, count in scan.targets[tid].most_common() if target in scan.threads]
    bins = [{"start": when.isoformat(), "conversation_id": tid, "model": model, "effort": effort, **_plain(values)} for (when, tid, model, effort), values in sorted(scan.bins.items())]
    tasks = [{"id": tid, "title": scan.threads.get(tid, {}).get("title", f"Conversation {tid[:8]}"), "project": scan.threads.get(tid, {}).get("project", "unknown"), "root_id": scan.root(tid), "is_worker": tid != scan.root(tid), **{key: sum(row[key] for row in rows if row["conversation_id"] == tid) for key in ACTIVITY_FIELDS}} for tid in sorted({row["conversation_id"] for row in rows})]
    total = _plain(collections.Counter({key: sum(int(row.get(key, 0)) for row in rows) for key in (*FIELDS, *ACTIVITY_FIELDS, "response_count")})); models, model_effort, projects = _aggregate(rows, ("model",)), _aggregate(rows, ("model", "effort")), _aggregate(rows, ("project",)); estimate = sum(float(row["estimated_standard_credits"] or 0) for row in models)
    coverage = {"indexed_files": len(scan.candidates), "scanned_files": scan.cursor, "completed_candidates": scan.cursor, "total_candidates": len(scan.candidates), "can_resume": not scan.complete, "missing_files": scan.missing, "skipped_files": scan.skipped, "truncated_files": scan.truncated, "bytes_read": scan.bytes_read, "max_seconds": MAX_SECONDS, "max_bytes": MAX_BYTES, "max_indexed_files": MAX_FILES, "candidate_overflow": False, "budget_exhausted": not scan.complete, "complete": scan.complete, "notes": list(dict.fromkeys(scan.notes))}
    return {"collected_at": dt.datetime.now(dt.timezone.utc).isoformat(), "indexed_at": scan.created_at.isoformat(), "range": {"start": scan.start.isoformat(), "end": scan.end.isoformat()}, "coverage": coverage, "totals": total | {"estimated_standard_credits": estimate}, "credits": {"estimated_standard": estimate, "measured_allowance": None, "label": "Estimated standard credits — not actual Pro allowance debits", "unknown_models": sorted({str(row["model"]) for row in models if not row["credit_rate_known"]})}, "models": models, "model_effort": model_effort, "projects": projects, "cells": sorted(rows, key=lambda row: row["total_tokens"], reverse=True), "tasks": tasks, "bins": bins, "conversations": sorted(rows, key=lambda row: row["total_tokens"], reverse=True), "workers": [row for row in rows if row["conversation_id"] != row["root_id"]], "selected_view_groups": {"model": models, "model_effort": model_effort}, "activity": {"classification": "heuristic metadata classification; inbound wakes and causal cost are unknown without recipient provenance", "outbound_peer_calls": total["peer_message_invocations"], "child_calls": total["child_invocations"], "inbound_peer_wakes": "unknown", "causal_cost": "unknown"}, "qualification": ["Local telemetry is not account billing or quota usage.", "Reasoning output is included in output tokens and is never double-counted.", f"Deduplicated response_id globally across collected files: {scan.duplicates} ignored.", f"Embedded owner filter excluded {scan.foreign_records} copied or foreign records.", "Peer activity is proved from function calls and embedded functions.exec JavaScript only; tool outputs and descriptions are not counted.", "Inbound peer wakes and causal cost are unknown without recipient provenance.", "No prompts, response text, tool arguments, tool output, credential values, or recipient identities are returned."]}


def collect_usage(start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    scan = create_scan(start, end); advance_scan(scan); return snapshot(scan)


class CodexUsageSnapshotService:
    """Four frozen in-memory range snapshots; refresh resumes incomplete work."""
    def __init__(self) -> None: self._cache: OrderedDict[tuple[str, str], UsageScan] = OrderedDict(); self._lock = asyncio.Lock(); self._collect = asyncio.Lock(); self._inflight: dict[tuple[str, str], asyncio.Task[dict[str, Any]]] = {}
    async def read(self, start: dt.datetime, end: dt.datetime, refresh: bool = False) -> dict[str, Any]:
        key = (start.astimezone(dt.timezone.utc).isoformat(), end.astimezone(dt.timezone.utc).isoformat())
        async with self._lock:
            scan = self._cache.get(key)
            if scan and not refresh: self._cache.move_to_end(key); return snapshot(scan) | {"collection": {"state": "cached", "in_progress": False}}
            if scan and scan.complete and refresh: scan = None
            if scan is None:
                scan = create_scan(start, end); self._cache[key] = scan
                while len(self._cache) > MAX_CACHE_STATES: self._cache.popitem(last=False)
            task = self._inflight.get(key)
            if task is None:
                async def run() -> dict[str, Any]:
                    async with self._collect: await asyncio.to_thread(advance_scan, scan)
                    return snapshot(scan)
                task = asyncio.create_task(run()); self._inflight[key] = task
        try: return (await task) | {"collection": {"state": "resumed" if not scan.complete else "refreshed", "in_progress": False}}
        finally:
            async with self._lock:
                if self._inflight.get(key) is task: self._inflight.pop(key, None)


snapshot_service = CodexUsageSnapshotService()
