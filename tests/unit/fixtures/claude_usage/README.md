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
