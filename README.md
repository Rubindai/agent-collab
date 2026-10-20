# Agent Collab

Agent Collab is a peer-first, challenge-first collaboration plugin for Codex and Claude Code. It launches the opposite product with a neutral brief, keeps the host analysis independent, then validates and combines the evidence.

Version 4.0.0 adds platform-aware workspace modes. Linux full-capability runs default to a disposable copy-on-write workspace; fresh macOS settings default to direct mode with bounded snapshots. The new required `workspace_mode` setting is a breaking change to persisted settings, so recreate existing settings before upgrading. Strict schema 2.0 remains in force, and 1.x settings and run artifacts still have no compatibility layer.

**Last comprehensive evaluation: 2026-10-18.** All README sections, repository instructions, package contents, and runtime controls were reassessed against current official documentation and local evidence. The evaluation covers correctness and documented behavior; it does not establish optimal review quality, latency, or cost. See [Evaluation evidence](#evaluation-evidence) for scope and outstanding checks. Version 3.0.0 appeared on GitHub `main` without a release tag; formal tags begin with `v4.0.0`.

## Defaults

| Peer | Default model | Effort |
| --- | --- | --- |
| Codex | `gpt-6-astra` | `xhigh` |
| Claude Code | `claude-opus-5-5` | `xhigh` |

These are the project defaults requested for Agent Collab. Existing persisted settings and per-run overrides retain precedence; installing this version does not rewrite them. Every requested pair still requires fresh availability evidence, with no fallback.

The user can select a model and reasoning or effort level in natural language. The host translates an explicit selection into the per-run flags `--peer-model` and `--peer-effort`, then requires a fresh availability attestation for that exact pair before launch.

Claude peer effort may be `low`, `medium`, `high`, `xhigh`, or `max`, subject to exact availability. Claude Code also recognizes `ultracode`, an orchestration mode with effective provider effort `xhigh`. Agent Collab rejects an explicit `ultracode` run with `helper_policy_unavailable` before creating run artifacts: a live test showed that Workflow agents bypass the finite Agent-call counter, and the documented Workflow agent budget does not implement this per-run bound. The runtime disables and denies Workflow for ordinary peers. It never substitutes `xhigh` for an explicit `ultracode` request. `check-availability` still reports provider support separately from this execution-policy restriction. [Claude workflows](https://code.claude.com/docs/en/workflows#behavior-and-limits)

Codex effort support is not a hardcoded Agent Collab list. Immediately before launch, the runtime refreshes `codex debug models` and accepts only an effort advertised for the exact requested model. The refreshed catalog is authoritative; no static effort list determines Codex availability. Actual-run Codex selection events are checked when emitted, and every observed conflict is rejected. If those events are absent, the evidence is the fresh catalog plus explicit launch arguments, not an independently observed provider selection.

Examples:

```text
Use Agent Collab with Fable 5 Max to review this change.
Use Agent Collab with Opus 5.5 xhigh to audit the release.
Use Agent Collab and run the Codex peer on gpt-6-astra at high effort.
```

“Fable 5 Max” is the explicit alias for `claude-fable-5` with `max`. Fable is available only as a user-requested per-run model; it is never a built-in, environment, local, or global default.

Availability is checked before a run is created or a peer is launched:

- Codex must advertise the exact model and effort in the freshly refreshed `codex debug models` catalog.
- Claude Code must complete a bounded, configuration-reduced live probe whose API result `modelUsage` includes the exact resolved model and whose `Stop` hook reports the expected effective effort. The probe runs outside the target repository with filesystem setting sources, built-in tools, and MCP servers disabled; managed provider policy can still apply. Claude Code may also report its documented auxiliary Haiku usage; another primary model is a mismatch. For `ultracode`, the expected provider level is `xhigh`; for every provider-level request it is the requested value. A downgrade or organization cap does not pass as the requested pair.

Only an `available` attestation proceeds. `unavailable` and `unknown` stop the entire collaboration attempt before a run, peer, helper, host-first-pass, or adjudicator is created, and the host must notify the user with the probe evidence. Model/effort rejection, authentication or authorization denial, and provider quota/rate-limit responses are reported as unavailable; timeouts, malformed telemetry, and indeterminate provider failures remain unknown. Agent Collab never substitutes or retries another model or effort.

Per-run flags have highest priority:

```text
per-run flag > environment > local settings > global settings > built-in
```

The explicit-only Fable rule is stricter than this general precedence order: `claude-fable-5` must come from a user-requested per-run override.

## Requirements

- Codex CLI 0.163.0 or newer
- Claude Code 2.1.295 or newer
- Python 3.10 or newer
- Git for workspace detection and Git-aware snapshots; non-Git directories use filesystem snapshots
- Linux or macOS with POSIX process groups and file locking. Windows peer launch is rejected before run artifacts are created because equivalent supervision is not implemented.
- On Linux, the default disposable workspace requires `fuse-overlayfs`, `fusermount3` (or `fusermount`), and `bwrap` with working FUSE/user namespaces. `start` runs a write/discard probe before creating a run. Fresh macOS settings default to direct mode with bounded persisted snapshots. Explicit isolated settings on macOS fail before run creation; they never fall back to direct.

These minimums cover the current Codex helper-default controls and Claude's forced helper-model selection. `start` checks the required peer CLI version before creating a run directory. Older CLIs are rejected; no alternate flags or commands are attempted.

## Install

### Linux isolation dependencies

Linux full-capability runs use a disposable workspace and need Bubblewrap, FUSE OverlayFS, and a FUSE unmount tool. From a repository checkout, check for the required commands or install the distribution packages:

```bash
scripts/install-linux-isolation-deps.sh --check
scripts/install-linux-isolation-deps.sh --install
python tools/agent-collab/scripts/host.py doctor
```

The script installs `bubblewrap`, `fuse-overlayfs`, and `fuse3` with `apt-get` on Debian/Ubuntu or `dnf` on Fedora. It uses `sudo` when needed and skips package installation when the commands are already available. With isolated workspace mode selected, `doctor` verifies that FUSE and user namespaces work on this host; having the commands installed alone is insufficient. On other Linux distributions, install those packages with the distribution's package manager, then run `doctor`. The script does not install the Agent Collab plugin; use the host marketplace steps below.

### Codex

This repository exposes a repo marketplace at `.agents/plugins/marketplace.json` and the dual-host plugin at `plugins/agent-collab`.

For a local checkout:

```bash
codex plugin marketplace add .
codex plugin add agent-collab@agent-collab
```

From GitHub:

```bash
codex plugin marketplace add Rubindai/agent-collab --ref main
codex plugin add agent-collab@agent-collab
```

To pin an exact release instead of tracking `main`, replace `--ref main` with `--ref v4.0.0` (or another published version tag). A pinned marketplace stays on that tag until you select a newer one.

The ChatGPT desktop app also discovers a repo marketplace when the repository is open. Restart the app after changing the marketplace or plugin.

### Claude Code

This repository exposes a Claude marketplace at `.claude-plugin/marketplace.json`. It points to the same dual-host package at `plugins/agent-collab`, which contains both plugin manifests, the shared skill, and Claude Code helper agents.

```bash
claude plugin marketplace add ./ --scope user
claude plugin install agent-collab@agent-collab --scope user
```

From GitHub, replace the marketplace-add line with:

```bash
claude plugin marketplace add Rubindai/agent-collab --scope user
```

To pin an exact release, add the marketplace as `Rubindai/agent-collab#v4.0.0` (or another published version tag) with `--scope user`. A pinned marketplace stays on that tag until you select a newer one.

No direct skill installer is provided. Marketplace installation is the supported path for both hosts.

### Update

Every release changes the Semantic Versioning value in both plugin manifests. Refresh a Git-backed marketplace, then restart the host after the update:

```bash
codex plugin marketplace upgrade agent-collab
codex plugin add agent-collab@agent-collab

claude plugin marketplace update agent-collab
claude plugin update agent-collab@agent-collab --scope user
```

For a local checkout, pull the repository first and rerun `codex plugin add agent-collab@agent-collab`, then start a new Codex session. Current Claude Code reads a local marketplace's relative-path plugin directly from that checkout; use `/reload-plugins` or start a new session after edits. Hosted Claude marketplaces use cached copies and the update flow above. Check the installed source and package contents, not only the version or cache timestamp. Direct cache edits are unsupported. [Claude local marketplace edits](https://code.claude.com/docs/en/plugin-marketplaces#test-an-edit-to-a-plugin), [Codex plugin commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-plugin)

Official references:

- [OpenAI: Package plugins](https://developers.openai.com/plugins/build/plugins)
- [OpenAI: Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [OpenAI: Current model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI: GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md)
- [OpenAI: Codex models](https://learn.chatgpt.com/docs/models)
- [OpenAI: `codex debug models`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-debug-models)
- [OpenAI: GPT-6 Astra prompting guidance](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#prompting-best-practices)
- [OpenAI: Rethinking skills and prompts for GPT-6 Astra](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
- [OpenAI: Codex prompting](https://developers.openai.com/codex/prompting)
- [OpenAI: Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Project instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [OpenAI: API prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic: Create plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [Anthropic: Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Anthropic: Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- [Anthropic: Model and effort configuration](https://code.claude.com/docs/en/model-config)
- [Anthropic: Claude Code settings](https://code.claude.com/docs/en/settings)
- [Anthropic: Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
- [Anthropic: Prompt caching](https://code.claude.com/docs/en/prompt-caching)
- [Anthropic: Environment variables](https://code.claude.com/docs/en/env-vars)
- [Anthropic: Project memory and AGENTS.md](https://code.claude.com/docs/en/memory)
- [Anthropic: Claude Code changelog](https://code.claude.com/docs/en/changelog)
- [Anthropic: Model migration guide](https://platform.claude.com/docs/en/about-claude/models/migration-guide)
- [Anthropic: Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)
- [Anthropic: Claude Fable 5 model](https://platform.claude.com/docs/en/models/fable-5/overview)
- [Anthropic: Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
- [Anthropic: Effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic: Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Anthropic: Structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [Anthropic: Permissions](https://code.claude.com/docs/en/permissions)
- [Anthropic: Permission modes](https://code.claude.com/docs/en/permission-modes)
- [Anthropic: Sandboxing](https://code.claude.com/docs/en/sandboxing)

## Invoke

Plain language works in either host:

```text
Use agent collab to audit this change.
```

This explicitly requests the real opposite-product peer workflow, including availability checks, independent host analysis, synthesis, and closure. An acknowledgement or host-only review does not satisfy it. Quoted examples and questions about the plugin do not launch it by themselves. These discovery instructions guide the host; this repository cannot intercept every host message or guarantee skill selection by an external client.

Codex:

```text
Use $agent-collab to review the current diff.
```

Claude Code:

```text
/agent-collab:agent-collab review the current diff
```

Implicit invocation remains enabled when the task matches the skill description. This repository intentionally configures full-capability mode as the plugin default; it is not evidence of fresh per-run consent and operators should change the setting or use safe mode where that posture is inappropriate.

The runtime selects one of five modes unless the host supplies `--mode`:

- `review`: correctness, security, regressions, tests, and release readiness
- `research`: current source-backed external facts
- `design`: architecture, alternatives, constraints, and tradeoffs
- `plan`: prerequisites, ordering, rollback, and verification
- `debug`: competing root causes and decisive reproduction evidence

## Runtime contract

The host resolves the Agent Collab launch request before launch. `host-request.json` records the exact requested model and effort, the live availability attestation, permission mode, research toggle, finite timeout, bounded helper fanout, requested Claude tool surface, and Codex launch configuration. Provider policy, installed tools, MCP configuration, and other host configuration may still affect what the peer CLI exposes, so the request is an auditable launch record rather than a claim of complete provider isolation. The configuration-reduced Claude probe can differ from the full peer in authentication or backend routing supplied by loaded settings. Availability therefore does not prove configuration equivalence; actual-run model and effort checks remain necessary.

Current peer commands use only current CLI surfaces:

```text
codex --ask-for-approval never exec --strict-config --ephemeral ...
  --sandbox danger-full-access
  --model <resolved-model>
  -c model_reasoning_effort="<resolved-effort>"
  -c web_search="live|disabled"

claude -p
  --model <resolved-model>
  --effort <resolved-effort>
  --permission-mode bypassPermissions
  --tools default
  --no-session-persistence
  --settings '{"availableModels":["<resolved-model>"],"fallbackModel":[],"switchModelsOnFlag":false,"disableAllHooks":false,"hooks":{...}}'
  --json-schema ...
  --output-format json
```

The Claude peer process also sets `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, which keeps provider-launched helpers to one layer. The finite total `Agent`-call hook remains a separate bound.

The normal Claude command deliberately omits `--max-turns`. Anthropic documents no turn limit by default. A user can opt into a positive cap for one run with `--claude-max-turns N`; only then does Agent Collab add `--max-turns N`.

Agent Collab safe mode changes a Codex peer to `read-only` and a Claude peer to `plan`; it uses direct workspace mode unless an explicit incompatible isolated selection is rejected. On Linux, a safe-mode Codex peer preflights the `bwrap` backend used by Codex read-only sandboxing. A safe-mode Claude peer uses provider-native `plan` permissions, not an OS container. Full-capability isolated mode separately preflights FUSE OverlayFS and `bwrap` for either peer, then gives the provider full tool permissions inside a disposable copy-on-write workspace. A failed preflight returns structured `sandbox_unavailable` evidence before run creation. Online research can be changed independently with `--online-research` or `--no-online-research`.

The reported pre-1.0 message `Codex sandbox is still broken (bwrap user-namespace failure) — implementing directly as planned.` has unverified historical provenance. The Codex safe-mode preflight passed on the evaluation host on 2026-10-18, but that did not establish a complete provider run. The new disposable-workspace preflight is a separate write/discard test and does not replace exact-pair availability attestation.

The online-research toggle controls the provider's built-in web research tools used by Agent Collab. For Claude Code these are the CLI's built-in `WebSearch` and `WebFetch` tools. This toggle is not a general network-isolation guarantee: full-capability shells, MCP tools, plugins, and provider configuration may have separate network behavior.

Persisted settings, state, requests, helper reports, and synthesis artifacts use strict schema 2.0 contracts. Old, incomplete, noncanonical, or wrongly typed files are rejected rather than migrated or coerced. Claude success is accepted only from `structured_output`; Codex success is accepted only from its required `--output-last-message` artifact. The provider-facing `peer-report-provider.schema.json` intentionally uses the portable structured-output subset accepted by both CLIs. The runtime then validates the result against the canonical `peer-report.schema.json` and semantic request relations, including exact run, origin, host, peer, mode, and target binding. `peer-normalization.schema.json` separately constrains raw-provider extraction and canonical-report validation evidence; host reconstruction preserves extraction warnings.

Run directories are created with owner-only permissions (`0700`) and runtime JSON, raw output, stderr, snapshots, settings, state, locks, and counters are written as owner-only files (`0600`) on POSIX systems, independent of the caller's umask. Writes reject symlink destinations where the platform exposes `O_NOFOLLOW`.

## Workflow

```text
neutral brief
  -> launch peer
  -> independent host analysis
  -> host-first-pass.json
  -> finish and normalize peer
  -> optional advisory adjudication
  -> host verification and synthesis
  -> complete and release worktree guard
```

`finish` is the normal wait point. The peer has a hard finite timeout, 2700 seconds by default, and cannot run indefinitely. Empty output is not treated as a stall before that deadline. At the deadline the runtime terminates the peer and records a structured timeout result. `finish` leaves a successful run in `ready_for_synthesis`; `complete` refuses to close it until a strict v2 `host-synthesis.json` says the final answer is ready and binds both the workspace-mutation digest and the claim-matrix digest, including canonical hashes of all admitted source reports. Changed mutation evidence or reports require synthesis again before the guard can be released. New required digest fields are a breaking artifact-contract change: finish and close active runs before upgrading; older incomplete artifacts are rejected without migration.

The independent-host and adjudicator stages are procedural workflow requirements, not claims that the runtime can prove what a host or helper previously read. `host-first-pass.json` provides ordering evidence; the host must still follow the skill. Likewise, `advisory_pending` means no adjudicator report was supplied—it does not mean an adjudicator ran. At completion, the runtime rebuilds the claim matrix from the current host, peer, helper, and adjudicator artifacts and refuses stale or manually detached synthesis evidence.

### Turn limit versus time limit

The 50-turn failure was caused by Agent Collab 1.0.0 always adding `--max-turns 50`. It was not an Anthropic default, a model disagreement, or the absolute peer deadline. Anthropic's CLI reference says `--max-turns` is an optional print-mode limit, has no limit by default, and exits with an error when reached. The commit that introduced both the timeout and the 50-turn default records no rationale for choosing 50; treating it as an extra runaway-work or cost guardrail is an inference from the controls added alongside it, not documented project intent. This failure class is not unique to Agent Collab: a public Claude Code issue reproduces `Reached max turns` when a caller explicitly supplies the flag and also notes that omission is unlimited. That report does not establish a provider default of 50; the value 50 came from Agent Collab. Tool-heavy research can consume many short agentic turns while still making useful progress inside the wall-clock deadline, so a second mandatory stop condition reduced report reliability without adding a necessary liveness guarantee. [Introduction commit](https://github.com/Rubindai/agent-collab/commit/b156c527f32bbffbe66a6906535b9d473536a9b6), [public Claude Code report](https://github.com/anthropics/claude-code/issues/16963)

Agent Collab 2.0.0 therefore uses one mandatory liveness boundary: the finite `agent_timeout_seconds` deadline, 2700 seconds by default. The deadline limits total elapsed time even if a tool or provider stalls. Claude turn count is now a separate, explicit budget policy: omit it for quality-first review and research, or pass `--claude-max-turns N` when the operator intentionally wants a work-count cap. A reached explicit or provider-imposed cap is recorded as `turn_limit_reached`, distinct from `timeout` and generic API errors.

Peer prompts use one compact, escaped XML brief with role, outcome, evidence, boundaries, task, and stop sections. Reviewed against [GPT-6 Astra prompting guidance](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#prompting-best-practices) and [Opus 5.5 prompting guidance](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5) on 2026-10-18, they ask for concise evidence, completion of authorized work, stated assumptions for nonblocking ambiguity, and explicit blockers with uncovered scope. Started commands and helpers must finish within the existing deadline. Further checks need new evidence, changes, or an unresolved question. Review mode retains every in-scope candidate with severity and confidence for host filtering. Helpers remain limited to sizeable independent workstreams. Quoted sources and tool output provide evidence, not authorization. Host conclusions, request JSON, and response schemas are not duplicated into the brief. These are prompt-contract changes, not a measured quality improvement; behavioral evaluation on representative tasks is still needed.

The prompt is an instruction to the peer, not a sandbox or a higher-precedence security boundary. Provider-managed instructions and repository guidance may still load through the peer CLI. Runtime boundaries include launch validation, the workflow guard, provider permissions, and the disposable filesystem when selected.

## Permissions and recursion

Full capability is the configured default. On Linux, the default disposable workspace lets the peer write temporarily to an overlay of the repository and HOME; those writes are discarded after the run. The rest of the host filesystem is mounted read-only, except the Agent Collab run directory and temporary provider artifacts. On macOS, fresh settings default to direct host access with before/after mutation snapshots. These snapshots detect included changes after the fact; they do not prevent or undo edits. Neither posture is a complete host-damage boundary: networking, MCP tools, plugins, sockets, and external services can still have effects. Anthropic recommends `bypassPermissions` only inside isolated containers, virtual machines, or development containers without internet access where host damage is impossible. Select safe mode for provider-native restrictions; on Linux, use `--workspace-mode direct` when direct host access is intended:

- Codex peers use `danger-full-access` with approval set to `never`.
- Claude peers use `bypassPermissions`; `--tools default` exposes every current built-in tool. `bypassPermissions` skips the permission layer and does not protect against prompt injection or unintended actions. Provider and organization policy can still refuse this mode.
- Native helpers retain the full tool and permission context of the host in full mode. Agent Collab adds no helper `tools` or `disallowedTools` frontmatter restriction; the peer launch separately applies research, zero-helper, and conversation-fork controls, while provider, organization, session, and tool-specific policies still apply. Configured MCP tools are not restricted by Claude's `--tools` built-in-tool selector.

For a non-Git workspace, the Codex peer launcher passes `--skip-git-repo-check` after a lightweight Git probe in isolated mode or the filesystem snapshot in direct mode. Codex keeps its repository check for Git workspaces. [OpenAI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode#git-repository-required)

Capability is not permission to make durable edits. `edit_allowed` defaults to `false`; `--edit-allowed` requires explicit delegation and must be paired with `--workspace-mode direct`, because isolated writes are discarded. Isolated mode writes a small boundary receipt instead of hashing repository contents. Its host mutation result describes the overlay boundary and does not detect changes made outside it. The run directory is deliberately writable by the peer for reports and hooks. The peer can also affect network services, devices, and sockets that remain accessible.

Direct mode retains before/after mutation snapshots as detective evidence. The baseline JSON must fit the 16 MiB reader limit, so large tracked-status and file manifests retain complete digests and counts with bounded path samples; Git index and patch output is streamed into digests. The persisted snapshot stays bounded as the file count grows, but scanning included contents still scales with file count and bytes. A very large ignored tree can exhaust the finite 300-second snapshot deadline. Direct mode covers tracked, untracked, most ignored, and selected Git-control surfaces, with exclusions for high-volume generated/dependency directories. Transient changes reverted between snapshots and changes outside the repository are not observed. Interrupted cancellation reuses its committed snapshot diagnostic.

Only one Agent Collab workflow can be active per Git worktree. `start` atomically acquires a guard under the worktree Git metadata before creating artifacts; every runtime copy for that worktree sees the same guard. A stable per-worktree OS file lock serializes every guard check, stale-start recovery, acquisition, update, and release with a bounded lock wait, so a stale recoverer cannot delete a replacement run. Unavailable process-identity evidence preserves the guard; only a proven dead or replaced starting launcher permits automatic recovery. A separate bounded per-run operation lock serializes terminal `finish`, `complete`, and `cancel` transitions without holding a lock while `finish` waits for a live provider. A startup gate prevents the peer wrapper from running until that guard and its process artifact are committed, and a second gate prevents provider exec until a strict provider-process tracker is committed. The guard remains active across the peer, host-local helpers, adjudication, and synthesis. `complete` or `cancel` releases it only after tracked wrapper/provider groups are terminal and quiescent. This is process-group supervision, not descendant containment: detached groups, remote work, and external sessions are outside that proof. This guard is the hard cross-product recursion boundary. The runtime blocks Claude peer `Agent` calls above the resolved total-call bound and sets Claude's documented subagent-spawn depth to one layer; a zero bound directly denies `Agent`. It configures Codex's documented concurrent spawned-thread cap, while Codex total helper calls and nesting depth remain explicit instructions rather than provider-enforced ceilings. The absolute peer deadline bounds the tracked provider execution. Helper-report admission is capped at the resolved bound, eight by default. Workflow agents were observed bypassing the Agent hook in an isolated live zero-helper test, so Workflow is disabled in command-line settings and denied as a tool. The brief also restricts delegation to ordinary native helpers. Other facilities such as shells, MCP, remote sessions (including RemoteTrigger), scheduled work (including CronCreate), and external processes remain outside a universal spawn cap. Host-native helper launch discipline is also stated in the skill, but the shared runtime cannot intercept every host tool call.

Independent helpers start in fresh contexts with a neutral bounded task, required inputs, edit authorization, remaining deadline, and expected evidence. Do not copy parent conversation history or use custom agents or explicit spawn arguments that override the spawning agent's model or effort. Reuse a helper for follow-up work. The Codex prompt explicitly limits total spawns to the resolved helper count even after threads close; only concurrency is provider-enforced. Claude inbound cross-session messages are refused to preserve review independence. Its own helper `SendMessage` resumes remain available and do not consume an `Agent` call, so the call counter is not a cap on all resumed work; the absolute peer deadline still applies. For explicitly authorized parallel edits, assign disjoint writable paths and one integration owner, serialize overlaps, and preserve other agents' changes. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Anthropic subagents](https://code.claude.com/docs/en/sub-agents)

Cancellation is append-only. It never replaces `peer-report.json`, `peer-normalization.json`, `host-result.json`, `host-synthesis.json`, or `claim-matrix.json`. After proving process-tree quiescence it writes immutable `cancel-result.json`, hashes the designated pre-existing report, raw-output, normalization, result, synthesis, and claim-matrix artifacts, and records mutation comparison separately in `cancel-workspace-mutation.json`. Other telemetry files are not covered by those hashes. If a valid terminal peer report already exists, `cancel` keeps the guard and directs the host to `finish` so completed evidence is not reclassified as cancellation.

For Claude runs, the runtime removes conflicting effort environment overrides, sets `CLAUDE_CODE_EFFORT_LEVEL` to the requested provider level, and supplies command-line settings that restrict `availableModels` to the exact request, empty `fallbackModel`, set `switchModelsOnFlag=false`, and explicitly keep hooks enabled. Command-line settings outrank user, project, and local settings; managed policy remains authoritative. Current Claude Code replaces the ordered `fallbackModel` chain by precedence, while non-managed `availableModels` arrays can merge and model entries can prefix-match extended identifiers. `availableModels` is not an exact model lock. Refusal fallback is separate: `switchModelsOnFlag=false` ends a flagged non-interactive main turn with a refusal, but subagents have documented exceptions. Exact-model live attestation and post-run `modelUsage` checks therefore remain necessary; these are not a universal prevention guarantee for provider substitutions. The probe requires its Stop hook to run, so policies that suppress it fail launch closed. Agent Collab never selects or retries a fallback and rejects observable primary-model mismatches. [Settings precedence](https://code.claude.com/docs/en/settings#lists-merge-instead-of-overriding), [Model fallback](https://code.claude.com/docs/en/model-config#ask-before-switching)

## Settings audit

The original settings audit was completed on 2026-07-26; all settings and launch-control claims were reassessed on 2026-10-18 with Codex CLI `0.163.0` and Claude Code `2.1.302`. Provider documentation defines what each control does; defaults remain Agent Collab product decisions. The helper count, deadline, and high-effort defaults have not been established as cost or latency optima.

### Persisted Agent Collab settings

| Setting | Default | Provider mapping and justification |
| --- | --- | --- |
| `local_subagents_allowed` | `true` | Enables bounded independent lenses when they materially improve coverage. Claude uses an `Agent` pre-tool hook; Codex uses documented multi-agent controls. Turning it off directly denies Claude `Agent` and sets Codex `agents.enabled=false`. |
| `max_local_subagents` | `8` | Finite per-run helper bound. For Claude this is a runtime-enforced total `Agent`-call count plus a documented one-layer spawn-depth limit. For Codex it maps to `agents.max_concurrent_threads_per_session=8`; total calls and depth remain instruction-level bounds because current Codex documentation exposes concurrency, not a hard total-call or depth control. Eight gives broad audit coverage without unbounded fanout. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| `adjudicator_enabled` | `true` | Internal Agent Collab control, not a provider setting. It requests a separate host-local advisory challenge after the host-first and peer reports. This independence is a host procedure; the runtime validates the artifact and rebinding but cannot prove the adjudicator's process history. It replaces the removed `profile`, whose only runtime effect was adjudication. |
| `agent_timeout_seconds` | `2700` | Internal absolute wall-clock deadline, persisted as a JSON number and constrained to 60–86400 seconds. Forty-five minutes is the project-selected budget, not a guarantee that every review will finish. This is the mandatory run bound; it is not a model turn count. |
| `safe_mode` | `false` | Deliberate full-capability product default for deep repository investigation. Codex maps to `danger-full-access`; Claude maps to `bypassPermissions`. This is not the generally safest provider posture: OpenAI says to use caution with full access, and Anthropic says bypass mode belongs only in isolated containers, VMs, or dev containers without internet access. Operators should enable safe mode when that risk is unacceptable. [OpenAI approvals and sandboxing](https://learn.chatgpt.com/docs/agent-approvals-security), [Anthropic permissions](https://code.claude.com/docs/en/permissions) |
| `workspace_mode` | Linux: `isolated`; macOS: `direct` | Linux uses copy-on-write HOME and repository overlays with full provider tools inside, after a successful `fuse-overlayfs` and `bwrap` write/discard preflight. macOS uses direct host filesystem access and bounded persisted mutation snapshots by default; snapshots do not prevent edits and still read included file contents. Explicit settings and per-run choices are preserved. Safe mode resolves to `direct` unless a conflicting isolated selection is explicitly requested. |
| `codex_model` | `gpt-6-astra` | Explicit GPT-6 Astra default for independent peer work. Availability still requires the exact ID in the refreshed Codex catalog. [OpenAI GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md) |
| `codex_effort` | `xhigh` | Project-selected default. Preserve per-run overrides and validate each pair against the live model entry; this is not a universal provider recommendation. |
| `online_research` | `true` | Maps only the provider research tools Agent Collab configures: Codex `web_search=live`, and Claude Code built-in `WebSearch`/`WebFetch` availability. Current-source verification is central to the research use case. This setting does not restrict network access through shells, MCP, plugins, or other provider facilities. [OpenAI web-search configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage) |
| `codex_config` | `[]` | Optional additive Codex `-c key=value` entries, a documented CLI surface. Empty by default to minimize hidden policy. Agent Collab rejects overrides of its owned model, provider routing, profiles, effort, sandbox, approval, web-search, and helper keys so the immutable request cannot disagree with the actual launch. [OpenAI Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-exec) |
| `claude_model` | `claude-opus-5-5` | Fixed Opus 5.5 ID; the live probe must observe this exact primary model. Fable remains explicit-only. [Anthropic Opus 5.5 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5) |
| `claude_effort` | `xhigh` | Project-selected default, verified through Stop-hook telemetry. Explicit `max` remains supported when attested. `ultracode` provider support can be probed, but bounded peer execution rejects that orchestration mode. [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort) |
| `claude_tools` | `default` | Keeps Claude's normal built-in tool surface in full-capability mode so the peer can inspect, test, and research. `default` is an exclusive sentinel rather than a list member. Online-research, zero-helper, and conversation-fork controls are applied separately with `--disallowedTools`. MCP tools and provider-managed policy are outside this selector. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage) |
| `history_retained_runs` | `50` | Internal default for explicit `clear-history` or setup cleanup, not a provider setting and not automatic deletion. The state index independently keeps bounded recent metadata; run directories remain until the operator invokes cleanup. |

### Per-run-only controls

| Control | Default | Reason |
| --- | --- | --- |
| `edit_allowed` / `--edit-allowed` | `false` | Separates launch capability from user authorization. It becomes true only after explicit delegation and remains advisory in full mode; mutation snapshots provide detective evidence. |
| `--peer-model`, `--peer-effort` | resolved defaults | Preserve explicit user choices exactly after documented alias resolution, then require a fresh exact-pair attestation. No fallback is selected or retried. |
| `--online-research`, `--adjudicator`, `--local-subagents`, `--max-local-subagents` | persisted value | Allow one run to change research, advisory adjudication, or helper use without silently changing future runs. Each resolved value is persisted in the immutable request. |
| `--safe-mode` / `--no-safe-mode` | persisted value | Lets a user explicitly change the permission posture for one run without rewriting settings. It never retries a failed posture with the other mode. |
| `--workspace-mode isolated\|direct` | persisted value | Selects a disposable workspace or direct host access for one run. Isolation failure never falls back to direct access. |
| `--timeout-seconds` | persisted value | Lets one unusually small or large task choose a finite 60–86400 second deadline while preserving the no-indefinite-run invariant. |
| `--claude-max-turns N` | omitted / `null` | Anthropic documents no turn limit by default. The flag is retained only as an explicit per-run work-count budget; it is never persisted as a default or inferred. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage) |

### Provider launch audit

| Provider control | Resolved value | Why Agent Collab uses it |
| --- | --- | --- |
| Codex `exec` | non-interactive | Official automation surface for a one-shot peer run. |
| Codex `--strict-config` | enabled | Fails on unrecognized current configuration rather than silently ignoring an enforcement key. |
| Codex `--ephemeral` | enabled | Prevents Codex session rollout persistence; Agent Collab owns the auditable run artifacts. |
| Codex `--json` | enabled | Produces bounded JSONL lifecycle telemetry retained in `peer-stdout.txt`, including usage when emitted. The required final report is read only from `--output-last-message`; telemetry is never a report fallback. |
| Codex `--cd` | exact repository root | Makes repository scope explicit and avoids launch-directory ambiguity. |
| Codex `--output-schema` and `--output-last-message` | provider-compatible peer schema and run artifact | Official structured-output path for stable downstream validation; stdout events are never substituted for the final report. The runtime applies the richer canonical schema and request relations afterward. [OpenAI non-interactive mode](https://developers.openai.com/codex/noninteractive) |
| Codex `--ask-for-approval never` | always | A background peer cannot answer interactive approval prompts. Safe mode combines this with read-only sandboxing; full mode is intentionally autonomous and carries the documented risk. |
| Codex `--sandbox` | `danger-full-access` or `read-only` | Full mode supports deep investigation; safe mode is a no-write posture and on Linux first requires a working `bwrap` preflight. |
| Codex `--model`, `model_reasoning_effort` | exact attested pair | Prevents implicit model/effort selection and makes the request auditable. |
| Codex `web_search` | `live` or `disabled` | Makes current research intent explicit instead of relying on the full-access default. |
| Codex `agents.enabled`, `agents.max_concurrent_threads_per_session` | derived helper policy | Uses the current documented multi-agent keys. Agent Collab does not claim a provider-enforced total-call, nesting-depth, or per-helper runtime limit. |
| Codex `agents.default_subagent_model`, `agents.default_subagent_reasoning_effort` | exact requested peer pair | Overrides ambient helper defaults. Custom agent definitions and explicit spawn arguments can still override model or effort, so the brief forbids conflicting agents; this is not a universal helper-selection guarantee. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) |
| Claude `-p` | non-interactive print mode | Required for structured one-shot automation and the CLI's budget/turn controls. |
| Claude `--model`, `--effort` | exact attested pair | Overrides implicit selection for the session. Agent Collab pins the effective effort in process and command-line settings environments and checks observed model usage afterward. Managed policy remains authoritative. The actual peer must produce valid Stop-hook effort evidence; missing, invalid, or conflicting observations fail with `effort_unverified`. |
| Claude `--permission-mode` | `bypassPermissions` or `plan` | Full mode preserves the configured powerful tool posture; safe mode uses provider-native plan permissions. `plan` is not an OS container and can permit classifier-approved commands. |
| Claude `--no-session-persistence` | enabled | Prevents the peer transcript from becoming a resumable Claude session; Agent Collab keeps its own bounded evidence. |
| Claude inline `--settings` | exact model controls, resolved `env` pins, inbound refusal requested subject to managed policy, finite fail-closed `Agent` hook | Narrows model selection, disables configured fallback and flagged-message model switching, and enforces the total Agent-call bound with command-line-priority settings. Hook execution failures and timeouts block the call (`onFailure: block`, requiring 2.1.295+). Resolved helper pins are also supplied in settings `env` because ordinary settings can overwrite inherited process variables. Managed policy remains authoritative; because non-managed arrays can merge, live and post-run exact-model checks are still required. [Anthropic settings](https://code.claude.com/docs/en/settings), [hook failure policy](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails), [environment precedence](https://code.claude.com/docs/en/env-vars#precedence) |
| Claude `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | exact peer model, `1` | Forces the helper model instead of relying on inheritance or the environment default alone. Force support requires Claude Code 2.1.257+. [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| Claude `CLAUDE_CODE_EFFORT_LEVEL` | exact effective effort | Overrides helper frontmatter and invocation effort. Provider caps can still apply and must pass availability attestation plus actual-run Stop verification. Explicit `ultracode` execution is blocked by the finite-helper policy. [Anthropic model configuration](https://code.claude.com/docs/en/model-config) |
| Claude `CLAUDE_CODE_FORK_SUBAGENT`, `--disallowedTools Agent(fork)` | `0`, denied | Prevents copying the parent conversation into independent helper contexts, including when ambient settings enable forks. [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| Claude `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | `1` | Uses the documented depth control to prevent helpers from recursively spawning another helper layer. The total `Agent`-call hook and absolute peer deadline remain independent bounds. [Anthropic changelog](https://code.claude.com/docs/en/changelog) |
| Claude Stop hook | bounded `claude-effort.json` evidence | Records observed main-peer effort; missing evidence or any mismatch rejects the result, even if later observations match. Private pending markers make capture failures and interruptions after bookkeeping begins visible to the wrapper. Failures before the capture executable starts remain outside that observation boundary. `onFailure` does not apply to Stop hooks. Receipts, markers, and locks use `0600` modes. [Hook effort fields](https://code.claude.com/docs/en/hooks#common-input-fields) |
| Claude `disableWorkflows`, `--disallowedTools Workflow` | disabled, denied | Prevents Workflow agents from bypassing the Agent-call counter. Explicit `ultracode` fails before launch; other full-mode capabilities retain their configured policy. |
| Claude `--json-schema`, `--output-format json` | provider-compatible peer schema | Uses Claude's official structured-output envelope; the richer canonical schema and request relations are checked after extraction, and bare or cross-provider shapes are rejected. [Anthropic structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs) |
| Claude `--tools`, `--disallowedTools` | resolved built-ins | Starts from the configured built-in surface, then removes web tools when research is off and directly denies `Agent` when helpers are disabled. It does not claim control of MCP or plugin tools. |
| Claude `--max-turns` | omitted unless explicitly requested | Avoids truncating valid tool-heavy work. If opted in, a reached cap is a distinct structured failure. |

Availability has a narrower contract than a peer run. Codex refreshes `codex debug models` and accepts only the exact advertised pair. The Claude probe runs outside the repository with filesystem setting sources, built-in tools, and MCP discovery disabled, uses one turn because the only task is to return `AVAILABLE`, and requires both exact primary-model `modelUsage` and Stop-hook effort telemetry. That one-turn probe cap does not apply to the real peer run.

The runtime neutralizes inherited thinking overrides in the process environment and supplies command-line environment pins. Boolean `0` values are documented; the empty `MAX_THINKING_TOKENS` pin is not independently specified by current provider documentation. Exact effort is checked through Stop telemetry, but this does not prove every internal thinking-budget behavior. [Claude environment variables](https://code.claude.com/docs/en/env-vars)

### Deliberately not enabled

- No default Claude turn or dollar budget is layered over the finite deadline. Either would be a second truncation policy; a turn cap remains explicit-only, and a future monetary cap would also need an explicit product contract.
- Normal peers do not use Claude `--bare`, `--setting-sources`, `--strict-mcp-config`, or troubleshooting `--safe-mode`, nor Codex `--ignore-user-config` or `--ignore-rules`. The full-capability product posture intentionally retains relevant repository guidance, configured tools, plugins, and provider policy. Only the narrow availability probe reduces configuration sources.
- Agent Collab does not append or replace provider system prompts. The compact user brief carries the task contract without claiming higher precedence than repository or managed instructions.
- Fallback chains remain empty and exact-model availability is fail-closed. A fallback would make the persisted request disagree with the model that actually ran.
- Both peer defaults use `xhigh` as a project choice. Persisted settings and explicit per-run overrides retain their precedence; upgrading the package does not rewrite existing settings. Effort changes are never silently substituted.

## Configuration

### Cache, performance, and cost

Independent reviews keep fresh contexts. A normal Claude helper does not read its parent's conversation cache on its first request; a fork does because it copies that conversation. Resuming a helper can reuse its own warm cache. Main `-p` turns default to one-hour caching within subscription included usage, or five minutes with API/usage billing; subagents normally get five minutes. Longer TTLs cost more to write, so Agent Collab leaves them provider-managed. Opus 5.5 effort changes preserve cache on supported direct API/subscription paths, with provider and policy exceptions. Plugin skills and agents are appended without invalidating the existing prefix. [Claude caching](https://code.claude.com/docs/en/prompt-caching)

Shared outcome and evidence text precedes run identity and task text to improve the opportunity for matching prefixes. Actual hits depend on provider prefixes, minimum lengths, routing, and expiry. OpenAI API caching controls are not automatically Codex CLI controls, and no undocumented cache option is injected. [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)

Use the fewest independent helpers needed and reuse them for follow-ups. Exact requested models and efforts remain fixed. Compare representative tasks before claiming savings: include completion quality, elapsed time, input/output and cached tokens, helpers, adjudication, and the fresh availability probe. `peer-stdout.txt` retains bounded Codex telemetry and Claude's `peer.raw.json` retains its result envelope; neither is a complete billing ledger. Probe charges and host/adjudicator usage are not aggregated. Reported provider cost estimates are not invoices. [Claude cost tracking](https://code.claude.com/docs/en/costs)

### Repository instructions

`AGENTS.md` remains the shared contract and `CLAUDE.md` contains only `@AGENTS.md`. Current Claude Code also supports direct AGENTS discovery, but the import is still supported, avoids duplicate loading, and covers the supported CLI versions before direct discovery arrived. The repository guidance fits Codex's default 32 KiB combined project-instruction budget; parent or global instructions can consume the rest. Imports do not reduce token cost. [Claude memory](https://code.claude.com/docs/en/memory#remove-an-earlier-agentsmd-workaround), [Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

### Setup and overrides

Interactive setup:

```bash
python tools/agent-collab/scripts/host.py setup
```

One-run overrides:

```bash
python tools/agent-collab/scripts/host.py start \
  --host codex \
  --target "current diff" \
  --brief "Review release readiness" \
  --peer-model claude-fable-5 \
  --peer-effort max \
  --online-research \
  --safe-mode \
  --timeout-seconds 3600 \
  --local-subagents \
  --max-local-subagents 4
```

Environment keys:

- `CODEX_AGENT_COLLAB_MODEL`
- `CODEX_AGENT_COLLAB_EFFORT`
- `CODEX_AGENT_COLLAB_CONFIG`
- `CLAUDE_AGENT_COLLAB_MODEL`
- `CLAUDE_AGENT_COLLAB_EFFORT`
- `CLAUDE_AGENT_COLLAB_TOOLS`
- `AGENT_COLLAB_ONLINE_RESEARCH`
- `AGENT_COLLAB_SAFE_MODE`
- `AGENT_COLLAB_WORKSPACE_MODE`
- `AGENT_COLLAB_TIMEOUT_SECONDS`
- `AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED`
- `AGENT_COLLAB_MAX_LOCAL_SUBAGENTS`
- `AGENT_COLLAB_ADJUDICATOR_ENABLED`
- `AGENT_COLLAB_HISTORY_RETAINED_RUNS`

Run `doctor` to check versions, authentication, schemas, paths, resolved defaults, and the selected workspace backend:

```bash
python tools/agent-collab/scripts/host.py doctor
```

`doctor` strictly parses every bundled schema and meta-validates it when Python `jsonschema` is installed; its `meta_validator` field distinguishes those checks. Release verification requires full schema meta-validation. Because 2.0 has no migration layer, an invalid legacy settings or state file must be archived or removed before retrying. Rerun `setup` to create canonical settings; for legacy run state, archive the old run root and start with a fresh default root or explicit `--run-root`. Do not edit installed caches or ask the runtime to coerce old artifacts.

After the host has synthesized a run:

```bash
python tools/agent-collab/scripts/host.py complete RUN_ID
```

## Repository layout

```text
.agents/plugins/marketplace.json                  Codex repo marketplace
.claude-plugin/marketplace.json                   Claude Code marketplace
plugins/agent-collab/                             Unified generated Codex/Claude package
tools/agent-collab/                               Canonical skill, runtime, schemas, references, and host-agent assets
tools/agent-collab/agents/                        Canonical Codex metadata and Claude helper agents
scripts/sync-packages.sh                          Package synchronization
tests/test_agent_collab_runtime.py                Runtime and package tests
tests/test_agent_collab_safety.py                 Process, recursion, snapshot, and strict-JSON safety tests
AGENTS.md                                         Repository maintenance contract
VERSION                                            Release version
CHANGELOG.md                                      Release history
```

Edit shared skill and runtime resources only under `tools/agent-collab/`, then synchronize the dual-host package:

```bash
scripts/sync-packages.sh
scripts/sync-packages.sh --check
```

## Verify

Run the checks below independently. Current Plugin Creator guidance supports this existing `.codex-plugin/plugin.json` format; it recommends root `plugin.json` for new portable packages, which does not require migrating this dual-host package. [OpenAI package formats](https://developers.openai.com/plugins/build/plugins)

Codex verification covers strict package JSON, schema and version consistency, official skill validation, and isolated marketplace installation. Test representative requests in a fresh conversation after installation. Public-directory publication additionally requires the submission portal's automated checks. [OpenAI local plugin testing](https://developers.openai.com/plugins/deploy/connect-chatgpt), [OpenAI submission checks](https://developers.openai.com/plugins/deploy/submission)

```bash
python -m unittest discover -s tests -v
python -m py_compile tools/agent-collab/scripts/*.py
scripts/sync-packages.sh --check
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" tools/agent-collab
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" plugins/agent-collab/skills/agent-collab
claude plugin validate . --strict
claude plugin validate plugins/agent-collab --strict
```

Before publication, parse every release JSON file strictly and test isolated marketplace installation on both hosts. Claude installation must pass `--scope user` explicitly.

### Tagged releases

GitHub `main` is an installable marketplace source. Any push that changes `plugins/agent-collab/` must carry a new Semantic Versioning release; do not publish different package contents under the same manifest version. Use a major bump for incompatible settings or runtime contracts, a minor bump for backward-compatible functionality, and a patch bump for compatible fixes. The 3.0.0 source was on `main` without a tag or GitHub Release. Formal annotated tags start at `v4.0.0`; do not backfill `v3.0.0` without release evidence.

For each release, update `VERSION`, both plugin manifests, and the root `CHANGELOG.md`, then synchronize the package and run the [Verify](#verify) checks. Commit the verified release state on `main` before creating a new annotated `v<version>` tag on that exact commit. Confirm that the remote branch has not changed, push the branch and tag, and verify both remote refs point to the intended commit. Publish a GitHub Release from the existing tag, with notes drawn from the matching root changelog entry. Never move or reuse a release tag. A version tag identifies an exact package; marketplace sources that track `main` still follow the branch, while sources pinned to a tag remain fixed until users choose a newer tag. [Semantic Versioning](https://semver.org/spec/v2.0.0.html), [Codex Git refs](https://developers.openai.com/plugins/build/plugins), [Claude marketplace versions and refs](https://code.claude.com/docs/en/plugins/host-marketplace), [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

Use the same version and commit for the tag, manifests, changelog entry, and GitHub Release. `gh release create` must use `--verify-tag` so it cannot silently create a tag at another commit. Check the release URL and tag target after publication. The root changelog is the maintained history; GitHub Release notes are a copy of that version's entry, not a separate source of truth. [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)

### Evaluation evidence

The 2026-10-18 evaluation checked every README section against current provider documentation, the canonical runtime, schemas, package tests, and installed CLI help. Two independent Agent Collab Claude Opus 5.5/xhigh reviews, independent host runtime review, and advisory adjudication supplement those checks. The second peer completed with exact-model usage and four matching xhigh Stop observations; its coverage limitations and all candidate dispositions are retained in the local run artifacts. Confirmed findings were fixed and checked after integration. Evaluation results are scoped to this Linux checkout and the versions listed above; macOS, every model/provider combination, and comparative quality/cost benchmarks were not exercised.

After removing the obsolete external-validator test hook, the 2026-10-18 regression run passed all 160 tests with no skips. Package byte parity, Python compilation, strict parsing of all 36 release JSON files, meta-validation of all 32 schema copies, official skill and Claude validators, and isolated installations on both hosts passed. The global Codex installation was refreshed to 3.0.0 and all 39 package files matched; Claude was refreshed at user scope and reads the synchronized local package directly. These are checkout-specific results, not a tagged or published release.

The evaluation also reproduced and fixed interrupted probe cleanup, Git index self-mutation during snapshots, strict-type and symlink-admission gaps, expiry between attestation and launch, conflicting selection evidence, noncanonical helper counters, dropped Stop captures, and normalization warning loss. A live before/after Workflow test demonstrated the counter bypass and confirmed that the disabled Workflow tool could no longer launch the test agent. The Linux `bwrap` initialization check passed; that does not establish a complete safe-mode provider run. Fresh availability probes remain mandatory for future runs, since today's account/model access is not a durable guarantee.

The 2026-10-21 checkout check passed 170 tests, package byte parity, Python compilation, strict parsing of 36 release JSON files, meta-validation of 32 schemas, official skill and Claude plugin validators, and isolated marketplace installations for both hosts. Tests simulate the macOS built-in direct-mode selection and verify that it does not invoke the Linux isolation preflight. This was run on Linux; native macOS execution and a complete provider peer run remain untested.

The Codex and Claude manifests and root `VERSION` must always contain the same Semantic Versioning 2.0.0 version. Record user-visible and breaking changes in the root `CHANGELOG.md`; do not put changelogs inside skills. Version 2.0.0 intentionally rejects 1.x settings and run artifacts rather than translating them or probing compatibility fallbacks.
