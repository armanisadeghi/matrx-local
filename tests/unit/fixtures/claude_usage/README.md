# Claude Code transcript fixtures (real records)

Real Claude Code session transcripts copied from `~/.claude/projects/` on
2026-09-17 (lane CS-24). Every record, field and number is as Claude Code wrote
it — record types, timestamps, `requestId`, `message.id`, `message.model`,
`message.usage` — so the usage reader is tested against the real shape,
including the streamed duplicates (one message, many lines).

The ONE change: string values longer than 24 characters outside the structural
fields (ids, timestamps, model, cwd, version…) are replaced by
`[redacted for the fixture]`. Prompts, replies, tool arguments and tool output
are Arman's and do not belong in a repository; the usage numbers do not depend
on them.

`97ce06fb-…jsonl` (added 2026-09-18, lane CS-33) is an EXCERPT of one real session,
not a whole one: Claude wrote the same message at priced assistant line 682 and again
at line 1530 of the real 52.6 MB transcript, with 65 other messages in between. The
excerpt keeps both of that message's multi-line blocks exactly as Claude wrote them
plus the first line of each message in between, so a unit-test-sized file still has
more than 64 distinct messages between the two occurrences — the shape a dedupe
bounded by a window of recent keys gets wrong. Same redaction as above.

## `subagents/` (added 2026-09-18, lane CS-33/F5)

Real sub-agent turn streams, in the exact tree Claude Code writes them in:

```
subagents/<parent session uuid>/subagents/agent-<id>.jsonl
subagents/<parent session uuid>/subagents/workflows/wf_<id>/agent-<id>.jsonl
```

The directory a stream is nested under IS the parent session, and every record
in the file repeats that id in its own `sessionId` field — the guard asserts
the two agree rather than trusting the path. `97ce06fb…/subagents/agent-a3e5…`
is a real sub-agent of the `97ce06fb…` transcript above.
`6ebeb164…/subagents/workflows/wf_938b608a-3d9/agent-a9b417…` is nested one
level deeper (a workflow's sub-agent) and its parent's own transcript is
deliberately absent, so the guard proves a sub-agent's spend is attributed to
its session even when nothing else about that session is on disk.

Same redaction as above. Both files keep Claude's streamed duplicate lines
(10 lines / 5 messages and 9 / 4), so the per-session dedupe is exercised.
