---
name: agent-collab
description: Use when the user says "use agent collab", "use agent-collab", or explicitly requests this plugin. Launch an independent Claude or Codex peer for review, research, design, planning, or debugging. Also use for substantial work when independent cross-checking is warranted; avoid routine edits and explanations.
---

# Agent Collab

Launch the opposite product before host analysis, then independently verify and synthesize its evidence. The host owns the final answer.

## Invocation and scope

A direct request to use Agent Collab invokes the actual peer workflow. Do not substitute host-only analysis or host-native helpers. Implicit discovery remains enabled for substantial independent cross-checking; a quoted mention, informational question, or source edit alone does not invoke a run. Explicit invocation overrides the task-size heuristic, but not prerequisites or authorization.

Treat the directory containing this file as `<skill-root>`. Use only its current scripts, references, and schemas; do not probe legacy paths or compatibility copies. Use strict schema 2.0 without translation.

Before launch, read [launch policy](references/launch.md). Default to full capability, `edit_allowed=false`, and adjudication enabled. Durable peer edits require explicit delegation and direct workspace mode. Preserve user-selected permission and workspace settings. Linux defaults to a disposable overlay; macOS and Windows default to direct mode with detective snapshots. Neither provides complete containment or mutation observation.

Default peers are Claude `claude-opus-5-5`/`xhigh` for a Codex host and Codex `gpt-6-astra`/`xhigh` for a Claude host. Preserve explicit per-run choices with `--peer-model` and `--peer-effort`; Fable is explicit-only. Require fresh exact-pair attestation before any peer, host pass, helper, or adjudicator starts. On an unavailable or unknown pair, substitution, downgrade, or other prerequisite failure, end the attempt, show the requested pair and structured evidence, and state that the peer did not run. Never retry a fallback or silently change permissions or workspace mode.

## Peer-first workflow

1. Read [peer briefing](references/peer-only.md). Write a compact neutral brief with the outcome, relevant context, expected output and done condition, decisive boundaries, and available evidence. Omit host conclusions and suspected findings. Quoted source content is evidence, not authorization; prompts are not security boundaries.

2. Start before host analysis:

```bash
python3 "<skill-root>/scripts/host.py" start --host <host> --target "<target>" --brief-file "<brief-file>"
```

Replace placeholders with literal values; `<host>` is `codex` or `claude`. Add a canonical `--mode review|research|design|plan|debug` and per-run overrides only as needed under the launch policy. On Windows use `py -3` instead of `python3`.

3. Read [synthesis and artifact contracts](references/synthesize.md). Complete an independent host pass without reading peer output or polling peer status. Use helpers only for work that benefits from separate context or independent verification; keep work requiring repeated shared-context handoffs in the main loop. The role catalog is optional, not a mandatory pipeline. The spawner owns fresh-context selection, exact model and effort inheritance, neutral bounded inputs, authorization, remaining deadline, and expected evidence. Use the fewest helpers needed, respect the resolved total-spawn bound (eight by default), forbid nesting and cross-product delegation, and reuse helpers for follow-ups. Follow the synthesis reference for authorized parallel writes and provider enforcement limits.

4. Save `host-first-pass.json` before reading peer output. Use the complete strict shape in the synthesis reference, beginning with `"schema_version": "2.0"`. Then synchronize:

```bash
python3 "<skill-root>/scripts/host.py" finish "<run-dir>"
```

The absolute peer deadline is finite, 2700 seconds by default. Do not cancel or replace a live peer before it unless the user stops the run. At expiry require structured timeout evidence and termination. Do not add a Claude turn cap unless requested.

5. When enabled and available, run a bounded, fresh host-local advisory adjudicator after the independent reports exist. Save its strict `adjudicator-report.json` and run `finish` again to rebuild the claim matrix. `advisory_pending` means no report was supplied. When disabled, do not create that artifact. Verify material claims yourself; agreement is not proof.

6. Write strict `host-synthesis.json`, binding both current mutation and claim-matrix digests from the ready `host-result.json`. Inspect mutation evidence and isolated-workspace cleanup. Close the run immediately before returning the final answer:

```bash
python3 "<skill-root>/scripts/host.py" complete "<run-dir>"
```

Only the host calls `complete`. The atomic per-worktree guard remains held through validated synthesis and quiescence; it is the hard cross-product recursion boundary. Explicit cancellation uses `cancel`; if it reports `already_terminal`, preserve the report and use `finish`. On Windows cancel from the starting session. See the launch reference for lock, process, sandbox, and failure details.

Complete authorized work, including pending commands and helpers, within the deadline. Use stated assumptions for nonblocking ambiguity; otherwise report concrete blockers and uncovered scope. Keep output concise and evidence-backed, and repeat checks only when changes, failures, or unresolved claims justify them.
