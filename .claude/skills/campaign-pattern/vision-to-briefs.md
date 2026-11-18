---
type: Reference
title: "campaign-pattern — from a ratified vision to fleet briefs"
description: "The build half of the former vision-to-fleet skill: reality audit, competitive research, infrastructure groundwork, decomposition into blind-executable briefs, and fleet assignment. Read from campaign-pattern once the vision is captured under ai-dream-coding-projects."
tags: [campaign, briefs, research, fleet]
timestamp: 2026-11-17T00:00:00Z
---

# From a ratified vision to fleet briefs

Formerly the `vision-to-fleet` skill (2026-07-07). Its vision-capture steps now live in the rulebook,
[ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md): capture his words first, interview him through
[ask-arman](/skills/ask-arman/SKILL.md), and never let research edit the vision by itself. This file is the
build half, used from [campaign-pattern](/skills/campaign-pattern/SKILL.md) after the vision exists.
Project docs stay forward-looking with no changelog (rulebook §3); a superseded doc gets a pointer to what replaced it.

## Standing rules

1. **Ground everything in the live system.** Claims about code cite file paths; claims about data cite live
   row counts. A doc claim older than a few days is stale: many sessions land on main daily.
2. **Compare the code to the vision, never the vision to the code.** The current implementation is evidence of
   what exists, never of what was intended. When code and vision disagree, the code is wrong unless the
   evidence genuinely cuts both ways.
3. **Fleet-aware git hygiene.** Other sessions are working on main right now. Stage only your files by explicit
   path, small commits, never stash someone else's work.
4. **This is an AI-application company.** In every step, "where do agents plug in" is a first-class design question.

## Phase 2 — Reality audit (brownfield)

Produce the current-status summary that every later claim rests on. Method that worked:

