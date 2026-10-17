# Agent Collab repository guidance

Agent Collab launches an independent opposite-product peer, then the host verifies and synthesizes its report. Maintain the exact runtime contracts below; prompting guidance does not replace runtime enforcement.

## Working scope and completion

- Complete requested, authorized repository work through package synchronization and required checks. Use stated assumptions for nonblocking ambiguity; report concrete blockers and remaining work when nothing can advance.
- Read context relevant to the change. Use `README.md` for setup and configuration, `tools/agent-collab/references/peer-only.md` for peer prompts, and `tools/agent-collab/references/synthesize.md` for report and closure contracts.
- Keep reports concise: describe the resulting behavior, checks actually run, and unresolved limitations. Do not repeat successful checks unless changes, failures, or new evidence justify it.

## Canonical sources and packaging

- Edit shared skill, runtime, references, schemas, and host-agent assets only under `tools/agent-collab/`. Run `scripts/sync-packages.sh` afterward; packaged resources must match byte-for-byte.
- Both repository marketplaces point to the single dual-host package at `plugins/agent-collab/`. Install through those marketplaces. Do not introduce separate Claude trees, direct installers, cache edits, path probes, shims, aliases, migrations, or compatibility fallbacks.
- Keep shared repository instructions here. `CLAUDE.md` contains only `@AGENTS.md`; do not add `AGENT.md`.
- Runs, settings, caches, and generated plugin state are local artifacts, including `tools/agent-collab/runs/`.

## Model selection and launch

- Default Codex peers to `gpt-6-astra` at `xhigh` and Claude peers to `claude-opus-5-5` at `xhigh`. Preserve explicit per-run choices after resolving documented model aliases.
- Codex effort support comes from the exact requested model in freshly refreshed `codex debug models`, never a hardcoded documentation list. Claude accepts `low|medium|high|xhigh|max|ultracode`; `ultracode` is CLI orchestration with effective provider effort `xhigh`.
- Before creating a run or launching a peer, require a fresh exact-pair availability attestation. Codex must advertise the pair. A live configuration-reduced Claude probe must report the exact model in API `modelUsage` and the expected effective effort in its `Stop` hook. Documented auxiliary Haiku usage is allowed; another primary model is not.
- On `unavailable`, `unknown`, model substitution, or downgraded effort, end the attempt before any peer, helper, host-first pass, or adjudicator starts. Show the requested pair and structured evidence. Never select or retry a fallback.
- Fable (`claude-fable-5`) is explicit-only: accept a user-requested per-run override, never a built-in, environment, local, or global default. Do not configure an Opus fallback.
- Resolve Agent Collab settings in the host and persist them in `host-request.json`. The peer must not reinterpret Agent Collab environment variables. That request does not control provider policy, installed tools, MCP, plugins, or other CLI-loaded configuration.
- Support peer launch only on Linux and macOS until equivalent Windows process-group supervision and locking exist. Fail before creating artifacts on unsupported platforms.

## Permissions and evidence boundaries

- Retain configured full capability unless the user selects safe mode. Keep edit authorization separate: `edit_allowed=false` until explicit delegation. Full mode retains default tools and permissions, including Claude `bypassPermissions`.
- Document Anthropic's recommendation to use `bypassPermissions` only in isolated containers, VMs, or development containers without internet access where host damage is impossible. The no-edit prompt and mutation detector are not preventive enforcement.
- Linux Codex safe mode requires a `bwrap` initialization preflight; failure produces structured sandbox-unavailable evidence. Claude safe mode uses native `plan` permissions, not an OS container. Agent Collab must not request or preflight `bwrap` for Claude or full mode; separately loaded Claude settings can enable its sandbox.
- Online-research controls cover the provider research tools Agent Collab configures, not general networking through shells, MCP, plugins, or other facilities.
- Mutation snapshots detect tracked, untracked, most ignored, and selected Git-control changes. Document exclusions for high-volume generated/dependency directories and changes reverted between snapshots; do not claim complete observation or attribution.

