---
name: agent-collab
description: Use for high-stakes repo-grounded collaboration where an independent Claude or Codex peer provides a challenge-first second opinion. Trigger for strict review, security-sensitive work, current source-backed research, architecture tradeoffs, implementation plans, debugging, risky verification, or explicit Claude/Codex cross-checking. Avoid routine explanations, simple edits, formatting, naming, and low-risk questions.
---

# Agent Collab

Launch the opposite product as an independent peer before host analysis, then verify and synthesize the evidence as the host.

## Resolve the run

Treat the directory containing this file as `<skill-root>`. Use only its current `scripts/`, `references/`, and `schemas/`; do not search legacy paths, caches, or compatibility copies.

Default to the configured full-capability launch settings, `adjudicator_enabled=true`, and `edit_allowed=false`. Full capability enables investigation but does not authorize repository changes. It has host-level consequences: Anthropic recommends Claude `bypassPermissions` only inside isolated containers, VMs, or development containers without internet access where host damage is impossible. Use safe mode when the user selects it. Add `--edit-allowed` only when the user explicitly delegates edits.

Resolve the opposite-product peer:

- Codex host: default Claude peer `claude-opus-5` with `max`.
- Claude host: default Codex peer `gpt-5.6-sol` with `max`.
- Preserve an explicit user model and effort choice with per-run `--peer-model` and `--peer-effort` flags. Resolve only documented aliases such as “Fable 5 Max” to `claude-fable-5` and `max`.
- For a Codex peer, do not use a hardcoded effort list. Require the exact pair to appear in the freshly refreshed `codex debug models` catalog. The current `gpt-5.6-sol` entry advertises `ultra`, which enables automatic delegation.
- For a Claude peer, accept `low`, `medium`, `high`, `xhigh`, `max`, or `ultracode`. `ultracode` is an explicit CLI orchestration mode whose effective provider effort is `xhigh`; preserve the requested mode in the attestation while requiring the Stop hook to report `xhigh`.
- Treat Fable as explicit-only. Never obtain it from built-in, environment, local, or global defaults.
- Require `start` to attest availability before it creates a run or launches a peer. Codex uses the refreshed model catalog. In a live configuration-reduced Claude probe, API `modelUsage` must include the exact resolved model and the `Stop` hook must report the expected effective effort.
- Never choose or retry a fallback model or effort. If the attestation is `unavailable` or `unknown`, or the observed pair differs, end the Agent Collab attempt immediately: do not start the peer, host-first pass, helpers, or adjudicator. Preserve and show the structured result, tell the user which pair was requested, and ask them to choose an available pair or obtain access.
- Add `--online-research` or `--no-online-research` when the user specifies research availability.
- Add `--safe-mode` or `--no-safe-mode`, `--timeout-seconds`, `--local-subagents` or `--no-local-subagents`, and `--max-local-subagents` when the user explicitly overrides those values for one run. Persisted settings otherwise apply.
- Do not add a Claude turn cap by default. Anthropic documents no limit by default, and the finite peer deadline is the mandatory liveness bound. Add `--claude-max-turns N` only when the user explicitly requests a per-run turn budget.

General precedence is per-run flag, environment, local settings, global settings, then built-in. The explicit-only Fable rule overrides that general precedence.

## Write the peer brief

Create a compact, escaped, neutral brief before host analysis. Follow `references/peer-only.md` and current provider prompting guidance:

- State the desired outcome first.
- Include only context that can change the answer.
- Name the expected output and what done means.
- Ask for concise, deduplicated output and a concrete stopping condition; do not add generic self-review or repeated-verification instructions.
- Include decisive boundaries such as scope, edit authorization, research availability, and recursion.
- Give the peer available tests, commands, or other verification signals.
- Leave room for independent investigation; omit host conclusions, suspected findings, and implementation defense.

Treat the peer brief as an instruction, not a sandbox or a higher-precedence security boundary. Rely on runtime launch checks, the Codex safe-mode sandbox or Claude permission mode selected for the run, structured-output validation, and the worktree guard for technical controls.

## Run peer-first

1. Start the peer before doing host analysis:

```bash
python "<skill-root>/scripts/host.py" start \
  --host "$host" \
  --target "$target" \
  --brief-file "$brief_file"
```

Set `$host` to `codex` or `claude`. Add a canonical `--mode review|research|design|plan|debug` only when an override is useful. Add only the per-run model, effort, research, permission, timeout, helper, adjudicator, and edit flags resolved from the rules above; otherwise let persisted settings apply.

