---
type: Reference
title: "handoff — the incoming lead"
description: "What the lead taking over a project does: test the handoff against the owner's words and the live product, bring fresh eyes, report and get grilled, plan and get attacked, build with verified lanes, keep the handoff current. Companion to the handoff skill."
tags: [handoff, coordination, takeover]
timestamp: 2026-11-02T00:00:00Z
---

# The incoming lead

From this moment you own the project end to end: the outcome, the plan, the team and the quality of
what ships. You are not here to continue the previous lead's path; you are here to get it right. The
handoff is a map drawn by someone else — it says where to look and proves nothing.

## 1. Read the handoff

First claim the project and dispatch the vision check new hands require
([projects §1](/skills/projects/SKILL.md), steps 2 and 3); its result is evidence for step 3 below.

Use the path Arman gave, or find it by the project's name: `rg -l '^type: Handoff' common-docs/systems`.
Read all of it. A handoff not in the standard sections (pasted text, an older format) is read the
same way; the sections it lacks are what step 3 must fill. Note the mode in its header. **Stalled** → assume it carries the blind spots that
stalled the work, and find out why it stalled. Sections 4 (decisions) and 7 (theories) are where you
push back hardest.

**Done when** you can name every claim the handoff makes that you will need to check.

## 2. His vision, in his words

Read the extraction file and the VISION.md the handoff links. When his original conversations are
reachable from here, extract them yourself with [user-ground-truth §1](/skills/user-ground-truth/SKILL.md)
— never rely only on the outgoing lead's selection, least of all a stalled one's. Then the fresh
summary by a `quick` lane (user-ground-truth §2), never by you. Label every decision he did not make as the previous lead's, and mark
every place the work drifted from what he asked.

**Done when** a summary of his words exists with drift and lead-made decisions marked.

## 3. Get the truth

Dispatch lanes ([subagent-dispatch](/skills/subagent-dispatch/SKILL.md); `researcher`, `standard`,
`browser-tester`) to establish reality: the live product used as a user would, the code, the
database, git history, and every branch in the lanes table. Each lane gets the handoff's claims as
**claims to check**, never as facts, and none of your suspicions — not what you expect the answer to
be, not what you think he never asked for ([on-track §3](/skills/on-track/SKILL.md)).
Each returns evidence, not opinion. Assemble one picture: works today (with link) · built but broken
or unproven · half-built · not started · every place the handoff was wrong.

Unfinished work you find on branches stays off main until it is verified. Before the plan, fix only a defect whose cause a lane confirmed, whose fix sits in one place, and that
no earlier attempt failed to fix. Everything else — including any bug the previous lead already tried
to fix — goes into the plan. Nothing is called fixed or live to him until someone other than the fixer
has checked it on the live product.

**Done when** every claim in the handoff is confirmed, refuted with evidence, or marked unverifiable.

## 4. Fresh eyes

A separate section of your report: what the previous lead missed, what rests on a wrong assumption,
what is harder or easier than they thought, and whether a better approach exists. A takeover that only
preserves knowledge has failed; this one has to add some.

**Done when** the section exists with at least one finding the handoff did not contain, or says
plainly that none was found and what was checked.

## 5. Report, then get grilled

In chat ([talk to Arman like a person](/policies/talk-to-arman-like-a-person.md)): where the project
stands today first (not what changed), then the remaining work, the discrepancies, and your fresh-eyes
findings. Then put every question you could not answer yourself to him through
[grilling](/skills/grilling/SKILL.md) and the question tool, your recommendation first on each, round
after round until no real question is left.

**Done when** no open question remains and his answers are recorded in VISION.md.

## 6. Plan, attack, approval

Write a plan that finishes the whole project, not just the next step. Run
[plan-attack](/skills/plan-attack/SKILL.md) with an agent that did not write it. Fix what holds up.
Bring him the final plan and what the attack changed. **Hold point: nothing that changes direction is
built until he approves.**

**Done when** he has approved the plan.

## 7. Build with an army, verify everything

Run the build under [campaign-pattern](/skills/campaign-pattern/SKILL.md) Part 2: clear lanes in
parallel (a row of the lanes table can be a lane's whole brief), each finished piece checked on the
live product by someone who did not build it before it counts, and updates to him as pieces land —
what works now, with links, and anything that needs him.

## 8. Keep the handoff living

As soon as step 3 finishes, rewrite HANDOFF.md in the standard sections from what you found; from then
on rewrite it in place each time a lane lands or a decision is made, and
delete the stored evidence once you have absorbed it. When your turn to hand off comes, the handoff is
already written; you only bring it up to the minute.

**Done when** HANDOFF.md matches what you would tell a successor right now.
