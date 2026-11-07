---
name: handoff
subject: skills/handoff
type: Skill
title: "handoff — moving a project from one lead agent to the next so it gains instead of loses"
description: "Moving a project from one lead agent to the next: the outgoing lead's wind-down and handoff, and the incoming lead's takeover. Use when credits run out, a lead is replaced, on 'hand this off', 'take over X from its handoff', or /handoff. NOT for leading while the owner is away (use take-command)."
tags: [coordination, handoff, delegation, continuity, fleet]
timestamp: 2026-11-02T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/handoff/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# handoff

A project changes lead for one of two reasons: the account ran out of credits, or the work stalled
and Arman is cutting his losses. Either way, nothing the old lead knew may be lost — and done right
the project **gains**, because a fresh lead sees what the old one missed. Two sides share one
document: the outgoing lead writes it, the incoming lead tests it and takes over.

## Which side are you — read only your file

| You observe | You are | Read |
|---|---|---|
| You have been leading this project in this conversation and are told to hand it off | Outgoing | [outgoing.md](outgoing.md) |
| You are told to take over a project, or are handed its HANDOFF.md | Incoming | [incoming.md](incoming.md) |

Proof record: [evals.md](evals.md) (only when editing this skill).

## Outgoing modes — chosen from his words, never from your own judgment of your work

| His message says | Mode |
|---|---|
| nothing beyond "hand this off" (credits running out, a planned change) | **Normal** — wind down sub-agents with unsaved work, then the full handoff |
| it stalled, wasn't working, you're being replaced, cutting losses | **Stalled** — the same, written as a record rather than advice |
| emergency, almost out, no time, quick | **Emergency** — secure the work, then sections 0–3 only |

## Where it lives

`HANDOFF.md`, frontmatter `type: Handoff`, in the project's home node under `common-docs/systems/`
(the [domain tree](/policies/domain-tree.md); the folder holding its VISION.md). A project that is one
of several inside its node uses `projects/<project-slug>/HANDOFF.md` there. One per project,
**rewritten in place** — never appended; git holds history. Raw evidence (his extracted words, long
lane notes) is a work product, not a doc: store it with
[platform work products](/systems/files/file-service/STATE.md#platform-work-products), collection
`handoff-<project-slug>`, and link it. When storage is unreachable from this session, commit it as
`.md` beside HANDOFF.md with a first line saying it is deleted once absorbed, and say so in the header.
A session with no common-docs checkout writes `docs/handoff/<project-slug>/HANDOFF.md` in its repo.

## The handoff document — same sections for every mode

Section order is priority order: an outgoing lead that gets cut off has still written the parts
nothing else can recover. Every claim is marked **checked** (you saw the result yourself during the
project — name the commit, link or moment; "I wrote it" is not a check; a wind-down adds no new
checks), **believed** (someone said so), or **guessed**.

0. **Header.** Project; outgoing lead (session id or link, account); date; mode (normal / stalled /
   emergency) and why; links to every conversation where Arman shaped this work; the project's
   state in one sentence.
1. **His words.** Link to the extraction file of everything he typed (outgoing §3) and to the
   VISION.md section holding his rulings and corrections, verbatim and dated — never a summary of
   them. Never re-quote him in the handoff
   ([docs rule 7](/policies/document-types.md)); list which of his corrections the work has not
   yet honoured.
2. **Where it stands.** Table: piece · state (working / unproven / half-built / broken / not
   started) · evidence (live link, commit, how it was checked) · checked / believed / guessed.
3. **Lanes and in-flight work.** Table, one row per sub-agent or session: what it was doing · where
   its work is (main / named branch / local paths) · state · its exact next step · how to reach it.
   Plus any platform schedule the project owns, and anything running or waiting on someone. **Each row stands alone** — the next
   lead may hand a row to a new agent as its whole brief.
4. **Decisions.** Table: decision · made by (Arman, quoted ruling linked / the lead) · why · cost if
   wrong. A lead's decision is open to challenge; his is not.
5. **What was tried.** Each approach: what was expected, what happened (exact error or result), what
   followed. Dead ends included — they are the most valuable rows.
6. **Not written anywhere else.** Traps, things that look wrong but are deliberate, surprises in the
   code or data, dependencies nobody would guess.
7. **What's left.** Remaining work in order; what you would check first starting fresh; where you
   are least sure you got it right. Stalled mode adds **Why it stalled** and **Theories** (labeled:
   the next lead tests them, never follows them).
8. **Takeover line.** The exact sentence Arman pastes to the new lead, with the handoff's real path —
   never a placeholder. A lead that cannot place it under `common-docs/systems/` uses the repo fallback
   above.

## Principles

- His words beat any summary of them; the live product beats any document.
- A wrong "done" costs more than an honest "unsure".
- Unpushed work does not exist for the next lead.
- The handoff is a map for the next lead, never proof; the next lead re-checks every line.
- Every lead assumes it will be handed off too, and keeps HANDOFF.md current from day one.
