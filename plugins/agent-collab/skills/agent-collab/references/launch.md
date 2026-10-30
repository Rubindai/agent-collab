# Launch Policy and Prerequisites

The normal workflow is self-contained in `SKILL.md`. Consult **Resolve the run** for explicit overrides and **Runtime boundaries and failures** for permissions, platform-specific supervision, or blocked runs. Paths are relative to the skill root containing `SKILL.md`; use only that root's current resources.

## Resolve the run

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

## Runtime boundaries and failures

Allow independent Agent Collab workflows in the same worktree. Keep an atomic guard entry for each run from `start` through `complete` or `cancel`, bound to its exact run-instance token and canonical owner. Registry admission reserves lifecycle growth. Use `status --active` to inspect owners across run roots and `recover RUN_ID` only for an abandoned starting entry with proven dead launcher identity; startup does not sweep other sessions. Peer depth markers and CLI shadows deter nested runs, but can be bypassed and are not a hard recursion boundary. Direct-mode snapshots may include changes made by other sessions; coordinate parallel edits and do not infer attribution. Serialize every guard transition and stale-start recovery with one stable per-worktree OS file lock and a bounded lock wait. A separate bounded per-run lock serializes terminal operations, and completion rebuilds the claim matrix from current admitted artifacts. Startup gates hold the wrapper's peer work and the provider launch until their process artifacts are committed, and guard release requires tracked process quiescence. The runtime hard-limits Claude peer total `Agent` calls, disables and denies Workflow orchestration, requires actual-run Stop effort evidence, configures Codex's documented concurrent-thread cap, and enforces helper-report admission. Codex total helper calls and V2 nesting depth remain instructions; V1 nesting is configured with `agents.max_depth=1`. Host-native helper launch discipline also remains instructional. The absolute peer deadline bounds tracked provider execution; detached POSIX groups, brokered Windows launches, and external sessions are outside that proof. On Windows, run `cancel` from the session that started the run.

In isolated mode the peer sees disposable copy-on-write views of HOME and the repository, a private temporary directory for its outputs, and its provider credential file bound through so token refreshes persist; host-owned run artifacts are not writable. The host records a small boundary receipt without scanning repository contents. `/tmp` and `/var/tmp` are fresh and `/dev` is minimal. This is not a containment boundary: network, sockets under `/run` (including a Docker daemon and the D-Bus session bus with the systemd user manager), MCP, plugins, and remote effects can still change the host and the real repository, and isolated mode takes no content snapshot that would detect such changes. In direct mode, mutation comparison is bounded detective evidence across tracked, untracked, most ignored, and selected Git-control surfaces. It excludes known high-volume generated/dependency directories and cannot observe transient changes reverted before a snapshot; large manifests retain complete digests with bounded path samples. Do not claim complete immutability or attribution.

When Linux safe mode is selected for a Codex peer, the runtime asks Codex to initialize its built-in read-only `bwrap` sandbox without launching a model. This OS capability check omits managed-profile remapping, while the actual peer retains managed policy. On macOS, Codex applies its own Seatbelt sandbox without an Agent Collab preflight; on native Windows, Codex safe mode fails with `sandbox_unavailable`. A Claude safe-mode peer uses provider-native `plan` permissions. Full-capability isolated mode separately preflights `fuse-overlayfs` and `bwrap` for either peer before run creation, and fails with `sandbox_unavailable` when unavailable. Safe mode resolves to direct workspace mode unless an explicit conflicting selection is rejected. Never retry a failed run with a different permission posture or workspace mode.

Use only strict settings and run schema 2.0. Reject legacy settings, runs, flags, package paths, installers, and output shapes instead of translating or probing them. Require Codex CLI 0.163.0 or newer, Claude Code 2.1.295 or newer, Python 3.11 or newer, and Linux, macOS, or Windows for peer process supervision. On Windows, provider CLIs must resolve to native `claude.exe` and `codex.exe`, and commands use `py -3` in place of `python3`. On invalid legacy settings or state, tell the user to archive or remove the artifact and rerun `setup` or use a fresh run root; never coerce it.

Useful commands:

```bash
python3 "<skill-root>/scripts/host.py" setup
python3 "<skill-root>/scripts/host.py" status
python3 "<skill-root>/scripts/host.py" result <run-id>
python3 "<skill-root>/scripts/host.py" complete <run-id> --synthesis <generated-input>
python3 "<skill-root>/scripts/host.py" clear-history --dry-run
python3 "<skill-root>/scripts/host.py" doctor
```