2. While the peer runs, complete an independent challenge-first host pass. Use the fewest host-local helpers needed, only for sizeable independent workstreams, and never exceed the resolved finite count or instructed depth, eight helpers by default. Give each only the neutral brief and its lens, and forbid Agent Collab, provider peer commands, and further cross-product delegation. In full mode do not reduce their native tools or permissions; provider policy still applies. Claude peer total Agent calls and one-layer spawn depth are runtime-enforced; Codex peer concurrency is provider-enforced while total calls and depth remain instruction-level bounds.

3. Save the independent pass as strict schema 2.0 `host-first-pass.json` before reading peer output. Its object starts with `"schema_version": "2.0"`; use the complete exact artifact shape in `references/synthesize.md`.

4. Use `finish` as the normal synchronization point:

```bash
python "<skill-root>/scripts/host.py" finish "$run_dir"
```

Respect the hard finite peer timeout, 2700 seconds by default. Do not cancel or replace a live peer before its deadline unless the user explicitly stops it. At the deadline require the runtime to terminate the peer and preserve a structured timeout result; never continue indefinitely.

5. When adjudication is enabled, run a bounded host-local advisory adjudicator when available. This is a procedural independence requirement; the runtime validates the artifact but cannot prove what the adjudicator read. `advisory_pending` means no adjudicator report was supplied. If an adjudicator writes `adjudicator-report.json`, run `finish` again to rebuild the claim matrix. When adjudication is disabled, do not create that artifact. Verify important host, peer, helper, and adjudicator claims yourself.

6. Synthesize with `references/synthesize.md`, bind the strict `host-synthesis.json` attestation to the ready `host-result.json` mutation digest, and then close the run immediately before returning the final answer:

```bash
python "<skill-root>/scripts/host.py" complete "$run_dir"
```

Never call `complete` from a helper or before host synthesis is ready. An explicit `cancel` writes separate immutable cancellation evidence and releases the guard only after quiescence. If `cancel` reports `already_terminal`, preserve the terminal peer report and use `finish` instead.

## Safety and failure behavior

Allow only one Agent Collab workflow per Git worktree. Keep the atomic worktree guard as the hard cross-product recursion boundary from `start` through `complete` or `cancel`. Serialize every guard transition and stale-start recovery with one stable per-worktree OS file lock and a bounded lock wait. A separate bounded per-run lock serializes terminal operations, and completion rebuilds the claim matrix from current admitted artifacts. Startup gates prevent wrapper/provider execution before their process artifacts are committed, and guard release requires tracked process quiescence. The runtime hard-limits Claude peer total `Agent` calls, configures Codex's documented concurrent-thread cap, and enforces helper-report admission. Codex total helper calls and depth, plus host-native helper launch discipline, remain instructions because this runtime cannot intercept them. The absolute peer deadline bounds aggregate runtime.

Treat workspace mutation comparison as bounded detective evidence. It covers tracked, untracked, most ignored, and selected Git-control surfaces, including crash and cancel paths. It excludes known high-volume generated/dependency directories and cannot observe transient changes reverted before a snapshot; `.next` is excluded only for generated/untracked/ignored scanning, so tracked `.next` changes remain visible through Git. Do not claim complete immutability or attribution.

When Linux safe mode is selected for a Codex peer, the runtime preflights the `bwrap` backend required by Codex read-only sandboxing. If it cannot initialize, `start` emits a structured sandbox-unavailable result before creating a run. A Claude safe-mode peer uses provider-native `plan` permission mode; do not describe `plan` as an OS container. Agent Collab itself neither requests nor preflights `bwrap` for Claude or in full-capability mode, although separately loaded Claude settings can enable Claude's sandbox. Never retry a failed run with a different permission posture.

Use only Agent Collab 2.0.0 strict settings and run schema 2.0. Reject legacy settings, runs, flags, package paths, installers, and output shapes instead of translating or probing them. Require Codex CLI 0.145.0 or newer, Claude Code 2.1.219 or newer, Python 3.10 or newer, and Linux or macOS for peer process supervision. On invalid legacy settings or state, tell the user to archive or remove the artifact and rerun `setup` or use a fresh run root; never coerce it.

Useful commands:

```bash
python "<skill-root>/scripts/host.py" setup
python "<skill-root>/scripts/host.py" status
python "<skill-root>/scripts/host.py" result "$run_id"
python "<skill-root>/scripts/host.py" complete "$run_id"
python "<skill-root>/scripts/host.py" clear-history --dry-run
python "<skill-root>/scripts/host.py" doctor
```
