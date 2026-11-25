---
name: test-model-selection
subject: policies/ai-model-and-spend-rules
type: Skill
title: "test-model-selection — which model a coding agent tests with"
description: "Picks the model a coding agent uses when a test or browser check sends an AI call. Use when choosing a model for any test, UI check, bench, or agent run, or when a test names Opus, Sonnet, Fable, Astra, or any model by habit."
tags: [testing, models, spend, agents]
timestamp: 2026-10-10T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/test-model-selection/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# test-model-selection — which model a coding agent tests with

<!-- rules:stamp -->
> Rules from `common-docs/policies/ai-model-and-spend-rules.md` — edit there, never here (`python3 common-docs/meta/scripts/one_home.py --write`).

1. **Premium models (Opus, GPT-6 Astra, Fable, anything priced like them) think once:** one-shot answers fed by a workflow. Never use them in an agentic loop or for classification, titles, summaries, short replies, tool picking or plumbing tests. (see "Detail behind the rules")
2. **Fable is never chosen by an agent.** Only Arman moves something to Fable. Fable or Astra agents move to Opus 5.5 at most (or GPT-6.1 Sol). No agent sets the account override. (see "Detail behind the rules")
3. **Never swap a model down for cost without Arman.** Show him the agent, runs per month, whether it is agentic, and links first. An agent Arman made on Opus is never touched, and anything that creates agents is never Sonnet. (see "Detail behind the rules")
4. **Reasoning effort defaults to low.** Medium only for non-straightforward tasks, high rarely and tuned by Arman (skill `reasoning-level-calibration`). (see "Detail behind the rules")
5. **A single run over $2 is approved by Arman after its first run**, before it runs again (knob `billing.run_approval.threshold_usd`). Nothing unreviewed runs at volume, and test data never justifies repeat premium runs. (see "Detail behind the rules")
6. **A money-spending automation runs once, then stops and reports.** Asking permission is not approval. Held work is re-enabled only through one small watched test with its expected cost stated. (see "Detail behind the rules")
7. **No spend limits on provider consoles;** every limit lives in our app. Plan and spend limits are never raised to unblock a test or a lane: at a cap, stop and tell Arman. (see "Detail behind the rules")
8. **A real user's spend, even trial or free, is never blocked, capped or framed as a problem.** Gate only agent, test and system spend, and report real users separately. (see "Detail behind the rules")
9. **Hard provider costs (scrapers, data APIs, media fetches) are charged in points,** 20,000 points = $1, gated like AI cost. Users never see vendor names or the rate. A new paid provider goes through the points ledger. (see "Detail behind the rules")
10. **Safety and moderation settings sent to an AI provider default to the provider's absolute minimum.** A person choosing stricter is honored. Never keep a stricter default "for safety". Guard: `scripts/check_minimum_safety.py`. (see "Detail behind the rules")
11. **An automation never runs without its core data present** and says plainly what is required. SEO, HR and news automations stay off pre-production. A test record is never a customer. (see "Detail behind the rules")
12. **Every agent we build starts on Haiku (Claude Haiku 5.5).** Sonnet only when tests prove Haiku cannot do the job, Opus only if Sonnet also fails and Arman approves. An agent born on Opus is a defect. No platform default pins a premium model. (see "Detail behind the rules")
13. **Coding agents test and tune an agent on Haiku, then Sonnet,** never on a premium model. They hand the test data to Arman, who optimizes the premium agents and decides whether one goes back to a bigger model. (see "Detail behind the rules")
14. **A spend approval for a run under $1 with 5 turns or fewer is approved automatically for 7 days,** capped at $25 a day in total. Bigger runs wait for Arman. Every value is a knob on `billing.run_approvals`. (see "Detail behind the rules")
15. **An approval is never decided blind:** its page lists what its subject did (`billing.run_approval_activity`), and a held coding agent says what it is doing with `scripts/spend_approval_note.py`. (see "Detail behind the rules")
16. **An approval opened by a run of an agents-only seat (`platform.agents_only_seat_ids()`) is approved automatically for 7 days.** A real person's hold, a volume hold and a person's rejection stay Arman's. (see "Detail behind the rules")
17. **Which model a run uses (model precedence): the model the person picked for that conversation, else the agent's own model.** A person's "Default AI model" (`agents.model_prefs.chat_default_model` and its class `chat_default_offering`) is not in that chain: it is only the model pre-selected where nothing has a model yet (building an agent, a page or surface where no model is chosen). It never replaces the model of an agent that already has one, on any door (including the default new chat, mandate `chat.default_new_chat` / General Chat) or in any app (www, chat.aimatrx.com, iPhone, extension, server). Owner ruling 2026-11-25: [chat-ground-up VISION item 46](/systems/chat/projects/chat-ground-up/VISION.md). Code that still applies the preference to an agent's run is bug B-1 in [new-chat README](/systems/chat/projects/new-chat/README.md).
<!-- rules:stamp-end -->

