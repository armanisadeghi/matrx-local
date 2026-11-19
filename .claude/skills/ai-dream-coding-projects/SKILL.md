---
name: ai-dream-coding-projects
subject: skills/ai-dream-coding-projects
type: Skill
title: "ai-dream-coding-projects — run a new project or vision with Arman"
description: "The rulebook for every coding project with Arman, and the map to the how-to skills. Use when he brings a new project or vision, you start, continue or resume a project, gather the projects on a topic into one, report where a project or its lanes stand, or a how-to skill sends you here."
tags: [projects, vision, interview, owner, arman]
timestamp: 2026-11-17T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/ai-dream-coding-projects/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

<!-- Written with Arman in chat on 2026-11-15 and approved by him; his words behind it are in
     /policies/VISION.md § ai-dream-coding-projects. The quick mode in §2, the §17 skill map, the
     back-links in §4 and §7, and the 2026-11-17 §15 bullets were added by an agent from his
     2026-11-17 words (same VISION.md section), pending his review. The §14 "every turn" steps were
     added by an agent on 2026-11-18, approved by him in chat that day. The §4 fact list and §5a were added by an agent on 2026-11-18 from
     his words that day (same VISION.md section), pending his review. Open work on this skill:
     /systems/platform/docs-system/projects/ai-dream-coding-projects-rollout/README.md -->

# AI Dream coding projects

This is how I want every new project run with me. It is the model for how all projects are done from now on. How well you draw out my vision matters most.

Project docs live in the `common-docs` folder of the AI Dream repo (`AI-Matrix-Engine/aidream`).

**This is the rulebook for every coding project.** It holds the rules. The how-to skills it links
to (the skill map, §17) say exactly how to do each part, and every one of them points back here. If
you arrived through one of them and are told to start a new project, start here.

## 1. My words today are the law

There are two separate modes. Never mix them up.

- **New vision mode (this skill).** I come to you with a new or current vision. What I say in this conversation is the most important thing and it is the law. Capture it first, as I said it. Do not go looking for what already exists and let that dilute, replace, or redirect what I'm telling you.
- **Search mode.** I ask you to find what I've said before, or to find an existing project to add this to. Only then do you start from what already exists.

If you find something that seems better or similar, never abandon what I asked for. Something in the code base that claims to cover this probably doesn't work. If it did, I wouldn't be bringing this to you.

## 2. Write the vision first

- Start a vision document that says exactly what I said I want. Take what I said and use that as the vision as much as you can.
- Because this is a long back-and-forth with clarifications, you may clean up my words when you use this skill. Keep my meaning. Talk with me about any clean-up that might change what I meant. (Outside this skill, agents may not change my words at all.)
- If you suggested something and I clearly agreed, add it to the vision.
- Don't present your own ideas as mine. They stay as questions until I agree.
- *(Agent-added 2026-11-17 from his words.)* A vision is never agent-written on its own, but it can be a collaboration: in an extended vision session, what we build together and he agrees to goes in, each agreed line marked `(agreed YYYY-MM-DD)`.
- End the vision with a numbered list of everything the vision says must be true, so progress can be measured against it (§7).
- Words I said in earlier conversations are pulled out by script, never from memory: [user-ground-truth](/skills/user-ground-truth/SKILL.md).

