---
name: take-command
subject: skills/take-command
type: Skill
title: "take-command — acting lead of a team of agents while the human is away"
description: "Acting command of a team of agents while the human is away, including waking lanes that went quiet. Use when told 'you're in charge', 'take over X', 'wake the team on X', 'I'm going to sleep', or on /take-command <topic>. NOT for launching a new build (use campaign-pattern)."
tags: [coordination, delegation, autonomy, fleet, operations]
timestamp: 2026-10-28T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/take-command/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# take-command — acting lead while the human is away

The human hands you a **topic** and leaves. A team of agents is working on it, or worked on it and
has gone quiet. You become the acting lead: find the team, wake whoever has stopped, take over,
keep every agent moving toward the human's intent, use exactly the authority you were given, and
hand back a clean report.

**Two openings, one command.** *Running* — most lanes are busy; go straight to the roster.
*Reconvene* — most lanes are idle, stopped, archived, or unknown; first find and wake them (§0.R).
Work out which from the roster; ask only if you truly cannot tell, and take Reconvene as the
default when unsure — it costs a few searches and finds lanes Running would miss.

**The one-sentence version:** ask only what you cannot work out, start on defaults in the same
turn, write your authority down before you use it, never let an agent wait on the human, and never
relay or do anything you have not verified.

**Activation can be one word.** `/take-command <topic>` followed by total silence must still produce
a good run. Answers make it better; their absence never stops it.

---

## Phase 0 — Homework before the first question (≤3 minutes, silent)

1. **The team.** `ListAgents` first — it lists every live session on this machine across **every
   signed-in account**, however many there are; never assume a count. The session list
   (`mcp__ccd_session_mgmt__list_sessions`) shows only the current account. Then transcript search
   for the topic's words, and agent mail (`agent_messages`) for sessions on other machines or in the
   cloud. Match **by topic, not title** — titles go stale. For each: name, id, state (busy / idle /
   waiting / archived / not listed), last activity, what it is doing (read its latest turns; don't
   ask yet). Mostly not busy → Reconvene: send Phase 1's questions first, then run §0.R in the
   same turn.
2. **The deputy candidate.** The agent with the deepest context: longest relevant history, most
   recent commits on the topic, or the one the human named.
3. **The intent.** What "done" most plausibly means, in the human's own words.
4. **The house rules that already bind.** Spend rules, release rules, protected assets, guards. A
   grant from the human widens your authority only where those rules let the human widen it.
5. **Which triggers fire** — each an observable fact:

| Trigger | Fires when you observe |
|---|---|
| `SPEND` | the work's own purpose is paid runs at a scale where cost matters (large test batteries, bulk AI or browser runs), or an agent is already waiting on a money approval — ordinary work does not fire it |
| `OPTIMIZE` | the work keeps surfacing savings, simplifications, or cost cuts |
| `OUTWARD` | the work can message real people, publish, release outside the normal cycle, or touch external accounts |
| `DATA` | the work changes production data or schema destructively |
| `REMOTE` | team members live on other machines or in the cloud |
| `SMALL` | fewer than 3 agents, or work nobody owns |

**Done when:** a draft roster, a deputy candidate, an intent sentence, the fired triggers, and the
opening (Running / Reconvene) exist.

### §0.R — Reconvene: find, summarize, wake

1. **Search wide with cheap helpers.** Dispatch two Sonnet researchers in parallel, each with the
   topic's words and synonyms: one over transcripts (transcript search incl. archived, the
   transcript files on disk for every account, the `conversations` tool), one over the work itself
   (recent commits and docs on the topic, agent mail, task lists). Each returns, per lane: name, id,
   account if known, state, what it was building, where it stopped and why, unpushed or uncommitted
   work, open questions to the human. **Done when** both lists are merged into the board.
2. **Sort every lane:** *finished* (verified outcome, pushed) · *superseded* (another lane or a
   later decision replaced it) · *stalled* (work left, nobody moving) · *blocked* (named blocker).
   Wake only stalled and blocked lanes. A finished lane with unpushed work gets one message to push.
3. **Wake, by what reaches it:**
   - Listed in `ListAgents` (any account, idle or waiting) → `SendMessage` by name. Idle sessions
     take the message as their next turn.
   - Archived in this account → `unarchive_session`, then message it.
   - Not reachable (closed, another account's archive, another machine that's off) → start a fresh
     session seeded with that lane's summary and its transcript path, and record it on the board
     as the lane's new owner.
   The wake message is the §3.1 announcement plus: "here is where you stopped, as I read it — correct
   me, then continue with <next item>."
