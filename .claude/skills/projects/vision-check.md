---
type: Reference
title: "projects — the vision check"
description: "How a project is measured against the owner's words by an agent that did not build it: who runs it, the brief, and the file the checker itself commits."
tags: [projects, vision, verification]
timestamp: 2026-11-14T00:00:00Z
---

# The vision check

The agent in the first conversation still holds his words, so it sees the distance at once. Every
later agent holds a plan instead, and measures against that. The check rebuilds the first agent's
position: his words, the live product, and nothing else.

## Who runs it

Never the lead, a lane of the build, or any session that has read the project's plan, register or
handoff. Whoever needs one (a lead, the sweep) dispatches one fresh `standard` lane and sends it
only the brief below. The checker writes and commits the result itself; the dispatcher never types
the result anywhere.

## The brief: send this and nothing more

```
You are checking one project against what its owner asked for. You did not build it and you are
not told what the builders think. Do not look for it.

Read <the file --project lists under "his words"> in full. It is everything the owner said. Open
no other file in <project folder> except VISION-CHECK.md, which you will write.

If the file has a section "The bar", that numbered list is what must be true. If it has none, make
the list yourself: line 1 is why this exists (his earliest statement of what he wants), then one
line per thing he said must be true, each a verbatim excerpt with its date.

For each line, go to the live product as a person would and to the live data, and do the thing the
line describes. A page that exists is not "met"; the line is met only when what he described
happens. Sign in as the agent member: `pnpm dev-login` on a machine running the local site; in a
cloud container take a member session from `scripts/shared/matrx_session.py test-token` and set it
as the site's sign-in cookie in Playwright. The agent admin only for admin pages. Never his account.
Create nothing you do not remove. A line that can only be judged in code is judged in the code and
the live database, and marked so.

Write <project folder>/VISION-CHECK.md, replacing any earlier one:

  ---
  type: Review
  title: "<project> — vision check"
  description: "An independent check of the live product against the owner's words."
  checked: "<today, YYYY-MM-DD>"
  core: "<met | partly | not>"        # line 1 only
  met: "<lines fully met>"
  of: "<lines in the list>"
  bar: "<confirmed | proposed>"       # proposed when you made the list yourself
  verdict: "<one sentence: what he would say if he opened it right now>"
  ---
  then the list: number · his words · met / partly / not · what you saw and where.
  Then anything in his words the list leaves out.

Commit only that file: message `vision check: <slug>`, with the lines `Project-Board: check` and
`Project: <slug>`. Push. Report the same content back.
```

## What happens to the result

1. The board shows it from the file: date, the verdict on line 1, the count, the sentence. Nothing
   is copied to the card.
2. Every line not met goes to the top of the project's open items, ahead of what the plan had next,
   and `next` on the card becomes the first of them.
3. The lead tells Arman in chat: the verdict on line 1 first, then each miss in a plain sentence,
   then what changes because of it. The checker's report is not edited, softened or argued with in
   that message. A lead that believes a line is wrongly judged says so separately and asks him.
4. A list the checker made (`bar: proposed`) is shown to him as the proposed bar; it goes into the
   vision file only as he confirms or corrects it.
5. A gap between the bar and the direction of the plan is not the lead's to settle: it is a question
   for him ([ask-arman](/skills/ask-arman/SKILL.md)).

For a full review with a remedy plan and the offer to fix it, run [on-track](/skills/on-track/SKILL.md).
Its judges are also shown the builders' claims in order to test them; this check withholds them.