**Quick mode: an existing area with no vision yet.** *(Agent-added 2026-11-17 at Arman's request, from the former domain-vision-interview skill; his 2026-08-20 words are in [docs-system VISION.md](/systems/platform/docs-system/VISION.md). Not the Masterwork Vision Interview product feature.)* One area per sitting, big picture only. Do the homework first: read the area's docs and settled rulings so you never re-ask. Then two open questions: the **minimal vision** ("what must this have before we can go live?", chased until it is a concrete, finite list; gently ask of anything big "is that needed to go live, or is that the destination?") and the **final vision** ("when this is fully grown, what is it?"). Capture details he volunteers; don't chase details he doesn't. Write both into the area's `VISION.md` in his words, dated, with anything inferred marked `(inferred)`; clear `VISION MISSING` wherever the area carried it; and mark which of its open tasks block go-live and which serve only the final vision. That split is what makes gap analysis possible.

## 3. Look forward only, then delete

- *(Agent-added 2026-11-17 from his words.)* **All work is a project**, run under these same rules and tracked centrally on the project board. It lives in a `projects/` folder inside the home it belongs to in `common-docs` (a social media project lives in the social media folder). Only work that cannot have a permanent home gets a folder under the top `projects/`. When it is done, what remains is the updated code and the docs of the features it changed.
- The project's detailed to-do list lives in the project doc itself.
- *(Agent-added 2026-11-17 from his words.)* A project for one portion of the system lives in that portion's folder (rebuilding the agent UI lives in the Agents folder).

## 3a. Converge as you go

*(Agent-added 2026-11-17 from his words.)* Whenever you touch a project, take the chance to move what you touch closer to one shared flow: docs, routes, code and menus that follow the same structure (the [domain tree](/policies/domain-tree.md)). A project folder in the wrong place, a route or doc that disagrees with the tree: fix it in the same change when it is small, or add it to the project's to-do list when it is not.

- The project doc is not a history. Don't record what was built or how long it took.
- Anything finished, even months of work, becomes one short line saying that feature is live.
- The goal is to delete the project doc as fast as possible. As things are done, take them out. In the end only the vision remains.
- What the doc must keep is a detailed to-do list showing where the vision isn't fully live yet.
- What happens to my vision once a project closes is a separate process (§13).

## 4. Grill me

- Ask as many questions as you need until you have the full vision.
- Start with what I've already said. Then think about what I may not have said and ask me about that.
- **Don't force big decisions on the spot.** Always give me a way to not address something right now when I know it's not as important to handle yet. I don't want the vision messed up because you forced a quick answer when I wanted to wait and do it right.
- **A question I skip stays open.** It never defaults to your recommendation unless I say so for that question. Write it down as open and bring it back later.
- Keep asking until you genuinely understand it.
- Open-ended vision questions get the most out of me. Ask them in plain chat.
- Never ask me a question in place of giving me your recommendation (§15).
- How every question is asked, gated and recorded: [ask-arman](/skills/ask-arman/SKILL.md).

**What to find out before the vision is written** *(agent-added 2026-11-18 from his words: "agents are still not really working hard to get all of the facts from me")*. Cover every one, from what he has said or by asking. Skip none because it seems obvious:

1. Who uses it, and what they do today without it.
2. The moment it works: what he sees, step by step, when it is done.
3. What it must never do, and what would make him reject it.
4. What he has seen that is close (another product, a page of ours), and what is wrong with it.
5. What we have now that he is unhappy with, and why.
6. What must be true to go live, and what is the final destination.
7. How it connects to other parts of the platform, and what it depends on.
8. What each organization should be able to decide for itself (knobs, law 6).
9. Order: what matters first, and what can wait.

Every round ends with what you still don't know, as a short numbered list. You leave this section only when he says the list is complete or says "enough for now". Then the unknowns stay as open questions in the vision. Reading the code or docs to ask better questions is allowed at any time, but what you find never replaces asking him (§1).

## 5. Decide: is this a new project?

One of the biggest mistakes is launching something as a new project when it already exists. This applies to the first project and to every project that comes out of the conversation.

- Once my words are captured, search for current projects before creating a new one.
- Some projects will be identical or almost identical. Bring those to me and ask "Are these the same thing?" Make sure they genuinely are. Don't get in the habit of bringing me random things that aren't connected.
- Whatever you find, my words today still lead (§1). Never fold my new vision into an old project without asking me.

## 5a. Gather the projects on a topic

*(Agent-added 2026-11-18 from his words: "look for all of the projects that relate to a topic and bring them together, or start a new one when one doesn't exist.")* This is search mode (§1). Nothing is lost.

1. **Search everywhere work waits:** the project board (`project_board.py`, every `projects/` folder), node task lists (`operations/tasks/`), each related node's VISION.md and STATE.md, and `inbox/`. Use background Haiku agents for the sweep. Also search his earlier words for the topic ([user-ground-truth](/skills/user-ground-truth/SKILL.md)).
2. **Bring him one numbered list:** each project or task found, one line on what it is, how done it is (§7), and your recommendation: the same thing (merge), part of it (absorb, §6), related (link, §9), or not related (dropped from the list, never shown to him).
3. **On his yes, merge into one project** in the home the [domain tree](/policies/domain-tree.md) gives the topic. Move every open item, Remind Arman line and dependency across. His words go to that node's VISION.md. Delete the emptied folders and repoint every link to them in the same change.
4. **Nothing found:** start a new project there ([projects §3](/skills/projects/SKILL.md)) and run this rulebook from §1.

## 6. Absorb nearly finished projects

Some projects are more than 85% done (§7) and sit in that stage for a long time because nobody flips the switch to finish them. Often the best thing that can happen to them is to be absorbed into a newer, bigger, active project, as a sub-project or a task. That gives them a much better chance of getting finished and keeps things organized.

- Look for chances to do this as much as possible, at the start and all through the project.
- When a project is winding down, see whether it can be pushed onto a bigger active one.
- Your job is to spot and suggest. I'm the human in the loop and I make the call, including whether a project really is that far along.

## 7. How done a project is

Agents forget that 90% of the work is the last 5 to 10%. So "how done" is never a feeling. It is measured against my vision.

For each item on the vision's numbered list (§2), using the latest version of my vision, ask:

1. **Built:** is it built the way my vision says?
2. **Reviewed:** has an adversarial review, by an agent that didn't build it, compared the code and the functionality to my vision and ruled that it matches?
3. **Tested live:** has it been tested live, were the results compared to my vision, and did they pass?

An item counts only when all three are yes. The percentage of items that count is how done the project is. If 85% of the items count, it's 85% done.

Any time an agent says how done a project is, it uses this measure and says how many items count out of the total.

The check itself is run by an agent that did not build the work: [on-track](/skills/on-track/SKILL.md).

## 8. Split off parts that need their own vision

We often realize part of the vision should really have its own vision doc, so it becomes another project.

- When you see it, confirm with me. When I say it, just do it.
- Create the separate project, run §5 for it too, and link the projects (§9).

## 9. Link projects, track dependencies, and remind me

These are two different things, and every project needs both.

- **Dependencies.** Each linked project mentions the other. Record dependencies in the project's existing dependency field, the one that says what a project is waiting on.
- **Remind Arman lines.** When finishing something here will unblock other work, write a very short line an agent will act on, for example:
  `Remind Arman: 2 other projects depend on this one: <A> and <B>. This must reach <point X> before they can use it.`
- **Reminding is the point.** I may come back to a project a week or two later to write code, and not remember what depends on it. Whenever you work on a task that has a Remind Arman line, tell me what finishing it will unblock. Agents need to get really good at this.

## 10. Check everything else at the end, with my words as the reference

Always finish by checking what else is out there. Use background agents. This is easy work for them: Sonnet for judgment, Haiku for quick searches.

1. **Search.** Each time you create or update a vision doc, have a background Sonnet agent check the rest of the documentation for things that are the same, similar, overlapping, or contradictory. Link the related ones (§9), and make sure we don't contradict something already said without me knowing.
2. **Report against my words.** Treat what I said in this conversation as fact. Number each finding, and for each one tell me:
   - "Here's what you said about X. Here's what <other doc> says."
   - Whether it's the same, adds things (list what it adds), or contradicts.
   - When it matters, what the other doc adds that I didn't say.
   - A clear question: should we refer to it, which version is right, or is this a different case?
   - Example: "You said the vision can be cleaned up. Another rule says your vision must always be kept word for word. Which is right?"
   - If you only read part of what's out there, say so.
3. **Fix it all the way through.** Getting my answer is not enough.
   - Work out whether the difference is a deliberate split that applies only here, or whether the other place needs updating too.
   - Make the updates so the two places agree and point to each other where that helps.
   - Example: the rule that my words are kept word for word stays for agents in general, and gets a note that cleaning up my words is allowed only through this skill.

## 11. Check what we already have

If you say we already have docs or a system for something, show me where. If I'm not aware of it, it's probably outdated or not very good.

## 12. Be my trusted advisor

- Focus on what I say.
- For anything that isn't totally new, keep asking how the best companies in the world already do it, and work hard to get us there.

## 13. When a project closes: what happens to my vision

That is its own skill: [vision-closeout](/skills/vision-closeout/SKILL.md).

- When a project closes out, an agent interviews me about what to do with my vision.
- At that point the vision gets significantly cleaned up, consolidated, worded better, and moved into a much bigger, structured vision doc, linked to my original words. A link through git history is fine.
- My vision must not grow forever. It gets cleaner and better structured over time.
- Triggers: a project closing, a vision doc getting too big, or a cleanup session I ask for across related projects.
- Agents may change my vision in that skill only because it's a live interview with me for that purpose.

Until the official vision it files into exists, when a project closes, tell me the vision needs this step and don't delete my words.

## 14. Stay on track

Thinking about the vision often reveals a whole other vision. Capture it (§8), but don't get sidetracked. The main vision is much bigger than one side thing.

Every turn, while you work on a project (added 2026-11-18, approved by Arman):

1. **Open the project page first.** It is the plan, not the latest message. Claim it
   (`project_board.py --claim <slug>`) so the project-focus hook puts it in front of you at every
   start, every context reset and every message.
2. **Answer against it.** A status question is answered from the page and checked against this
   rulebook, not from what the chat happened to do last.
3. **Update `next` and `waiting_on` before you end the turn,** so the board never says "nothing"
   while you wait on him.

## 15. Talk so I can listen

- I often work by voice and listen to your replies on my phone. I usually make it clear when I'm on voice. If it isn't clear, you can ask whether I'd prefer voice or normal right now.
- **Coding agents:** your replies get hidden among tool calls. Give me short updates that are easy to listen to.
- When you want me to listen to something, don't use the question tool in the same message. When I've told you I'm listening by voice, don't use it at all: I can't hear your message until I answer, and the message is what I need to answer.
- When you use the question tool, I only see the questions, so put all the context I need inside each question.
- Use the question tool for questions that are clearly multiple choice and that I know the answer to. For my vision, just talk to me.
- **Never ask me a question in place of a recommendation.** If I ask you to help me figure something out, tell me your recommendation and why, written out. Don't hand me four options, and don't hide your pick as option A in a question.
- From time to time, give me one concise update with no questions, so I can listen and answer by voice.

## 16. Run work in parallel

- Between questions and answers, start as many background agents as you want, for research or anything else, without losing sight of the task I gave you.
- Don't wait for the vision to be finished before running tasks, especially ones whose findings would change what you ask me next.
- Once we have the whole vision or reach a good point, schedule as many tasks as possible across as many parallel lanes as possible.

## 17. The skill map

*Agent-added 2026-11-17 at Arman's request, pending his review: this skill holds the rules, and these skills say how to do each part. Each of them points back here.*

**My vision, from first words to closing.** One rule set (§1–§4, §13). These are the how-tos:

| Step | Skill |
|---|---|
| Capture a new vision, or a quick go-live + final vision for an existing area | this skill, §2 |
| Pull out what I said in earlier conversations | [user-ground-truth](/skills/user-ground-truth/SKILL.md) |
| Ask me anything: recommend first, the question tool, interview rounds | [ask-arman](/skills/ask-arman/SKILL.md) |
| Where a vision file and a project folder live in the docs | [docs](/skills/docs/SKILL.md), [projects §3](/skills/projects/SKILL.md) |
| Check how done a project is against my vision (§7) | [on-track](/skills/on-track/SKILL.md) |
| Clean up, condense and file my vision when a project closes | [vision-closeout](/skills/vision-closeout/SKILL.md) |

**Running the project, by what you were given.** Decide new-or-existing first (§5), then:

| You were given | Skill |
|---|---|
| A whole new system or a big multi-agent build | [campaign-pattern](/skills/campaign-pattern/SKILL.md), then its [vision-to-briefs](/skills/campaign-pattern/vision-to-briefs.md) for research and fleet briefs |
| A whole existing feature to audit, finish or overhaul | [feature-deep-dive](/skills/feature-deep-dive/SKILL.md) |
| One capability to add to a live feature | [build-sub-feature](/skills/build-sub-feature/SKILL.md) |
| A plan to commit to | [plan-attack](/skills/plan-attack/SKILL.md), by an agent that did not write it |
| Work to hand to helper agents | [subagent-dispatch](/skills/subagent-dispatch/SKILL.md) |
| A queue of tasks for the always-on fleet | [work-loop](/skills/work-loop/SKILL.md) |
| A topic whose projects should be gathered into one, or started | this skill, §5a |
| An existing project to take, resume, find stalled, or commit for | [projects](/skills/projects/SKILL.md) |
| A project another lead is leaving | [handoff](/skills/handoff/SKILL.md) |
| A team to run while I'm away | [take-command](/skills/take-command/SKILL.md) |

**Where work waits when it is not a project:** big work I chose not to start yet,
[platform-list](/skills/platform-list/SKILL.md); things for me to see or test in the app,
[agent-review-queue](/skills/agent-review-queue/SKILL.md); defects and task ledgers,
[task-hygiene](/skills/task-hygiene/SKILL.md).

A how-to skill that disagrees with this rulebook is wrong: fix it to match, and point it back here.