Know what is being tested and adjust accordingly ([owner's words](/policies/VISION.md#ai-model-and-spend-rules)). Never pick a
test model from memory. Ask what the test is for, then ask the catalog.

## 1. What are you testing?

| The test is about | Purpose | Gets you |
|---|---|---|
| Plumbing: UI, persistence, wiring, tools, streaming. The answer's content does not matter. | `plumbing` | The cheapest unrestricted priced chat model that supports tool calls. Cheaper than Haiku. |
| Something that depends on the provider: its API, its streaming shape, its errors. | `provider` + name | The cheapest unrestricted model of THAT provider (Haiku for Anthropic). |
| The actual intelligence: answer quality, reasoning, an eval. | `intelligence` | The recommended default (Sonnet 5.5 today) plus the list of restricted models. |

## 2. Ask (one call)

```
agent_catalog action=test_model purpose=plumbing
agent_catalog action=test_model purpose=provider provider=Anthropic
agent_catalog action=test_model purpose=intelligence
```

No MCP? From `aidream/`: `uv run python scripts/test_model.py plumbing`, or `provider Anthropic`,
or `intelligence`. Both return the model id, name, price per 1M tokens in and out, and why. Use that
model name as given.

**Plumbing that does not need a fresh answer at all: don't call the model.** Run the test in AI test
mode (`?ai_test_mode=stand_in` on the app URL, or header `X-Matrx-AI-Test-Mode: stand_in`): automated
AI steps answer from saved baselines and cost nothing. Never for an `intelligence` test, and you still
owe one `real` run before calling an AI-backed flow done. [ai-test-mode](/policies/ai-test-mode.md).

## 2b. Never test through General Chat

General Chat is for people who chat; a test gets its own super cheap, fast agent
([owner's words](/systems/chat/conversations/VISION.md)). When a test needs any AI reply, run **Test Reply**
(agent `8a4b7c8e-144f-4391-b5f0-3d2cd2ecee0f`, plumbing model, reasoning low, 1500 output tokens,
no tools, about $0.00002 a reply): `agent_run agent_id=8a4b7c8e-144f-4391-b5f0-3d2cd2ecee0f
user_message="ok"`. The test seats' new-chat default is already Test Reply (user binding of
`chat.default_new_chat`). It answers in a sentence or two by default and gives a length the turn asks for
in full (a stream walk asking for 400 words gets them); guard
`scripts/check_default_chat_holders_honor_length.py --live`. General Chat stays held for agent seats.
A test that needs an agent to WORK WITH DATA (tables, records, a connected app such as Notion) runs **Test
Data Agent** (`5f533f4f-325a-4983-8702-d3a824394e68`, Claude Haiku 5.5, reasoning low, tools: records,
table, ask_person). The plumbing 20B model could not drive the records tool (it asked for an export), so
this one is the cheapest that does.

## 3. Never a restricted model

Max-tier, Fable, Astra, Mythos, and anything at or above the cost rating the answer reports
(`restricted_min_cost_rating`, 6 today) are off limits for testing. An `intelligence` answer lists
them in `restricted_models`. The top-tier gate refuses them for anyone not approved, and a test
account that asks for one raises a spend alarm to the super admins (one per account per day). The
guard warns; it does not block, so the refusal is not your signal. This skill is.

## 4. Keep the test small

Fresh short context, as few calls as prove the point, and no premium pipeline re-run to check a code
fix that does not depend on the model's reasoning. Rules:
[ai-model-and-spend-rules §5](/policies/ai-model-and-spend-rules.md).

## 5. Where the answer comes from

One function, `ai.test_model_choice(purpose, provider)`, computed from the live catalog (active,
priced offerings only). Knobs under `ai.test_models` override it: `plumbing_model`,
`provider_models`, `intelligence_model`, `restricted_min_cost_rating`, `restricted_name_pattern`.
Reviewed monthly; the catalog changes, so never hard-code the answer in a test.