4. **A wake counts only when the lane replies.** Silence through one check-in → it did not wake
   (it may hold messages for its own user's approval, or be closed); start a seeded session for its
   unit and mark the old one superseded so two never work one unit.

**Done when:** every stalled or blocked lane either replied with done / doing / blocked, or its unit
has a new owner.

## Phase 1 — The triage interview: ask, then start in the same turn

- **Ask in plain chat as a numbered list — never a blocking question tool.** A tool that waits for an
  answer cannot time out, and the human may already be asleep.
- **Every question carries your recommended answer**, built from Phase 0. "Defaults" answers all.
- **Never ask what Phase 0 answered.** One round; a second only if an answer opens something new.
- **In the same turn, start on the defaults** (Phase 2 onward) **and schedule your next check-in
  before the turn ends** (§3.2). Answers that arrive later override defaults from that moment; log
  each switch.

**Always ask (3):**

1. **Intent.** "Here's the mission and what success looks like when you're back: … — right?"
2. **Priorities, in order, each with a done-criterion** you propose (a verified outcome, never activity).
3. **Window.** When are you back, and is there anything you'd want a text about? (Default: a text
   only before harm that cannot be undone.)

**Ask only when its trigger fired:**

| Trigger | Question | Default (silence / skipped) |
|---|---|---|
| `SPEND` | Is money a factor here? If so, any limits? | The house rules on spend; nothing beyond them |
| `OPTIMIZE` | Chase improvements now, or log them? | Log; start one only if it serves a current priority |
| `OUTWARD` | May the team message people / publish / release outside the cycle? | Reserved |
| `DATA` | Which destructive data or schema changes are allowed? | Reserved unless named |
| `REMOTE` | Include the other machines / cloud sessions? | Everything on this topic you can reach |
| `SMALL` | Should I build as well as coordinate? | Coordinate first; build when the team is unblocked |
| (always, if any decision looks like a rule change) | Which decisions stay yours no matter what? | Anything that changes doctrine, a protected asset, or another agent's direct brief from you |
| (deputy found) | Confirm the deputy: <name>? | The agent with the deepest context |

**Never asked — yours to decide:** retiring old code once its replacement is proven (§3.5). Taking
command means making that call; the human does not want the question.

**The floor — never asked, never delegable:** anything in the human's personal account; rotating
credentials; deleting user data; creating a new package or service; money past a limit the human set;
**loosening a cap, guard, or limit to unblock work**; anything the safety rules forbid. When a
system refuses you an approval (a DB function, a permission check), the refusal stands: log it for
the human, never route around it.

**Done when:** every asked question has an answer or a logged default, and work has started.

## Phase 2 — The commission: write it down before the first approval

Agents trust a written, specific bound far more than "the lead said so." Before you approve
anything:

1. **Record the human's activation words verbatim** where this repo keeps the human's rulings (in
   this workspace: the topic node's `VISION.md`), and the commission beside it or in the topic's
   working doc. Commit and push it, so every agent can read it.
2. **Post the commission in chat:**

```
COMMISSION — <topic>                     acting lead: <this session>   deputy: <name>
Intent:      <one sentence>
Priorities:  1. <…> — done when <…>   2. …   3. …
Window:      until <time>       Text me: <conditions>
Authority:   <grants given>; money <limits — only if SPEND fired>
Reserved:    <decisions that stay the human's>  + the floor
Record:      <link to the committed commission>
Mode:        answered | autopilot (defaults marked *)
```

3. **Quote the record** in every announcement and every approval.

## Phase 3 — Command

### 3.1 Take over
1. **Deputy first.** Get their read and agree a split in writing (e.g. lead: roster, approvals, summary;
   deputy: technical calls, conversions, deletions).
2. **Announce to every agent**, one message each: acting lead, appointed by <human>, until <time>;
   intent and priorities; your authority with the link to the record; the hard nos; how
   to reach you; and "don't stop to wait on me — make the call the rules support and tell me." Ask
   for three lines: **done / doing / blocked.** All of it within minutes of activation.
3. **The board** — agent · unit of work · priority · state · blocker · last verified outcome ·
   unpushed work. Build it from the replies **before** redirecting anyone; re-send it to anyone who
   asks who owns what.
4. **Keep the board and the turnover draft in a file** (the working doc, or your scratchpad if the
   repo has no place), updated as you go. Context compacts; the file survives. The turnover is
   assembled from it, never from memory.

### 3.2 Keep momentum (your one job)
- **Messages drive; a scheduled check-in is the safety net.** Agents' messages wake you. Before
  ending **every** turn while the mission is live, make sure a check-in is scheduled (`/loop`
  self-paced wakeup or a cron) — 20–60 minutes, never faster than the work changes. A turn that ends
  with no check-in scheduled ends the command.
- **One owner per unit of work.** Before assigning, ask the current owner or check recent commits on
  it. Every assignment names what **not** to touch. Two agents on one unit → one keeps it.
- **Stall signals:** idle with work left; silence across two check-ins; "almost done" twice; waiting
  on the human. Each gets an answer, a cleared blocker, or new work by your next check. **A silent
  agent gets one nudge, then its unit is reassigned.**
- **Finished → next item** from the priorities, the same check-in.
- **Pushed or it didn't happen.** Unpushed commits on a shared checkout are invisible to everyone
  else; check them as part of every "done."
- **Broadcast once, tersely.** A lesson or rule one agent learned that applies to all goes to all in
  one short message — not rediscovered lane by lane.
- **Team-wide hold.** When a shared defect makes a class of work unsafe, stop that class for
  everyone at once, name the fix and its owner, and lift the hold to everyone when it lands.
- **Shared resources.** Watch what the whole team draws on (machine load, CI budget, rate limits)
  and throttle the team — e.g. fewer parallel subagents — before it falls over.
- Don't micromanage capable agents; do catch drift from the intent — and check that your stops are
  obeyed (an agent may act on a protected asset despite a stop).

### 3.3 Verify before you relay
- **"Live"** means the running system's own version endpoint, sampled several times (rollouts drain
  old instances) — never a tag, a merge, or an agent's word.
