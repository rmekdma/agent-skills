# Orchestration Patterns

Reference catalog of agent orchestration patterns this repo endorses, plus anti-patterns to avoid. Read this before adding a new source command that coordinates multiple subagent prompts, or before introducing a new subagent prompt that "wraps" existing ones.

The governing rule: **the user (or a source command) is the orchestrator. Subagent prompts do not invoke other subagent prompts.** Skills are mandatory hops inside a subagent prompt's workflow.

---

## Endorsed patterns

### 1. Direct invocation (no orchestration)

Single subagent prompt, single perspective, single artifact. The default and the cheapest option.

```
user → code-reviewer → report → user
```

**Use when:** the work is one perspective on one artifact and you can describe it in one sentence.

**Examples:**
- "Review this PR" → `code-reviewer`
- "Find security issues in `auth.ts`" → `security-auditor`
- "What tests are missing for the checkout flow?" → `test-engineer`

**Cost:** one round trip. The baseline you should always compare orchestrated patterns against.

---

### 2. Single-subagent prompt source command

A source command that wraps one subagent prompt with the project's skills. Saves the user from re-explaining the workflow every time.

```
/review → code-reviewer (with code-review-and-quality skill) → report
```

**Use when:** the same single-subagent prompt invocation happens repeatedly with the same setup.

**Examples in this repo:** `/review`, `/test`, `/code-simplify`.

**Cost:** same as direct invocation. The source command is just a saved prompt.

**Anti-signal:** if the source command's body is mostly "decide which subagent prompt to call," delete it and let the user call the subagent prompt directly.

---

### 3. Parallel fan-out with merge