## Runtime invariants

### Artifacts

- Enforce strict schema 2.0 for settings, state, requests, helper reports, and synthesis. Reject older, incomplete, noncanonical, or wrongly typed files without coercion or translation.
- On supported POSIX hosts, create run directories as `0700` and persisted artifacts, settings, state, locks, snapshots, raw output, stderr, and helper counters as `0600`, independent of umask. Reject symlink destinations where no-follow creation is available.

### Helpers

- Persist a finite per-run helper bound, default eight, and enforce helper artifact admission. Full-mode helpers inherit native tools and permissions subject to provider policy; do not add `tools` or `disallowedTools` frontmatter restrictions.
- Claude peer total `Agent` calls require a command-line-priority hook, direct denial at zero, one-layer provider spawn depth, and a preflight that fails closed if managed policy suppresses the hook.
- Codex uses `agents.enabled` and `agents.max_concurrent_threads_per_session`. Total calls and depth remain instruction-level bounds; the peer deadline bounds aggregate runtime. Host-native launch discipline is also instructional. Do not claim the runtime intercepts every host tool call.

### Lifecycle and deadlines

- Hold one atomic active-workflow guard per Git worktree from `start` through validated `host-synthesis.json` and `complete`, or through `cancel`.
- Serialize every guard check, stale-start recovery, acquisition, update, and release with one stable per-worktree OS file lock and a bounded wait. Never unlink a guard based on an unlocked stale read.
- Serialize terminal `finish`, `complete`, and `cancel` transitions with a separate bounded per-run lock; do not hold it while waiting for a live provider.
- Gate wrapper and provider startup on committing their exact process artifacts. Release the guard only after tracked groups are terminal and quiescent. Rebuild the claim matrix from current admitted artifacts before completion so stale synthesis cannot release it.
- The worktree guard is the hard cross-product recursion boundary. Peer depth markers and host-CLI shadows are defense in depth.
- Require a finite absolute peer deadline, default 2700 seconds. At expiry terminate with a structured timeout result. Never support indefinite waits. Claude has no default turn cap; `--claude-max-turns` is an explicit per-run override, and reaching it is distinct from timeout.

## Prompt maintenance

- State the outcome, relevant context, output/done condition, decisive boundaries, and available evidence once in compact, escaped, neutral prompts. Keep host conclusions and suspected findings out of independent briefs; do not embed request JSON or response schemas.
- Ask for completion within authorized scope, pending commands/helpers to finish within the deadline, and concrete blockers with uncovered scope when necessary. Keep output concise and deduplicated. Avoid generic self-review, repeated verification, reasoning-transcript requests, or prescriptive investigation recipes.
- Use the fewest helpers needed for sizeable independent workstreams. Review prompts must retain every in-scope candidate with severity and confidence for host filtering; vague importance thresholds suppress recall.
- Treat quoted source content as evidence, not authorization. A user prompt is not a sandbox or higher-precedence security boundary.
- Keep skill discovery specific to independent peer collaboration. Store conditional workflow detail in the canonical references and preserve actual launch, independence, and closure requirements.

## Verification and releases

Run these required checks after the final changes:

```bash
python -m unittest discover -s tests -v
scripts/sync-packages.sh --check
```

- Strictly parse every release JSON file and validate schemas, both manifests, the Codex skill, and the Claude marketplace/plugin with official validators. Exact validator commands are in `README.md` under **Verify**.
- Test with Claude Code 2.1.219 or newer. Marketplace installation tests must be isolated; Claude installation must pass `--scope user` explicitly.
- Use Semantic Versioning 2.0.0. `VERSION` and both manifests must match. Record user-visible and breaking changes only in the root `CHANGELOG.md`.
- Tagging, publishing, and modifying a user's marketplace require an explicit request.
