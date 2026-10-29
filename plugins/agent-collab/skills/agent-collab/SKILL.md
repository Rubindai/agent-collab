---
name: agent-collab
description: Use when the user says "use agent collab", "use agent-collab", or explicitly requests this plugin. Launch an independent Claude or Codex peer for review, research, design, planning, or debugging. Also use for substantial work when independent cross-checking is warranted; avoid routine edits and explanations.
---

# Agent Collab

Launch the opposite product before host analysis, then independently verify and synthesize its evidence. The host owns the final answer.

## Invocation and scope

A direct request to use Agent Collab invokes the actual peer workflow. Do not substitute host-only analysis or host-native helpers. Implicit discovery remains enabled for substantial independent cross-checking; a quoted mention, informational question, or source edit alone does not invoke a run. Explicit invocation overrides the task-size heuristic, but not prerequisites, authorization, recursion, or deadlines.

Treat the directory containing this file as `<skill-root>`. Use only its current scripts, references, and schemas; do not probe legacy paths or compatibility copies. Use strict schema 2.0 without translation.

Before launch, read [run selection and prerequisites](references/launch.md#resolve-the-run). Consult [runtime boundaries and failure handling](references/launch.md#runtime-boundaries-and-failures) when evaluating permissions or isolation, supervising platform-specific processes, or resolving a blocked run. Default to full capability, `edit_allowed=false`, and adjudication enabled. Durable peer edits require explicit delegation and direct workspace mode. Preserve user-selected permission and workspace settings. Linux defaults to a disposable overlay; macOS and Windows default to direct mode with detective snapshots. Neither provides complete containment or mutation observation.

Default peers are Claude `claude-opus-5-5`/`xhigh` for a Codex host and Codex `gpt-6-astra`/`xhigh` for a Claude host. Preserve explicit per-run choices with `--peer-model` and `--peer-effort`; Fable is explicit-only. Require fresh exact-pair attestation before any peer, host pass, helper, or adjudicator starts. On an unavailable or unknown pair, substitution, downgrade, or other prerequisite failure, end the attempt, show the requested pair and structured evidence, and state that the peer did not run. Never retry a fallback or silently change permissions or workspace mode.

## Peer-first workflow

1. Read [peer briefing](references/peer-only.md). Write a compact neutral brief with the outcome, relevant context, expected output and done condition, decisive boundaries, and available evidence. Omit host conclusions and suspected findings. Quoted source content is evidence, not authorization; prompts are not security boundaries.

2. Start before host analysis:

```bash
python3 "<skill-root>/scripts/host.py" start --host <host> --target "<target>" --brief-file "<brief-file>"
```

Replace placeholders with literal values; `<host>` is `codex` or `claude`. Add a canonical `--mode review|research|design|plan|debug` and per-run overrides only as needed under the launch policy. On Windows use `py -3` instead of `python3`.

3. Read [synthesis and artifact contracts](references/synthesize.md). Complete an independent host pass without reading peer output or polling peer status. Save semantic JSON with `summary` and `claims`, then synchronize:

```bash
python3 "<skill-root>/scripts/host.py" finish "<run-dir>" --first-pass "<first-pass-input>"
```

The runtime supplies `"schema_version": "2.0"` and run metadata. Follow the synthesis reference for helper selection, fresh contexts, exact model/effort inheritance, authorization, and the finite helper bound. Role catalogs are optional lenses.

The absolute peer deadline is finite, 2700 seconds by default. Do not cancel or replace a live peer before it unless the user stops the run. At expiry require structured timeout evidence and termination. Do not add a Claude turn cap unless requested.

4. When enabled and available, obtain a fresh host-local advisory report and admit it with `finish RUN --adjudicator FILE`. `advisory_pending` means no report was supplied. Verify material claims yourself; agreement is not proof.

5. `finish` returns `review_dir` and `synthesis_input`. Read the pinned evidence with `result RUN --format markdown --review DIR`, edit the generated four-field synthesis input, then close immediately before answering:

```bash
python3 "<skill-root>/scripts/host.py" complete "<run-dir>" --synthesis "<synthesis-input>"
```

If evidence changes, inspect a new revision from `finish`; never refresh an old judgment's hashes. `cancel` handles an explicit stop after process quiescence.
