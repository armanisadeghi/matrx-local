---
name: ask-arman
subject: platform/question-desk
type: Skill
title: "ask-arman — recommend first, the homework gate, the interview, the Question Desk"
description: "How any question reaches Arman: recommend instead of asking, the homework gate, the question tool (never while he listens by voice), open vision questions, interview rounds, the Question Desk. Use when about to ask him anything, on 'grill me' or 'stress-test this', ending a turn on a question, writing 'needs Arman's decision', or on /ask-arman. NOT for a step only he can perform (use a guided session)."
tags: [questions, decisions, arman, interview, discipline, operations]
timestamp: 2026-11-17T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/ask-arman/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# ask-arman — recommend first, the gate, the interview, the desk

<!-- Interview mechanics (§1 draft-and-prune, §3 rounds) adapted from Matt Pocock's `grilling`, via the former grilling skill. -->

Part of a project? The rulebook is [ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md);
this skill is how every question in it is asked. How every sentence to him is shaped:
[talk to Arman like a person](/policies/talk-to-arman-like-a-person.md). His words behind §0:
[VISION.md § ai-dream-coding-projects](/policies/VISION.md#ai-dream-coding-projects) (2026-11-17).

## 0. Recommend; don't ask

- **A recommendation is never a question.** When he asks you to help figure something out ("which
  platform should we use?"), answer with your recommendation and its reason, written out in the
  message. Never hand him a menu of options, and never put your pick in a question as "option A
  (Recommended)" instead of saying it.
- **A real question** is one he knows the answer to and you believe he knows: his intent, his taste,
  what he meant, a ruling only he can make. Those go through the question tool (§2) with good
  multiple-choice answers.
- **Open vision questions are the most valuable ones.** Ask them often, in plain chat, with no
  options and no recommendation: "When this is fully grown, what is it?" Let him talk.
- **Voice.** When he has said he is listening by voice, never use the question tool in that
  message: he cannot hear your message until the question is answered, and the message is what he
  needs to answer it. Ask in plain words, short and easy to listen to. When it is unclear whether
  he is on voice, you may ask once.

## 1. The homework gate — run in order, record each result

Draft every open decision privately first, with the decisions that hang off it. Then run each one
through this gate. What survives is only vision, product semantics, money, brand, legal, deleting
real data, an account only he holds, or his eyes on a real screen.

0. **Boss or user?** Would the question exist if he had never used the product himself? A
   question about ONE organization's content, taste or settings is a customer's: make it an
   onboarding step, a starter kit, or a knob with a default ([ask the boss](/policies/talk-to-arman-like-a-person.md)).
0a. **Is it a step the task he gave already needs?** Then it is approved: do it, never ask
   ([rule 3a](/policies/talk-to-arman-like-a-person.md)). Could he plausibly say no? If not, it is not a question.
0b. **Does any answer change what he will see?** If not, decide and record it.
1. **A fact?** Code, the DB, a doc or the web settles it: look it up (a `quick` lane if tedious).
2. **Already ruled or delegated?** Grep the SUBJECT nouns across the owning node's
   `DECISIONS.md` and `VISION.md`, the vocabulary lexicon, [conflicts](/operations/conflicts.md),
   and `question_desk(action='list_answers')`. A dated ruling → apply and cite it. "Research the
   best and decide" → decide, record the reason in `DECISIONS.md`, tell him.
3. **Table stakes, a knob, a limit or a number?** Decide it
   ([table stakes](/policies/talk-to-arman-like-a-person.md),
   [limits are knobs](/policies/limits-are-knobs-agents-set-them.md)). A number that is really a
   mechanism: bring the mechanism.
4. **Do the champions agree?** ([champions](/policies/champions.md)) Do what they do, name
   who, list it as "decided — override by number".
5. **A human step?** His account, his screen, money, a credential → one guided session in chat
   ([rule 11](/policies/talk-to-arman-like-a-person.md)), not a question.

The one exception: Arman saying "grill me" on HIS OWN plan. Then challenge his assumptions too,
still never ask facts.

## 2. Ask it in chat with the question tool

A question that survives §1 is his to answer, and asking it is the right move, not a failure.

1. Unless §0's voice rule applies, use the question tool (`AskUserQuestion`) in the chat he is in.
   Never bury a question in a long message, and never send it only to the Question Desk while he is
   talking with you.
2. **He sees only the questions** while the tool is open, so each question carries all the context
   he needs: the thing in plain words, one line of background, what he ruled before (or "never
   ruled"); each option's description says its effect and what the best companies do. A question
   about a screen carries the page's full link ([rule 22](/policies/talk-to-arman-like-a-person.md)).
   Where you hold a recommendation on a real question, it is also written in the message text, never
   only as a labelled option.
3. Options differ in approach, never just in magnitude. Up to four quick questions may share one
   call; a complex one goes alone.
4. Keep building everything that does not depend on the answer. Say that a skipped question stays
   open and comes back later; nothing ships with your recommendation unless he says so for that
   question. Record his answer verbatim in the owning `VISION.md` the same turn.
5. No question tool in your harness, or he is not in this conversation (a scheduled or background
   run) → file it on the Question Desk (§4) and keep working.

## 3. Interview rounds (a plan, scope, spec or vision needs many rulings)

- **Frontier** = surviving decisions whose prerequisites are settled. A question whose answer
  depends on another question open this round waits for a later round. Never block on exploration:
  send the fact-independent frontier now; only questions downstream of a running subagent wait. If he
  can step away while you explore, say so. Queue the rest by how much of the tree each answer unblocks.
- **Number continuously across rounds** (round 2 starts at Q5), so "5 yes" is never ambiguous.
- **Question shape:** **Q<n> — <title>.** 2–3 plain sentences of background (no doc references,
  codenames, item IDs or emojis), then the question in one sentence. About a flow, surface or
  behavior: attach the clickable URL and where to look, or a mermaid diagram of what actually
  happens, or a throwaway prototype labelled PROTOTYPE. A question he cannot answer from what you
  gave him is a defect in the question, and often a sign the path itself is broken, which is the real finding.
- **Every round ends with:** `Decided (override by number): D1 … · D2 …` / `Anything you skip stays
  open; I'll bring it back.` / `What else should I know that I didn't ask?`
- **After every answer:** record it where it belongs and commit (vision → his words in `VISION.md`;
  ruling → the settled table, dated; new work → the node's task list; killed work → removed). Then
  recompute the frontier: say which queued questions died; a partial answer gets a follow-up next
  round. **Skipped** = still open, asked again later. **"Defer"** = a dated deferral. **"I need to
  see it first"** = deferral + the URL. **"I don't know" / "we have options"** = the question failed
  him: research, then bring it back with better facts and your recommendation, never picked
  silently and never re-asked as-is.
- **Done** when the frontier is empty: every decision answered, decided with override offered, or
  deferred with a date. Then act: state the final decisions in ≤10 lines in the same message in
  which you start the work. An empty frontier is the success condition; never invent a question to
  have one to ask.

## 4. File it on the Question Desk (when it can wait)

For a question that can wait while you finish other work, or when he is not in the chat. The desk is the in-app surface `https://manage.aimatrx.com/admin/question-desk/<interview id>`
(send him the tool's full `url`), driven by the
AI Dream MCP tool `question_desk`. There is no markdown ledger.

1. `question_desk(action='list_interviews')`, then `get_interview` on the open ones; a question
   on the same subject exists → add yourself through `update_question` (`also_asked_by`) and stop.
2. Otherwise `create_interview` (respondent Arman; needs full MCP access) or reuse an open one,
   then `add_question` with `slug`, `title`, `question` and `fields`: the five parts
   (`ruled_before`, `the_best_do`, `today`, `implications`, `recommendation`), `background`,
   `default_in_force`, `door`, `node` (a real `systems/…` or `projects/…` path), `checked`
   (your gate results), `filed_chat_title`, `filed_session_id`.
3. `update_question(status='researched')` once all five parts are filled (the tool refuses
   otherwise).

## 5. Interview mode (/ask-arman when he has time)

1. **Gather:** every open question across `list_interviews` and the owner questions in
   [conflicts](/operations/conflicts.md). Merge duplicate subjects.
2. **Re-run §1 for every asker.** Close what fails with its reason and evidence; a rescue of a
   closed row must show the ruling is absent.
3. **Research survivors:** one `standard` brief per question fills the five parts with sources;
   tag kind (quick/complex), door, weight.
4. **Rounds:** open with "I have N questions to ask you and M things to tell you"; the things
   first. Then rounds per §3, `mark_asked` as each goes out, closing with how many remain and
   "anything you skip stays open".
5. **Record:** answers he typed in the admin surface are already on the row; an answer given in
   chat → `record_answer`, verbatim. Then the owning `DECISIONS.md` (his words → `VISION.md`).
6. **Deliver:** the asking chat gets one paragraph (question, verbatim answer, where recorded)
   via `SendMessage`, or `claude --resume <session> -p` when it is closed; then
   `mark_delivered`. Drain the agent half of `work_loop` campaign `question-desk`
   (`qd:overturn:*`, `qd:decide:*`, claimed by exact key) until `status` shows nothing claimable.
7. **Close:** answered, closed-without-him (one line each), remaining, chats he must poke.

Never create a schedule for this ([no unapproved schedules](/policies/no-unapproved-schedules.md)).

## 6. Picking up your answer

On resume: `question_desk(action='list_answers')` (or the bounded `wait_for_answers` when idle),
read it verbatim, apply it, reverse the default where it differs.

## Rationalizations

| Excuse | Reality |
|---|---|
| "I'll give him the options and let him pick" | He asked for help deciding. Recommend (§0). |
| "My pick is option A, marked Recommended" | Say it in the message. A picker is not a recommendation. |
| "This needs Arman's decision" | Gate tests 1–4 were not run. |
| "It's blocked on him" | Only a one-way door blocks. |
| "He skipped it, so my recommendation ships" | A skipped question stays open unless he says so for that question. |
| "I'll use the question tool, it's faster" | Not while he is listening by voice. |
| "Asking him looks like I didn't do my homework" | After §1, not asking is the failure. |

## Red flags

- A menu of options in reply to "which should we use?".
- The question tool in a message to someone who said they are listening.
- "Let me check with Arman before I…" before grepping `DECISIONS.md`.
- "Blocked" in your status with no one-way door.
- A question containing a file path, a code, or "see" (a page it is about carries its full link instead).
- Zero open vision questions in a vision interview.
- A decision left as a flag inside a document he would have to dig for.
- One question per message, approval after each design section, or making him wait for a full audit before round 1.

Proof records: [evals.md](evals.md) (the gate) and [interview-evals.md](interview-evals.md) (the
rounds, from the former grilling skill; its ship-the-default criteria predate the 2026-11-15
skipped-questions ruling and must be rerun against it).
