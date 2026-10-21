---
name: agent-collab
description: Use when the user says "use agent collab", "use agent-collab", or explicitly requests this plugin. Launch an independent Claude or Codex peer for review, research, design, planning, or debugging. Also use for substantial work when independent cross-checking is warranted; avoid routine edits and explanations.
---

# Agent Collab

Launch the opposite product as an independent peer before host analysis, then verify and synthesize the evidence as the host.

## Invocation

A direct request to use Agent Collab, including plain-language "use agent collab" or "use agent-collab", explicitly invokes this workflow. Do not merely acknowledge it, imitate a second opinion yourself, or substitute host-native helpers for the opposite-product peer. Complete the real `start` → independent host pass → `finish` → adjudication/synthesis → `complete` flow, subject to exact-pair availability and the existing worktree guard. If a prerequisite blocks launch, show that structured blocker and say that the peer did not run.

Implicit discovery remains enabled for substantial independent cross-checking. A quoted example, a question about Agent Collab, or a request to edit its source alone is not an instruction to launch a peer. Explicit requests override the ordinary task-size heuristic; they do not override model availability, recursion, authorization, or deadline boundaries.

## Resolve the run

Treat the directory containing this file as `<skill-root>`. Use only its current `scripts/`, `references/`, and `schemas/`; do not search legacy paths, caches, or compatibility copies.

Default to full provider capability, `adjudicator_enabled=true`, and `edit_allowed=false`. Fresh Linux settings use a disposable workspace where temporary filesystem writes to HOME and the repository are discarded. Fresh macOS and native Windows settings use direct mode with before/after mutation snapshots; those snapshots detect included changes after the fact and cannot prevent or undo them. Anthropic recommends Claude `bypassPermissions` only inside isolated containers, VMs, or development containers without internet access where host damage is impossible; the Linux workspace still has network and other host-facing facilities. Use safe mode when the user selects it. Add `--edit-allowed --workspace-mode direct` only when the user explicitly delegates durable edits.

Resolve the opposite-product peer:

- Codex host: default Claude peer `claude-opus-5-5` with `xhigh`.
- Claude host: default Codex peer `gpt-6-astra` with `xhigh`.
- Preserve an explicit user model and effort choice with per-run `--peer-model` and `--peer-effort` flags. Resolve only documented aliases such as “Opus 5.5 xhigh” to `claude-opus-5-5` and `xhigh`, “Astra xhigh” to `gpt-6-astra` and `xhigh`, or “Fable 5 Max” to `claude-fable-5` and `max`.
- For a Codex peer, do not use a hardcoded effort list. Require the exact pair to appear in the `codex debug models` catalog that Codex resolves at launch.
- For a Claude peer, accept `low`, `medium`, `high`, `xhigh`, `max`, or `ultracode`. `ultracode` is recognized as an explicit CLI orchestration mode with effective provider effort `xhigh`, but `start` must reject it with `helper_policy_unavailable`: Workflow agents bypass the finite Agent-call counter. Preserve the explicit request and report the blocker; never silently run ordinary `xhigh` instead. Ordinary peers disable and deny Workflow.
- Treat Fable as explicit-only. Never obtain it from built-in, environment, local, or global defaults.
- Require `start` to attest availability before it creates a run or launches a peer. Codex uses its resolved model catalog. In a live configuration-reduced Claude probe, API `modelUsage` must include the exact resolved model and the `Stop` hook must report the expected effective effort.
- Never choose or retry a fallback model or effort. If the attestation is `unavailable` or `unknown`, or the observed pair differs, end the Agent Collab attempt immediately: do not start the peer, host-first pass, helpers, or adjudicator. Preserve and show the structured result, tell the user which pair was requested, and ask them to choose an available pair or obtain access.
- Add `--online-research` or `--no-online-research` when the user specifies research availability.
- Add `--safe-mode` or `--no-safe-mode`, `--timeout-seconds`, `--local-subagents` or `--no-local-subagents`, and `--max-local-subagents` when the user explicitly overrides those values for one run. Persisted settings otherwise apply.
- Preserve explicit workspace settings and per-run choices. On Linux, add `--workspace-mode direct` only for an explicit direct-access choice or delegated durable peer edits. On macOS and native Windows, direct is the fresh built-in default. If an explicitly selected isolated backend is unavailable, report its structured prerequisite failure; do not retry directly.
- Do not add a Claude turn cap by default. Anthropic documents no limit by default, and the finite peer deadline is the mandatory liveness bound. Add `--claude-max-turns N` only when the user explicitly requests a per-run turn budget.