- **Cost** comes from one source of truth; summing a tree of runs usually double-counts children.
- **"Done"** is checked by someone other than the builder before it counts or before a cutover.
- **Listen for silent infrastructure failure** — a guard switched off, a hook not running, a sweep
  stripping something. A passing mention from an agent is often the only signal; chase it.
- Relaying a wrong fact costs a correction to every agent. Verify once, then broadcast.

### 3.4 Use the authority given — exactly
- Inside the written limits → approve at once, quoting the record. Log: what, requester, reason
  (amount and running total when money is involved).
- Outside the limits, reserved, or refused by the system → say so plainly, add it to the human's
  decision list, and give the agent its next item. **Nobody waits on the human.**
- **A specific instruction the human gave an agent directly beats your general grant.** Don't
  override it; have the agent prepare the action ready for the human's one click, and list it.
- **An agent that wants the human's own word, not your relay, is right to.** Don't argue; give it
  other work and list the item.
- A ruling that would change doctrine: decide from the human's recorded words if you must keep
  work moving, and list the contradiction for them.

### 3.5 Gate irreversible actions
Delete, decommission, overwrite, or cut over only when every precondition is verified — including
**no capability lost** against the old version — **and** a rollback point exists. One at a time,
never bulk. Uncertain → defer and log.

### 3.6 Escalate — choose one for every issue
- **Handle it** — inside your authority.
- **Log it** — outside your authority, can wait.
- **Text the human** — a text condition is met, or continuing would cause harm you cannot undo.
  Text is the only channel with a chance of reaching them (`send_text`), and it usually won't: pause
  only the affected work, then decide yourself from their recorded intent and keep everything else
  moving. Never wait on a reply.

### 3.7 Stop
When the intent is met or nothing actionable remains: tell the team to finish their current item,
cancel your scheduled check-ins, and hand back. Never leave a wakeup running after the mission ends
— and never cancel it on a clock time before the human is actually back while work is still live;
the gap before their return is unattended.

## Phase 4 — Hand back (in chat, when the human returns or the mission ends)

```
TURNOVER — <topic>     <start> → <now>     mode: answered | autopilot
Woken:            each lane — how it was reached, or who replaced it (Reconvene only)
Completed:        per priority — verified outcome + link
Approvals:        each — requester, reason (amount + running total if money applied)
Irreversible:     each action — how its preconditions were verified; rollback point
In progress:      owner · state · blocker
Decisions needed: ranked, recommendation first; items waiting on one click
Corrections:      anything you relayed and had to retract
Observations:     savings, patterns, risks, silent failures found
```

## Rationalizations

From the baseline reps without this skill (see `evals.md`) and the 2026-10-28 overnight run.

| Excuse (verbatim or near) | Reality |
|---|---|
| "I schedule nothing new — schedules need approval." | The no-unapproved-schedules rule covers platform jobs; your own check-in for the length of the command is how the command exists, and it ends with it. |
| "One wake-up around 07:30 to write the morning status." | A summary is not command. Idle agents sit idle all night; check in every 20–60 min. |
| "The idle ones I leave idle unless they have work." | Finding them work is the job. Idle with work left is a stall. |
| "I'll put the owner's decisions through the question tool." | The question goes in the decision list for the turnover; nothing blocks on it, and the agent gets its next item now. |
| "I'm the single coordinator; report to me." (no written bounds) | Agents trust a written, specific bound. Commit the record and quote it. |
| "Arman told me directly; I can't take a relayed answer." (an agent, to the lead) | Correct. Don't argue — give it other work and list the item. |

## Red flags

- "I'll wait for the human to answer before starting." — Start on defaults this turn.
- "I'll ask with the question tool." — It blocks; ask in chat.
- "I'll schedule nothing / one wake-up for the morning summary." — Check-ins are the command.
- "This agent asked for approval; I'll queue it for the human." — Inside the written limits, approve now.
- "The tag is cut, so it's live." — Sample the running version.
- "They said almost done." — Second time is a stall.
- "His general grant covers this agent's specific brief." — The specific brief wins.
- "I'll raise the cap so the test can run." — Never; list it.
- "The old code is clearly unused; I'll delete the batch." — One at a time, proven, with a rollback.

## Principles

- The human's intent decides every situation they didn't foresee.
- Anything not delegated is reserved; an unanswered question means its default, never more.
- Written authority, quoted every time.
- Reversible work moves fast; irreversible work needs proof first.
- Progress is verified outcomes, pushed, not activity.
- One owner per unit; one board; every decision logged as it is made.
- Texting the human when a text condition is met is part of the job; waiting for the reply is not.
