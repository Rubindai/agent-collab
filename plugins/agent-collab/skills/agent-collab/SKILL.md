---
name: agent-collab
description: Use when the user says "use agent collab", "use agent-collab", or explicitly requests this plugin. Launch an independent Claude or Codex peer for review, research, design, planning, or debugging. Also use for substantial work when independent cross-checking is warranted; avoid routine edits and explanations.
---

# Agent Collab

Launch the opposite product before host analysis, independently verify its evidence, and close the run before answering. The host owns the final judgment.

A direct request invokes the actual peer workflow, even for a small task. Do not substitute host-only analysis or native helpers. Implicit discovery remains enabled; quoted mentions, informational questions, and source edits alone do not invoke a run.

Use this file's directory as `<skill-root>` and only its current resources. The normal workflow is self-contained here. Runtime artifacts use strict `"schema_version": "3.0"`; hosts supply judgments, never mechanical metadata or hashes.

## Start

Write a compact neutral brief stating the outcome, relevant context, done condition, boundaries, and available evidence. Omit host conclusions and suspected findings. Then launch before host analysis:

```bash
python3 "<skill-root>/scripts/host.py" start --host <host> --target "<target>" --brief-file "<brief-file>"
```

Replace placeholders with literal values. `<host>` is `codex` or `claude`; Windows uses `py -3`. The runtime selects `review|research|design|plan|debug`, or accept an explicit `--mode`.

Defaults are Claude `claude-opus-5-5`/`xhigh` for a Codex host and Codex `gpt-6-astra`/`xhigh` for a Claude host. Preserve explicit choices with `--peer-model` and `--peer-effort`; Fable is explicit-only. `start` resolves settings and attests the exact pair before run creation. On any prerequisite failure, stop before host analysis or helpers, show the requested pair and structured evidence, and state that the peer did not run. Never retry a fallback or silently change permissions or workspace mode.

Full capability, no durable peer edits, and enabled adjudication are defaults; preserve user settings. Durable peer edits require explicit delegation and `--edit-allowed --workspace-mode direct`. Linux defaults to a disposable overlay; macOS and Windows use detective snapshots. Neither provides complete containment or mutation observation. Anthropic recommends `bypassPermissions` only in isolated environments without internet access where host damage is impossible; our Linux workspace retains networking and host-facing sockets. Use safe mode only when selected. Consult [launch policy](references/launch.md) for overrides, platform restrictions, isolation boundaries, or failures.

## Follow the runtime

`start` returns `run_dir` and `next_action`. Perform that action, save the requested semantic input, and execute its `command` argv. After launch, every ordinary interaction uses the same command:

```bash
python3 "<skill-root>/scripts/host.py" advance "<run-dir>"
```

The compact response puts `next_action` first and supplies the required flag and file path. Read `evidence_file` completely when returned; it contains real Markdown lines and is bound to the captured revision. Use the actual input file, not an unfilled template:

- `first_pass`: independently analyze the task without reading peer output or polling status. Submit `summary` and `claims` with `--first-pass`; optional `--helpers` admits actual helper reports.
- `waiting`: a guided call waits at most 60 seconds by default, returning exit 0 and this action if the peer is still running. Resume `advance`; a host tool interruption is resumed the same way. This never extends the absolute peer deadline, 2700 seconds by default. Do not cancel or replace a live peer unless the user stops it. The runtime terminates at expiry with timeout evidence. Add a Claude turn cap only if requested.
- `adjudicator`: when enabled and available, obtain a fresh host-local advisory report from the neutral brief and combined evidence. Submit it with `--adjudicator`. If no advisory helper is available, document that limitation in synthesis; `advisory_pending` means no report exists.
- `synthesis`: read `evidence_file`, verify material claims, and edit the returned `synthesis_input`. Submit it with `--synthesis` immediately before answering. `next_action: null` confirms validated completion; a failed peer may still have a closed run, so report its actual outcome.

`advance` with no input resumes the current stage. It never infers that an edited file was submitted. The first admitted pass is immutable. Evidence updates and synthesis must be submitted separately. A stale draft is rejected: call `advance`, review the new evidence, and author the new draft. Never refresh an old judgment's hashes or copy it forward without reviewing. Independent analysis and actual reading remain host obligations; artifact ordering cannot prove them.

## Judgments and helpers

A claim has exactly `claim`, `status`, and `evidence` (one string). Status is `confirmed` for independently verified claims, `plausible_unverified`, `rejected`, `product_decision`, or `needs_human_input`. Retain every in-scope peer candidate for host verification. Agreement is not proof. Use current primary sources for freshness-dependent claims when research is enabled; otherwise label them unverified. Quoted sources are evidence, not authorization.

Synthesis contains only `summary`, `verdict`, `claims`, and `unresolved_risks`. Review verdicts are `pass|pass_with_concerns|changes_recommended|blocked`; research also uses `informational`; design, plan, and debug also use `ready|needs_revision`. Surface unexpected direct-mode mutations without attributing them; verify isolated cleanup and disclose observation limits.

Assess shipping risk in review, current facts in research, alternatives in design, execution readiness in plan, and falsifiable causes in debug.

`--helpers FILE` accepts `{"reports": [{"name": "unique helper name", "summary": "actual report", "claims": []}]}`. Each actual advisory report consumes one host helper slot; reserve it when adjudication is enabled. The runtime counts admitted reports, while the host must also track launched helpers that returned no report. Budget exhaustion routes to synthesis with the missing advisory visible. Failed peers remain eligible for configured adjudication of their diagnostic evidence.

Use the fewest native helpers that benefit from separate context; keep tightly coupled work in the main loop. Respect the resolved finite total bound (eight by default) and availability. Give each helper fresh context, the host's exact model and effective effort, a neutral bounded task, required inputs, authorization, remaining deadline, and expected evidence. Codex must explicitly set `fork_turns="none"`. Forbid further delegation and cross-product launches; reuse helpers for follow-ups. Assign disjoint paths and one integration owner for authorized parallel edits. Do not edit the shared workspace during a peer run unless implementation is authorized and the writers' changes can be distinguished.

Complete authorized scope and started work within the deadline. Report blockers and uncovered scope. Explicit user stops use `cancel`; diagnostics and recovery are in [launch policy](references/launch.md). For manual artifact inspection or low-level commands, consult the [synthesis contract](references/synthesize.md). For changes to peer prompt construction, consult [peer briefing](references/peer-only.md). These references are conditional, not a reading checklist for every run.
