# Claude Code transcript fixtures (real records)

Three real Claude Code session transcripts copied from `~/.claude/projects/` on
2026-09-17 (lane CS-24). Every record, field and number is as Claude Code wrote
it — record types, timestamps, `requestId`, `message.id`, `message.model`,
`message.usage` — so the usage reader is tested against the real shape,
including the streamed duplicates (one message, many lines).

The ONE change: string values longer than 24 characters outside the structural
fields (ids, timestamps, model, cwd, version…) are replaced by
`[redacted for the fixture]`. Prompts, replies, tool arguments and tool output
are Arman's and do not belong in a repository; the usage numbers do not depend
on them.