General precedence is per-run flag, environment, local settings, global settings, then built-in. The explicit-only Fable rule overrides that general precedence.

## Write the peer brief

Create a compact, escaped, neutral brief before host analysis. Follow `references/peer-only.md` and current provider prompting guidance:

- State the desired outcome first.
- Include only context that can change the answer.
- Name the expected output and what done means.
- Ask for concise, deduplicated output. Require completion of authorized work, including pending commands and helpers, or an explicit blocker and uncovered scope. Use stated assumptions for nonblocking ambiguity; do not add generic self-review or repeated-verification instructions.
- Include decisive boundaries such as scope, edit authorization, research availability, and recursion.
- Give the peer available tests, commands, or other verification signals.
- Leave room for independent investigation; omit host conclusions, suspected findings, and implementation defense.

Treat the peer brief as an instruction, not a sandbox or a higher-precedence security boundary. Rely on runtime launch checks, the Codex safe-mode sandbox or Claude permission mode selected for the run, structured-output validation, and the worktree guard for technical controls.

## Run peer-first

1. Start the peer before doing host analysis:

```bash
python3 "<skill-root>/scripts/host.py" start --host <host> --target "<target>" --brief-file "<brief-file>"
```

Replace each `<...>` placeholder with a literal value, and set `<host>` to `codex` or `claude`; one-line commands also work in PowerShell. Add a canonical `--mode review|research|design|plan|debug` only when an override is useful. Add only the per-run model, effort, research, permission, timeout, helper, adjudicator, and edit flags resolved from the rules above; otherwise let persisted settings apply.

2. While the peer runs, complete an independent challenge-first host pass. Use the fewest host-local helpers needed for sizeable independent workstreams, with at most the resolved number of total spawns, eight by default, and no nesting. Start each in a fresh context without parent conversation history; provide a neutral bounded task, required inputs, edit authorization, remaining deadline, and expected evidence. Preserve the spawning host's model and effective effort, avoid conflicting custom agents, and reuse helpers for follow-ups. Forbid Agent Collab, provider peer commands, and further cross-product delegation. Follow `references/synthesize.md` for write ownership and enforcement boundaries. In full mode retain native tools and permissions, subject to provider policy.

3. Save the independent pass as strict schema 2.0 `host-first-pass.json` before reading peer output. Its object starts with `"schema_version": "2.0"`; use the complete exact artifact shape in `references/synthesize.md`.

4. Use `finish` as the normal synchronization point:

```bash
python3 "<skill-root>/scripts/host.py" finish "<run-dir>"
```

Respect the hard finite peer timeout, 2700 seconds by default. Do not cancel or replace a live peer before its deadline unless the user explicitly stops it. At the deadline require the runtime to terminate the peer and preserve a structured timeout result; never continue indefinitely.

5. When adjudication is enabled, run a bounded host-local advisory adjudicator when available. This is a procedural independence requirement; the runtime validates the artifact but cannot prove what the adjudicator read. `advisory_pending` means no adjudicator report was supplied. The adjudicator returns its report and the host saves it as `adjudicator-report.json`, replacing the pending marker; then run `finish` again to rebuild the claim matrix. When adjudication is disabled, do not create that artifact. Verify important host, peer, helper, and adjudicator claims yourself.