1. Fan out parallel **Explore** agents, one per suspected subsystem, each with a precise
   question sheet ("is X still a stub? which of these six items shipped? what does the
   uncommitted diff in Y do?"). Ask for file paths in every claim.
2. Yourself, in parallel: live DB truth — `information_schema` sweep of the relevant schemas
   with row counts; sample ambiguous tables. **Row counts are gold**: "table exists, 0 rows,
   queried nowhere" is a completely different fact from "table exists."
3. Chase every anomaly to a verdict. A mystery table (education's `quiz_sessions`) gets its
   columns read, its data dated, and its writers grepped — until it's *resolved*, not flagged.
4. Check git motion since any prior audit: `git log --since=<date> -- <paths>`. In-flight work
   by other agents becomes an explicit "owned elsewhere — do not absorb" list.

Output (section in the plan doc, not a separate file): **DONE** (real, verified — the assets to
build on), **IN FLIGHT** (owner named), **MISSING** (what decomposition will cover), plus
resolved-anomalies and open flags. Also fix trivial drift you find (a stale "coming soon" label)
in the same pass — leaving known-wrong metadata is negligence.

## Phase 3 — Competitive research

Full mechanics: [research-playbook.md](research-playbook.md). The
essentials:

- **Benchmark against the best at the FUNCTION, never against AI-engine peers** — the rule and
  the competitor-selection method are `research-playbook.md` step 1; the standing
  champion set is `common-docs/policies/champions.md`. Getting this framing wrong invalidates
  the whole phase.
- **~8–10 parallel deep-research passes, one per competitor/cluster**, each mining real user
  sentiment (Trustpilot, app-store reviews, Reddit, G2, feature boards, news, academic papers,
  regulatory records). What do people LOVE, what do they HATE, what did the company fumble.
  Cited claims only; self-reported marketing numbers flagged as such.
- **Visual capture (was missed on education — do not skip again):** browser-agent passes that
  screenshot each competitor's key flows (onboarding, the hero feature, paywall, empty states)
  into a `research-assets/<competitor>/` folder with an index. These feed the design step.
- **Design pass (also previously missed):** for every surface the plan will build, a design
  direction informed by the screenshots — what the best-in-function get right visually, what we
  adopt/reject — so build-phase agents inherit direction instead of inventing it. Use the
  ui-* / web-design skills.

Synthesize into a `COMPETITIVE_INSIGHTS_AND_REPRIORITIZATION.md` (template in templates.md):
strategic thesis first, ranked market wants with signal counts, per-competitor
nails/hated-for/one-thing-to-take table, cross-cutting themes, then **proposed vision
elevations/additions clearly marked FOR APPROVAL** — research never edits the vision by itself.

## Research findings reach the vision only through him

Proposed elevations from research go to Arman as recommendations, through [ask-arman](/skills/ask-arman/SKILL.md).
On approval, his words go into the vision in each item's home section. Deferred items get a real brief marked
"Wave 2, do not assign", because "later" without a document means "forgotten."

## Phase 5 — Infrastructure groundwork

Before decomposing, decide the platform layer so N agents don't invent N solutions:

- **Data model:** the canonical content-model pattern per entity family (base columns,
  associations edges, visibility, RLS via the canonical generator, registry entries). New
  tables go through the `db-change` skill family. Name the pattern-reference implementation
  each brief should copy.
- **Server/client split per the data-flow doctrine (CLAUDE.md):** browser ↔ Supabase direct for
  all pure data ops; Python only for real server needs (bytes, signing, heavy compute,
  auth/anon boundaries). Every brief states which side each piece lives on; cross-repo
  (aidream) work is *part of a project*, never a follow-up ticket.
- **Agent integration points (standing rule 4):** enumerate every AI touchpoint; each becomes "author
  agent via agent_author → wire via the launch/streaming pattern" work inside some brief, with
  the agent-spec doc named.
- **Day-1 contracts:** identify every interface where two projects would otherwise block on
  each other (access gates, entitlements, trust envelopes, converters, data contracts). Each
  gets an owner, a typed signature, and the rule: **publish the signature (stub if needed) on
  day 1** so consumers wire call sites immediately. This is what makes full parallelism real
  instead of aspirational.

## Phase 6 — Decomposition into briefs

The milestone deliverable: a briefs folder (`docs/proposals/<area>-projects/`) containing a
MASTER PLAN (README.md), one brief per project, an ASSIGN.md prompt, and F-addenda for agents
already in flight. All templates + the sizing rules: [brief-templates.md](brief-templates.md).

Sizing rules (Arman's, verbatim in spirit): each project is a **substantial, coherent body of
work** — a full subsystem or vertical slice, never a task list; prefer **fewer, larger**
projects (merge anything knockable-out-quickly into a neighbor); **maximize parallelism** by
pushing every cross-dependency into a Phase-5 contract. Small tasks → one agent overseeing a
group; large tasks → one agent each. Work already owned by a live agent goes in an **F-addendum**
to that agent, with hand-offs in/out stated explicitly. **Split test:** split two pieces into
separate projects only where a reviewer could reject one while approving the other; fold setup,
config, scaffolding, and docs into the project whose deliverable needs them.

A wide refactor (one mechanical change across many callers) is sequenced expand → migrate in
batches → **contract**. The contract project is mandatory, blocked by every migrate batch, and in
the same wave; never "later".

Every brief must pass the blind-handoff test: *could a strong agent with only this brief + the
repo ship the right thing?* If any section makes you think "they'll figure it out," it fails.
The test is **run by a fresh reviewer per brief plus one cross-brief contracts reviewer via
`plan-attack`**, never by the brief's author, before remaining flags go to Arman. A brief
**fails automatically** if it contains TBD/TODO, "handle edge cases", "add appropriate error
handling", "similar to P<n>", or a name or type defined in no brief or contract.

Before assignment, write the collision table into the master plan: one row per pair of briefs that
share a file, table, or interface (what one produces vs. what the other consumes → the Phase-5
contract that settles it), and one row per brief checking it agrees with itself (tests it demands
vs. code it specifies). "No conflicts" without the rows is not a scan you ran.

Map **convergence points** (integration milestones with their own DoD), **fan-out** (what each
convergence unlocks), and **waves** ordered by vision impact. Then surface every remaining flag
to Arman through [ask-arman](/skills/ask-arman/SKILL.md), record his answers back into the docs, and commit.

## Phase 7 — Assignment & fleet operations

- Arman assigns via ASSIGN.md's copy-paste prompt — fresh session per project; F-addenda go to
  the owning agent's existing session. Track assignment state in ASSIGN.md's table.
- While the fleet runs, the master plan is living: decisions recorded, contracts updated when
  an owner ships the real thing, kickoff addenda added when concurrent work lands on a brief's
  turf (see P7's share-links addendum for the shape).
- Your own remaining moves: answer escalations fast with evidence, keep docs truthful, and at
  each convergence run the integration audit its DoD defines.

## Done means

Vision ratified and current · reality audit grounded every claim · research synthesized with
visuals + design direction · infrastructure contracts published · every brief blind-executable ·
flags asked and answers recorded · everything committed · Arman pasting assignment prompts.
