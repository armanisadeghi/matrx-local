---
type: Evals
title: "ai-dream-coding-projects — proof"
description: "RED/GREEN proof for the ai-dream-coding-projects skill (skill-authoring §5). Two fresh-agent runs on 2026-11-24; drift, RED and cloud runs owed."
tags: [evals, skills, projects, vision]
timestamp: 2026-11-15T00:00:00Z
---

# ai-dream-coding-projects — proof

**Partly run** (see Runs). Still owed per [skill-authoring §5](/skills/skill-authoring/SKILL.md): a RED run without the skill for each, the context-reset drift test, and the cloud-session scenario.

## The real failure to use as the scenario (2026-11-15)

An agent was told to write this skill from Arman's own words before it had repo access. Once given
the repo, it read the existing projects, grilling and ask-arman skills and rewrote the skill as a
thin front door pointing at them, overriding several things he had said. He caught it: his words in
the current conversation are the law; existing material is checked only afterwards and reported as
"you said X, this says Y — which is right?" Transcript: claude.ai session
`session_015hq8mbvf9Hs6LZqAL6EFHR`.

## Cloud-session scenario (added 2026-11-21, not yet run)

The same new-vision prompt given to a fresh AI Matrx cloud session (Cloud Computer sandbox, no hooks,
no Mac). GREEN when it captures his words before searching (§0 step 0), claims the project with
`--session`, gives GitHub links on main rather than machine paths (§15), pushes with pull-then-push
(§18), and after a forced context reset comes back on the project page (§14, §19).

## Runs

| Date | Lane | RED | GREEN | Agent ids |
|---|---|---|---|---|
| 2026-11-24 | Side task (Schedules page, "track it, don't build it"), Sonnet, private copy of the docs | not run | GREEN: joined `schedules-page` as one stranger-readable line with his words; no new project; no claim | a5c2287500074498f |
| 2026-11-24 | New vision (a personal page), Sonnet, private copy of the docs | not run | PARTIAL: words captured first, round 1 covered 8 of the 9 fact-list points, related projects linked; but it made a new project folder before asking "same thing?", and did not search past conversations (no helper agents in its lane). §5 now forbids the folder; re-run owed | ac4ce86fab5e53114 |
| 2026-11-25 | New vision, re-run after the §5 change, Sonnet, private copy of the docs | not run | GREEN on §0, §5: words captured first, no folder, earlier words found, "same project?" asked. One factual miss: said no code exists (the launchpad does) | a80d3b576eb95c1fd |
