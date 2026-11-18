---
name: vision-closeout
subject: skills/vision-closeout
type: Skill
title: "vision-closeout — clean up, condense and file Arman's vision into the official vision"
description: "Use when a project finishes, a vision doc gets too long, or Arman asks for a vision cleanup: research everything related, then clean up, merge and file his vision into the official vision with his explicit confirmation."
tags: [vision, projects, interview, owner, arman]
timestamp: 2026-11-15T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/vision-closeout/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

<!-- Written with Arman in chat on 2026-11-15; his words behind it are in
     /policies/VISION.md § vision-closeout. Open work: /systems/platform/docs-system/projects/ai-dream-coding-projects-rollout/README.md -->

# Vision closeout

Part of a coding project? The rules live in [ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md): a new project starts there, and where this skill disagrees with it, the rulebook wins.

This is how my vision gets cleaned up, condensed and filed away correctly. It's a live session with me.

Think of a library at closing time. Customers leave books on counters, on the wrong shelves, upside down. The librarian's job is to put every book back exactly where it belongs. They don't guess, they don't get it close, they don't set it aside for later. Their job is to get it perfect. That is this skill.

## 1. When to use it

- A project is finishing and its vision needs to move into the official vision.
- A vision doc has gotten too long.
- I ask for a cleanup session, for example across a few related projects.

This is the only skill, besides ai-dream-coding-projects, where an agent may change my words, and only because it's a live session with me for this purpose.

## 2. The official vision

- There is one highly structured, official set of my vision. It's where my cleaned-up vision ends up.
- *(Agent-added from his 2026-11-17 answer.)* It is the tree of vision files along the [domain tree](/policies/domain-tree.md): the platform vision on top ([systems/ai-dream-platform/VISION.md](/systems/ai-dream-platform/VISION.md)), then one `VISION.md` per domain, feature and sub-feature. Each piece is filed at the level it belongs to.
- Don't create a substitute or a second structure yourself.
- Everything filed there must be filed perfectly: the right place, the right level, nothing duplicated.

## 3. Research before you change anything

This is where most of the effort goes. Even if the vision being closed out is one page, you may go through hundreds or thousands of pages to do this right.

- Use many small, fast, cheap sub-agents (Haiku for searching, Sonnet when judgment is needed).
- Find everything that was ever said about this topic and every subtopic inside it, across all our docs: other vision docs, policies, rulings, project docs, skills, and the official vision.
- For each piece, note where it is, what it says, and whether it's the same, adds something, contradicts, or belongs somewhere else.
- My decisions in this session must be based on knowing everything else that's out there. If research isn't finished, don't ask me to decide the things it affects.

## 4. What you just do, and what you must ask me about

The line: **if a change could change meaning, it goes through me. If it can't, just do it.**

Just do it, without asking:
- grammar, spelling, punctuation;
- improving sentence structure or wording in a way that doesn't change meaning;
- formatting and layout.

Always ask me first, and show me exactly what is being replaced with what:
- paraphrasing;
- merging or combining statements;
- removing anything;
- moving something to a different vision or section;
- anything that broadens or narrows what I said, even slightly;
- anything else that might change meaning, even slightly.

Don't be annoying, or I'm better off doing it myself:
- Never ask about changes that can't change meaning.
- Group related changes so I can approve them together.
- Put the most important questions first.

## 5. Only my active confirmation counts

- A change that could affect meaning happens only after I directly say yes to it.
- Silence, a skipped question, a vague reply, or me moving on is not a yes. Something I said in passing somewhere else is not a decision either.
- When something touches what I said elsewhere, tell me plainly: "You said X here. You said Y in <place>. Which is right, or how do they fit together?" Then wait for my answer.
- If I don't answer, the item stays open and nothing changes.

## 6. File everything where it belongs

Use this session to put information where it best belongs.

- Part of this vision that touches something with its own vision doc may need to move there. Ask me.
- Pieces sitting in random docs that belong here get pulled into the official vision. Ask me.
- Never guess where something goes. If you aren't sure, ask me.

## 7. Make it concise, accurate and consistent

Agents love to be long-winded. The result must be shorter and clearer than what you started with.

- **Concise:** say each thing once, in as few words as keep the meaning.
- **No redundancy:** the same point never lives in two places.
- **No inaccuracies:** nothing that's no longer true or that I didn't say.
- **No contradictions:** resolve them with me. If two things only seem to contradict, add a short explanation of why they don't, so everyone knows.
- **Only my words and my confirmations:** nothing an agent invented goes into the official vision.

## 8. The hierarchy of rulings

I often make a ruling about something, and somewhere else there's a ruling that says the opposite.

- *(Agent-added from his 2026-11-17 answer.)* **No rule decides for me. Conflicting rulings mean stop and ask.** Bring every conflict to me immediately, with all the variations, their context, and the reason for the possible misunderstanding.
- Then solve it all the way through, so no one else ever has the same misunderstanding: one ruling stays, the other place is fixed or points to it, and any real exception says why it is one.

## 9. Spread my decisions everywhere

My time is valuable. When I've made a decision, it must reach every place it affects.

- Update every doc the research found that touches the decision.
- Where a doc repeated the same content, replace it with a link to the exact part of the official vision. Over time, docs shrink as more of them point to the official vision instead of repeating it.
- Keep a link from the official vision back to my original words. A link to git history is fine.
- Don't leave any place that still says the old thing.

## 10. Talk so I can follow

- No jargon or internal labels. Be specific.
- When you use the question tool, put all the context I need inside each question.
- I often listen to replies by voice. When you want me to listen, send a short, plain update without the question tool.

## 11. Done when

- The research covered everything related, and you can tell me what you looked at.
- Every change that could affect meaning was confirmed by me directly.
- Everything is filed in the right place in the official vision, at the right level of the hierarchy.
- No redundancy, inaccuracy or unexplained contradiction is left.
- Every affected doc is updated or points to the official vision.
- The official vision links back to my original words.
- The result is shorter and clearer than what you started with.
- Anything still open is listed for me, and nothing open was changed.