Multiple subagent prompts operate on the same input concurrently, each producing an independent report. A merge step (in the main agent's context) synthesizes them into a single decision.

```
                    ┌─→ code-reviewer    ─┐
/ship → fan out  ───┼─→ security-auditor ─┤→ merge → go/no-go + rollback
                    └─→ test-engineer    ─┘
```

**Use when:**
- The sub-tasks are genuinely independent (no shared mutable state, no ordering dependency)
- Each sub-agent benefits from its own context window
- The merge step is small enough to stay in the main context
- Wall-clock latency matters

**Examples in this repo:** `/ship`.

**Cost:** N parallel sub-agent contexts + one merge turn. Higher than direct invocation, but faster wall-clock and produces better reports because each sub-agent stays focused on its single perspective.

**Validation checklist before adopting this pattern:**
- [ ] Can I run all sub-agents at the same time without ordering issues?
- [ ] Does each subagent prompt produce a different *kind* of finding, not just the same finding from a different angle?
- [ ] Will the merge step fit in the main agent's remaining context?
- [ ] Is the user's wait time long enough that parallelism is actually noticeable?

If any answer is "no," fall back to direct invocation or a single-subagent prompt command.

---

### 4. Sequential pipeline as user-driven source commands

The user runs source commands in a defined order, carrying context (or commit history) between them. There is no orchestrator agent — the user IS the orchestrator.

```
user runs:  /spec  →  /plan  →  /build  →  /test  →  /review  →  /ship
```

**Use when:** the workflow has dependencies (each step needs the previous step's output) and human judgment between steps adds value.

**Examples in this repo:** the entire DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP lifecycle.

**Cost:** one sub-agent context per step. Free for the orchestration layer because there is no orchestrator agent.

**Why not automate it:** an LLM "lifecycle orchestrator" would (a) lose nuance between steps because it has to summarize for hand-off, (b) skip the human checkpoints that catch wrong-direction work early, and (c) double the token cost via paraphrasing turns.

---

### 5. Research isolation (context preservation)

When a task requires reading large amounts of material that shouldn't pollute the main context, spawn a research sub-agent that returns only a digest.

```
main agent → research sub-agent (reads 50 files) → digest → main agent continues
```

**Use when:**
- The main session needs to stay focused on a downstream task
- The investigation result is much smaller than the input it consumes
- The decision quality benefits from the main agent having room to think after

**Examples:** "Find every call site of this deprecated API across the monorepo," "Summarize what these 30 ADRs say about caching."

**Cost:** one isolated sub-agent context. Worth it any time the alternative is loading hundreds of files into the main context.

**On Codex, use the built-in `Explore` subagent** rather than defining a custom research subagent prompt. `Explore` runs on Haiku, is denied write/edit tools, and is purpose-built for this pattern. Define a custom research subagent only when `Explore` doesn't fit (e.g. you need a domain-specific system prompt the model wouldn't infer).

---

## Codex compatibility

This catalog is harness-agnostic, but most readers will run it on Codex. Here's how each pattern maps onto Codex's primitives — and where the platform enforces our rules for us.

### Where subagent prompts live

Plugin subagents go in `agents/` at the plugin root. This repo is a plugin (`.claude-plugin/plugin.json`), so `code-reviewer`, `security-auditor`, and `test-engineer` are auto-discovered when the plugin is enabled. No path configuration needed.

### Subagents vs. Agent Teams

Codex has two parallelism primitives. Pattern 3 (parallel fan-out with merge) maps to **subagents**. If you need teammates that talk to each other, use **Agent Teams** instead.

| | Subagents | Agent Teams |
|--|-----------|-------------|
| Coordination | Main agent fans out, sub-agents only report back | Teammates message each other, share a task list |
| Context | Own context window per subagent | Own context window per teammate |
| When to use | Independent tasks producing reports | Collaborative work needing discussion |
| Status | Stable | Environment-dependent |
| Cost | Lower | Higher — each teammate is a separate Claude instance |

**The subagent prompts in this repo work in both modes.** When spawned as subagents (e.g. by `/ship`), they report findings to the main session. When spawned as teammates (`Spawn a teammate using the security-auditor agent type…`), they can challenge each other's findings directly. The subagent prompt definition is the same; only the spawning context changes.

One subtlety: the `skills` and `mcpServers` frontmatter fields in a subagent prompt are honored when it runs as a subagent but **ignored when it runs as a teammate** — teammates load skills and MCP servers from your project and user settings, the same as a regular session. If a subagent prompt depends on a specific skill or MCP server being loaded, configure it at the session level so it's available in both modes.

### Platform-enforced rules

Two rules in this catalog aren't just convention — Codex enforces them:

- **"Subagents cannot spawn other subagents"** (verbatim from the docs). Anti-pattern B (subagent prompt-calls-subagent prompt) and Anti-pattern D (deep subagent prompt trees) cannot exist on Codex by construction.
- **"No nested teams"** — teammates cannot spawn their own teams. Same anti-patterns blocked at the team level.

This means you can adopt the patterns in this catalog without worrying about contributors accidentally building the anti-patterns. They'll just fail to load.

### Built-in subagents to know about

Before defining a custom subagent, check whether one of these covers the role:

| Built-in | Purpose |
|----------|---------|
| `Explore` | Read-only codebase search and analysis. Use this for Pattern 5 (research isolation). |
| `Plan` | Read-only research during plan mode. |
| `general-purpose` | Multi-step tasks needing both exploration and modification. |

Don't redefine these. Layer your specialist subagent prompts (code-reviewer, security-auditor, test-engineer) on top of them.

### Frontmatter restrictions for plugin agents

Plugin subagents do **not** support the `hooks`, `mcpServers`, or `permissionMode` frontmatter fields — these are silently ignored. If a future subagent prompt needs any of those, the user must copy the file into `~/.codex/agents/` instead.

The fields that DO work in plugin agents are: `name`, `description`, `tools`, `disallowedTools`, `model`, `maxTurns`, `skills`, `memory`, `background`, `effort`, `isolation`, `color`, `initialPrompt`. Use `model` per-subagent prompt if you want to optimize cost (e.g. Haiku for `test-engineer` coverage scans, Sonnet for `code-reviewer`, Opus for `security-auditor`).

### Spawning multiple subagents in parallel

In Codex, parallel fan-out (Pattern 3) requires issuing **multiple Codex subagent tool calls in a single assistant turn**. Sequential turns serialize execution. `/ship` calls this out explicitly. Any new orchestrator command should do the same.

---

## Worked example: Agent Teams for competing-hypothesis debugging

This example shows when to reach for **Agent Teams** instead of `/ship`'s subagent fan-out. The two patterns look similar from a distance — both spawn the same three subagent prompts — but the value comes from a different place.

### The scenario

> *Checkout occasionally hangs for ~30 seconds before completing. It happens roughly once every 50 sessions. No errors in logs. Started after last week's release.*

Plausible root causes (mutually exclusive, all fit the symptoms):

1. A race condition in the new payment-confirmation flow
2. An auth check that occasionally falls through to a slow synchronous network call
3. A missing index on a query that scales with cart size
4. A flaky third-party API where the SDK retries silently before timing out

A single agent will pick the first plausible theory and stop investigating. A `/ship`-style subagent fan-out would have each subagent prompt report independently — but their reports never meet, so nothing rules out the wrong theories.

This is exactly the case the Agent Teams docs describe: *"With multiple independent investigators actively trying to disprove each other, the theory that survives is much more likely to be the actual root cause."*

### Why this is *not* a `/ship` job

| | `/ship` (subagents) | Agent Teams |
|--|--------------------|-------------|
| Sub-agents see | The same diff, different lenses | A shared task list, each other's messages |
| Output | Three independent reports → one merge | Adversarial debate → consensus root cause |
| Right when | You want a verdict on a known artifact | You want to *find* the artifact among hypotheses |

`/ship` is a verdict; Agent Teams is an investigation.

### Setup (one-time, per-environment)

Multi-agent teammate support depends on the active Codex environment. Confirm the required setup in your Codex installation before using this pattern.

### The trigger prompt

Type into the lead session, in natural language:

```
Users report checkout hangs for ~30 seconds intermittently after last
week's release. No errors in logs.

Create an agent team to debug this with competing hypotheses. Spawn
three teammates using the existing agent types:

  - code-reviewer  — investigate race conditions and blocking calls
                     in the checkout code path
  - security-auditor — investigate auth checks, session handling,
                       and any synchronous network calls added recently
  - test-engineer  — propose tests that would distinguish between the
                     hypotheses and check coverage gaps in checkout

Have them message each other directly to challenge each other's
theories. Update findings as consensus emerges. Only converge when
two teammates agree they can disprove the others'.
```

The lead spawns three teammates referencing the existing subagent prompt names. The subagent prompt body is **appended** to each teammate's system prompt as additional instructions (on top of the team-coordination instructions the lead installs); the trigger prompt above becomes their task.

### What happens

1. Each teammate runs in its own context window, exploring the codebase from its own lens.
2. Teammates use `message` to send findings to each other directly. The lead doesn't have to relay.
3. The shared task list shows who's investigating what — visible at any time with `Ctrl+T` (in-process mode) or in a tmux pane (split mode).
4. When `code-reviewer` finds a `Promise.all` that should be sequential, it messages `security-auditor` to confirm the auth call isn't part of the race. `security-auditor` checks and replies — either confirming the race is the real issue or producing counter-evidence.
5. `test-engineer` proposes a focused integration test for whichever theory is winning, which the team uses to verify before declaring consensus.
6. The lead synthesizes the converged finding and presents it to you.

You can interrupt at any teammate by cycling with `Shift+Down` and typing — useful for redirecting an investigator who's gone down a wrong path.

### When to clean up

When the investigation lands on a root cause, tell the lead:

```
Clean up the team
```

Always cleanup through the lead, not a teammate (per the docs: teammates lack full team context for cleanup).

### Cost expectation

Three Sonnet teammates running for ~10–15 minutes of investigation costs noticeably more than the same three subagent prompts spawned as subagents by `/ship`. The justification is *quality of conclusion* — for production debugging where the wrong fix is expensive, the extra tokens are a bargain. For a routine PR review, stick with `/ship`.

### Anti-pattern in this scenario

Do **not** rebuild this as a `/debug` source command that fans out subagents. Subagents can't message each other — you'd lose the adversarial debate that makes the pattern work. If a workflow keeps coming up, document the trigger prompt above as a snippet rather than wrapping it in a source command that misuses subagents.

### When *not* to use Agent Teams

- Production-bound verdict on a known diff → use `/ship` (subagents).
- One specialist perspective on one artifact → direct subagent prompt invocation.
- Sequential lifecycle (spec → plan → build) → user-driven source commands (Pattern 4).
- Read-heavy research with a small digest → built-in `Explore` subagent.

Reach for Agent Teams only when teammates **need** to challenge each other to produce the right answer.

---

## Anti-patterns

### A. Router subagent prompt ("meta-orchestrator")

A subagent prompt whose job is to decide which other subagent prompt to call.

```
/work → router-subagent prompt → "this needs a review" → code-reviewer → router (paraphrases) → user
```

**Why it fails:**
- Pure routing layer with no domain value
- Adds two paraphrasing hops → information loss + roughly 2× token cost
- The user already knew they wanted a review; they could have called `/review` directly
- Replicates the work that source commands and intent mapping in `AGENTS.md` already do

**What to do instead:** add or refine source commands. Document intent → command mapping in `AGENTS.md`.

---

### B. Subagent prompt that calls another subagent prompt

A `code-reviewer` that internally invokes `security-auditor` when it sees auth code.

**Why it fails:**
- Subagent prompts were designed to produce a single perspective; chaining them defeats that
- The summary the calling subagent prompt passes loses context the called subagent prompt needs
- Failure modes multiply (which subagent prompt's output format wins? whose rules apply?)
- Hides cost from the user

**What to do instead:** have the calling subagent prompt *recommend* a follow-up audit in its report. The user or a source command runs the second pass.

---

### C. Sequential orchestrator that paraphrases

An agent that calls `/spec`, then `/plan`, then `/build`, etc. on the user's behalf.

**Why it fails:**
- Loses the human checkpoints that catch wrong-direction work
- Each hand-off summarizes context — accumulated drift over a long pipeline
- Doubles token cost: orchestrator turn + sub-agent turn for every step
- Removes user agency at exactly the points where judgment matters most

**What to do instead:** keep the user as the orchestrator. Document the recommended sequence in the project README and let users invoke it.

---

### D. Deep subagent prompt trees

`/ship` calls a `pre-ship-coordinator` that calls a `quality-coordinator` that calls `code-reviewer`.

**Why it fails:**
- Each layer adds latency and tokens with no decision value
- Debugging becomes a multi-level investigation
- The leaf subagent prompts lose context to multiple summarization steps

**What to do instead:** keep the orchestration depth at most 1 (source command → subagent prompts). The merge happens in the main agent.

---

## Decision flow

When considering a new orchestrated workflow, walk this flow:

```
Is the work one perspective on one artifact?
├── Yes → Direct invocation. Stop.
└── No  → Will the same composition repeat?
         ├── No  → Direct invocation, ad hoc. Stop.
         └── Yes → Are sub-tasks independent?
                  ├── No  → Sequential source commands run by user (Pattern 4).
                  └── Yes → Parallel fan-out with merge (Pattern 3).
                           Validate against the checklist above.
                           If any check fails → fall back to single-subagent prompt command (Pattern 2).
```

---

## When to add a new pattern to this catalog

Add a new entry only after:

1. You've used the pattern at least twice in real work
2. You can name a concrete artifact in this repo that demonstrates it
3. You can explain why an existing pattern wouldn't have worked
4. You can describe its anti-pattern shadow (what people will mistakenly build instead)

Premature catalog entries become aspirational documentation that no one follows.