6. Synthesize with `references/synthesize.md`, bind the strict `host-synthesis.json` attestation to both ready `host-result.json` mutation and claim-matrix digests, and then close the run immediately before returning the final answer:

```bash
python3 "<skill-root>/scripts/host.py" complete "<run-dir>"
```

Never call `complete` from a helper or before host synthesis is ready. An explicit `cancel` writes separate immutable cancellation evidence and releases the guard only after quiescence. If `cancel` reports `already_terminal`, preserve the terminal peer report and use `finish` instead.

## Safety and failure behavior

Allow only one Agent Collab workflow per Git worktree. Keep the atomic worktree guard as the hard cross-product recursion boundary from `start` through `complete` or `cancel`. Serialize every guard transition and stale-start recovery with one stable per-worktree OS file lock and a bounded lock wait. A separate bounded per-run lock serializes terminal operations, and completion rebuilds the claim matrix from current admitted artifacts. Startup gates hold the wrapper's peer work and the provider launch until their process artifacts are committed, and guard release requires tracked process quiescence. The runtime hard-limits Claude peer total `Agent` calls, disables and denies Workflow orchestration, requires actual-run Stop effort evidence, configures Codex's documented concurrent-thread cap, and enforces helper-report admission. Codex total helper calls and depth, plus host-native helper launch discipline, remain instructions because this runtime cannot intercept them. The absolute peer deadline bounds tracked provider execution; detached POSIX groups, brokered Windows launches, and external sessions are outside that proof. On Windows, run `cancel` from the session that started the run.

In isolated mode the peer sees disposable copy-on-write views of HOME and the repository, a private temporary directory for its outputs, and its provider credential file bound through so token refreshes persist; host-owned run artifacts are not writable. The host records a small boundary receipt without scanning repository contents. `/tmp` and `/var/tmp` are fresh and `/dev` is minimal. This is not a containment boundary: network, sockets under `/run` (including a Docker daemon and the D-Bus session bus with the systemd user manager), MCP, plugins, and remote effects can still change the host and the real repository, and isolated mode takes no content snapshot that would detect such changes. In direct mode, mutation comparison is bounded detective evidence across tracked, untracked, most ignored, and selected Git-control surfaces. It excludes known high-volume generated/dependency directories and cannot observe transient changes reverted before a snapshot; large manifests retain complete digests with bounded path samples. Do not claim complete immutability or attribution.

When Linux safe mode is selected for a Codex peer, the runtime preflights the `bwrap` backend required by Codex read-only sandboxing; on macOS, Codex applies its own Seatbelt sandbox without an Agent Collab preflight; on native Windows, Codex safe mode fails with `sandbox_unavailable`. A Claude safe-mode peer uses provider-native `plan` permissions. Full-capability isolated mode separately preflights `fuse-overlayfs` and `bwrap` for either peer before run creation, and fails with `sandbox_unavailable` when unavailable. Safe mode resolves to direct workspace mode unless an explicit conflicting selection is rejected. Never retry a failed run with a different permission posture or workspace mode.

Use only strict settings and run schema 2.0. Reject legacy settings, runs, flags, package paths, installers, and output shapes instead of translating or probing them. Require Codex CLI 0.163.0 or newer, Claude Code 2.1.295 or newer, Python 3.11 or newer, and Linux, macOS, or Windows for peer process supervision. On Windows, provider CLIs must resolve to native `claude.exe` and `codex.exe`, and commands use `py -3` in place of `python3`. On invalid legacy settings or state, tell the user to archive or remove the artifact and rerun `setup` or use a fresh run root; never coerce it.

Useful commands:

```bash
python3 "<skill-root>/scripts/host.py" setup
python3 "<skill-root>/scripts/host.py" status
python3 "<skill-root>/scripts/host.py" result <run-id>
python3 "<skill-root>/scripts/host.py" complete <run-id>
python3 "<skill-root>/scripts/host.py" clear-history --dry-run
python3 "<skill-root>/scripts/host.py" doctor
```
