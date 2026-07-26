# Agent Collab

Agent Collab is a peer-first, challenge-first collaboration plugin for Codex and Claude Code. It launches the opposite product with a neutral brief, keeps the host analysis independent, then validates and combines the evidence.

Version 2.0.0 is a breaking release. It has no compatibility layer for 1.x settings or run artifacts: the removed profile and persisted Claude turn-cap fields are rejected rather than translated.

## Defaults

| Peer | Default model | Effort |
| --- | --- | --- |
| Codex | `gpt-5.6-sol` | `max` |
| Claude Code | `claude-opus-5` | `max` |

These are deliberate quality-first product defaults requested for Agent Collab. Current provider guidance recommends lower levels for most work—OpenAI reserves `max` for the hardest quality-first work, and Anthropic says Opus 5 `max` can overthink and have diminishing returns—so this default accepts higher latency and cost for high-stakes peer review. An explicit user selection always takes priority when the exact pair is available.

The user can select a model and reasoning or effort level in natural language. The host translates an explicit selection into the per-run flags `--peer-model` and `--peer-effort`, then requires a fresh availability attestation for that exact pair before launch.

Claude peer effort may be a provider level (`low`, `medium`, `high`, `xhigh`, or `max`) or the explicit Claude Code orchestration mode `ultracode`. `ultracode` requests provider effort `xhigh` and enables dynamic orchestration, so availability telemetry intentionally records `requested_effort=ultracode` and `observed_effort=xhigh`. Agent Collab passes it only when the installed Claude Code accepts it and the live probe confirms that effective level; the same finite Agent-call guard still applies.

Codex effort support is not a hardcoded Agent Collab list. Immediately before launch, the runtime refreshes `codex debug models` and accepts only an effort advertised for the exact requested model. The current `gpt-5.6-sol` Codex CLI catalog advertises `ultra`, which enables automatic delegation, but the refreshed catalog remains authoritative.

Examples:

```text
Use Agent Collab with Fable 5 Max to review this change.
Use Agent Collab with Opus 5 Ultracode to audit the release.
Use Agent Collab and run the Codex peer on gpt-5.6-sol at high effort.
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

- Codex CLI 0.145.0 or newer
- Claude Code 2.1.219 or newer
- Python 3.10 or newer
- Git for Git-aware workspace snapshots; non-Git directories use filesystem snapshots
- Linux or macOS with POSIX process groups and file locking. Windows peer launch is rejected before run artifacts are created because equivalent supervision is not implemented.

`start` checks the required peer CLI version before creating a run directory. Older CLIs are rejected; no alternate flags or commands are attempted.

## Install

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

No direct skill installer is provided. Marketplace installation is the supported path for both hosts.

### Update

Every release changes the Semantic Versioning value in both plugin manifests. Refresh a Git-backed marketplace, then restart the host after the update:

```bash
codex plugin marketplace upgrade agent-collab

claude plugin marketplace update agent-collab
claude plugin update agent-collab@agent-collab --scope user
```

For a local checkout, pull the repository first, refresh or reinstall the marketplace plugin, and start a new host session. Do this for both Codex and Claude; neither host should be assumed to reread an already installed package in place. Direct cache edits are unsupported.

Official references:

- [OpenAI: Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [OpenAI: Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [OpenAI: Current model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI: GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [OpenAI: Codex models](https://learn.chatgpt.com/docs/models)
- [OpenAI: `codex debug models`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-debug-models)
- [OpenAI: GPT-5.6 model and prompting guidance](https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6)
- [OpenAI: Codex prompting](https://developers.openai.com/codex/prompting)
- [OpenAI: Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Anthropic: Create plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [Anthropic: Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Anthropic: Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- [Anthropic: Model and effort configuration](https://code.claude.com/docs/en/model-config)
- [Anthropic: Claude Code settings](https://code.claude.com/docs/en/settings)
- [Anthropic: Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
- [Anthropic: Claude Code changelog](https://code.claude.com/docs/en/changelog)
- [Anthropic: Migrating to Claude Opus 5](https://platform.claude.com/docs/en/about-claude/models/migration-guide)
- [Anthropic: Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)
- [Anthropic: Claude Fable 5 model](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5)
- [Anthropic: Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
- [Anthropic: Effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic: Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Anthropic: Structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [Anthropic: Permissions](https://code.claude.com/docs/en/permissions)
- [Anthropic: Sandboxing](https://code.claude.com/docs/en/sandboxing)

## Invoke

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

The host resolves the Agent Collab launch request before launch. `host-request.json` records the exact requested model and effort, the live availability attestation, permission mode, research toggle, finite timeout, bounded helper fanout, requested Claude tool surface, and Codex launch configuration. Provider policy, installed tools, MCP configuration, and other host configuration may still affect what the peer CLI exposes, so the request is an auditable launch record rather than a claim of complete provider isolation.

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

Agent Collab safe mode changes a Codex peer to `read-only` and a Claude peer to `plan`; it is unrelated to Claude Code's separate `--safe-mode` troubleshooting flag. On Linux, a safe-mode Codex peer preflights the `bwrap` backend used by the Codex read-only sandbox. If that backend is missing or cannot initialize, `start` emits a structured sandbox-unavailable result and stops before creating a run. A safe-mode Claude peer uses Claude's provider-native `plan` permission mode; `plan` is a permission boundary, not an OS container. Agent Collab itself neither requests nor preflights `bwrap` for Claude or in full-capability mode, although Claude settings loaded from other policy sources can independently enable Claude's sandbox. Online research can be changed independently with `--online-research` or `--no-online-research`.

The reported pre-1.0 message `Codex sandbox is still broken (bwrap user-namespace failure) — implementing directly as planned.` was investigated. Its claimed historical cause is not present in this repository or its Git history, so that provenance is unverified. The current Linux host does reproduce a Codex `bwrap` prerequisite failure while configuring the isolated loopback interface. A peer failure has no authorization meaning: Agent Collab full mode does not request `bwrap`, and a safe-mode Codex preflight emits `sandbox_unavailable` and stops before launch artifacts are created.

The online-research toggle controls the provider's built-in web research tools used by Agent Collab. For Claude Code these are the CLI's built-in `WebSearch` and `WebFetch` tools; Anthropic's separate statement that Opus 5 lacks the API's server-side web-fetch tool does not remove the Claude Code built-ins. This toggle is not a general network-isolation guarantee: full-capability shells, MCP tools, plugins, and provider configuration may have separate network behavior.

Persisted settings, state, requests, helper reports, and synthesis artifacts use strict schema 2.0 contracts. Old, incomplete, noncanonical, or wrongly typed files are rejected rather than migrated or coerced. Claude success is accepted only from `structured_output`; Codex success is accepted only from its required `--output-last-message` artifact. The provider-facing `peer-report-provider.schema.json` intentionally uses the portable structured-output subset accepted by both CLIs. The runtime then validates the result against the canonical `peer-report.schema.json` and semantic request relations, including exact run, origin, host, peer, mode, and target binding. `peer-normalization.schema.json` separately constrains success and failure evidence.

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

`finish` is the normal wait point. The peer has a hard finite timeout, 2700 seconds by default, and cannot run indefinitely. Empty output is not treated as a stall before that deadline. At the deadline the runtime terminates the peer and records a structured timeout result. `finish` leaves a successful run in `ready_for_synthesis`; `complete` refuses to close it until a strict v2 `host-synthesis.json` says the final answer is ready and binds the exact workspace-mutation digest. A mutation discovered after synthesis refreshes the evidence and requires synthesis again before the guard can be released.

The independent-host and adjudicator stages are procedural workflow requirements, not claims that the runtime can prove what a host or helper previously read. `host-first-pass.json` provides ordering evidence; the host must still follow the skill. Likewise, `advisory_pending` means no adjudicator report was supplied—it does not mean an adjudicator ran. At completion, the runtime rebuilds the claim matrix from the current host, peer, helper, and adjudicator artifacts and refuses stale or manually detached synthesis evidence.

### Turn limit versus time limit

The 50-turn failure was caused by Agent Collab 1.0.0 always adding `--max-turns 50`. It was not an Anthropic default, a model disagreement, or the absolute peer deadline. Anthropic's CLI reference says `--max-turns` is an optional print-mode limit, has no limit by default, and exits with an error when reached. The commit that introduced both the timeout and the 50-turn default records no rationale for choosing 50; treating it as an extra runaway-work or cost guardrail is an inference from the controls added alongside it, not documented project intent. This failure class is not unique to Agent Collab: a public Claude Code issue reproduces `Reached max turns` when a caller explicitly supplies the flag and also notes that omission is unlimited. That report does not establish a provider default of 50; the value 50 came from Agent Collab. Tool-heavy research can consume many short agentic turns while still making useful progress inside the wall-clock deadline, so a second mandatory stop condition reduced report reliability without adding a necessary liveness guarantee. [Introduction commit](https://github.com/Rubindai/agent-collab/commit/b156c527f32bbffbe66a6906535b9d473536a9b6), [public Claude Code report](https://github.com/anthropics/claude-code/issues/16963)

Agent Collab 2.0.0 therefore uses one mandatory liveness boundary: the finite `agent_timeout_seconds` deadline, 2700 seconds by default. The deadline limits total elapsed time even if a tool or provider stalls. Claude turn count is now a separate, explicit budget policy: omit it for quality-first review and research, or pass `--claude-max-turns N` when the operator intentionally wants a work-count cap. A reached explicit or provider-imposed cap is recorded as `turn_limit_reached`, distinct from `timeout` and generic API errors.

Peer prompts use one compact, escaped XML brief with role, outcome, evidence, boundaries, task, and stop sections. This follows current GPT-5.6 guidance to state the desired outcome, relevant context, constraints, verification evidence, success criteria, and output contract once, and current Opus 5 guidance to constrain scope and avoid generic self-review loops. The brief starts with the desired result, asks for concise deduplicated evidence without omitting findings, includes only process constraints that matter to independence or safety, and does not reveal host conclusions or suspected findings. Its stop condition is concrete: cover the requested scope, record evidence and uncertainty, then stop without adjacent or repeated checks. In review mode it asks the peer to surface every in-scope issue, including uncertain and low-severity candidates, and to label severity and confidence for host verification instead of suppressing recall with a vague importance filter. Helper delegation is reserved for the fewest sizeable independent workstreams, not quick lookups. Request JSON and response schemas are not duplicated into the prompt; the CLI output controls and runtime validation handle the structured report.

The prompt is an instruction to the peer, not a sandbox or a higher-precedence security boundary. Provider-managed instructions and repository guidance may still load through the peer CLI. Technical guarantees in this document refer only to runtime checks such as launch validation, structured-output validation, the workflow guard, the Codex safe-mode sandbox preflight, and Claude's selected permission mode.

## Permissions and recursion

Full capability is the configured default launch posture for deep investigation and validation. It is powerful: use it only where host-level consequences are acceptable. OpenAI advises caution with `danger-full-access`, and Anthropic recommends `bypassPermissions` only inside isolated containers, virtual machines, or development containers without internet access where host damage is impossible; trusting a repository is not a substitute for that isolation. Select Agent Collab safe mode explicitly when full host access is not appropriate:

- Codex peers use `danger-full-access` with approval set to `never`.
- Claude peers use `bypassPermissions`; `--tools default` exposes every current built-in tool. `bypassPermissions` skips the permission layer and does not protect against prompt injection or unintended actions. Provider and organization policy can still refuse this mode.
- Native helpers retain the full tool and permission context of the host in full mode. Agent Collab adds no helper `tools` or `disallowedTools` restriction, while provider, organization, session, and tool-specific policies still apply. Configured MCP tools are not restricted by Claude's `--tools` built-in-tool selector.

Capability is not permission to edit. `edit_allowed` defaults to `false` independently of the launch permission mode. The host may pass `--edit-allowed` only when the user explicitly delegates repository changes. In full-capability mode the no-edit instruction is advisory and mutation detection is detective, not a preventive filesystem boundary. From the resolved repository root, the host records changes visible between its before/after snapshots—including tracked, untracked, most ignored, and selected Git-control surfaces—even if the peer crashes or is cancelled. Filesystem traversal records symlink identity instead of following symlinked directories. Known high-volume generated or dependency directories are excluded; `.next` is excluded only from generated/untracked/ignored scanning, while tracked `.next` files remain covered by Git index, status, and diff evidence. Transient changes reverted before a snapshot are not observable, so the diagnostic cannot prove attribution or complete immutability.

Only one Agent Collab workflow can be active per Git worktree. `start` atomically acquires a guard under the worktree Git metadata before creating artifacts; every runtime copy for that worktree sees the same guard. A stable per-worktree OS file lock serializes every guard check, stale-start recovery, acquisition, update, and release with a bounded lock wait, so a stale recoverer cannot delete a replacement run. A separate bounded per-run operation lock serializes terminal `finish`, `complete`, and `cancel` transitions without holding a lock while `finish` waits for a live provider. A startup gate prevents the peer wrapper from running until that guard and its process artifact are committed, and a second gate prevents provider exec until a strict provider-process tracker is committed. The guard remains active across the peer, host-local helpers, adjudication, and synthesis. `complete` or `cancel` releases it only after tracked wrapper/provider groups are terminal and quiescent. This guard is the hard cross-product recursion boundary. The runtime blocks Claude peer `Agent` calls above the resolved total-call bound and sets Claude's documented subagent-spawn depth to one layer; a zero bound directly denies `Agent`. It configures Codex's documented concurrent spawned-thread cap, while Codex total helper calls and nesting depth remain explicit instructions rather than provider-enforced ceilings. The absolute peer deadline bounds aggregate runtime. Helper-report admission is capped at the resolved bound, eight by default. Host-native helper launch discipline is also stated in the skill, but the shared runtime cannot intercept every host tool call.

Cancellation is append-only. It never replaces `peer-report.json`, `peer-normalization.json`, `host-result.json`, `host-synthesis.json`, or `claim-matrix.json`. After proving process-tree quiescence it writes immutable `cancel-result.json`, hashes any pre-existing evidence, and records mutation comparison separately in `cancel-workspace-mutation.json`. If a valid terminal peer report already exists, `cancel` keeps the guard and directs the host to `finish` so completed evidence is not reclassified as cancellation.

For Claude runs, the runtime removes session-effort environment overrides and supplies command-line settings that restrict `availableModels` to the exact request, empty `fallbackModel`, disable `switchModelsOnFlag`, and explicitly keep hooks enabled. Claude Code command-line settings outrank user, project, and local settings; managed policy remains authoritative. Non-managed array settings can merge, so the exact-model live attestation and post-run `modelUsage` checks remain the decisive controls rather than relying on `availableModels` alone. The availability probe requires its Stop hook to run, so managed policies that suppress non-managed hooks fail the launch closed. Agent Collab never selects or retries another model or effort, including Anthropic's documented automatic fallback after a refusal.

## Settings audit

This audit was refreshed on 2026-07-26 against the current official OpenAI and Anthropic documentation and the installed `codex 0.145.0` and Claude Code `2.1.220` help. Provider documentation defines what each provider control does; the choice of default remains an Agent Collab product decision for high-stakes, challenge-first repository work.

### Persisted Agent Collab settings

| Setting | Default | Provider mapping and justification |
| --- | --- | --- |
| `local_subagents_allowed` | `true` | Enables bounded independent lenses when they materially improve coverage. Claude uses an `Agent` pre-tool hook; Codex uses documented multi-agent controls. Turning it off directly denies Claude `Agent` and sets Codex `agents.enabled=false`. |
| `max_local_subagents` | `8` | Finite per-run helper bound. For Claude this is a runtime-enforced total `Agent`-call count plus a documented one-layer spawn-depth limit. For Codex it maps to `agents.max_concurrent_threads_per_session=8`; total calls and depth remain instruction-level bounds because current Codex documentation exposes concurrency, not a hard total-call or depth control. Eight gives broad audit coverage without unbounded fanout. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| `adjudicator_enabled` | `true` | Internal Agent Collab control, not a provider setting. It requests a separate host-local advisory challenge after the host-first and peer reports. This independence is a host procedure; the runtime validates the artifact and rebinding but cannot prove the adjudicator's process history. It replaces the removed `profile`, whose only runtime effect was adjudication. |
| `agent_timeout_seconds` | `2700` | Internal absolute wall-clock deadline, persisted as a JSON number and constrained to 60–86400 seconds. Forty-five minutes is long enough for high-effort, tool-heavy reviews while guaranteeing liveness. This is the mandatory run bound; it is not a model turn count. |
| `safe_mode` | `false` | Deliberate full-capability product default for deep repository investigation. Codex maps to `danger-full-access`; Claude maps to `bypassPermissions`. This is not the generally safest provider posture: OpenAI says to use caution with full access, and Anthropic says bypass mode belongs only in isolated containers, VMs, or dev containers without internet access. Operators should enable safe mode when that risk is unacceptable. [OpenAI approvals and sandboxing](https://learn.chatgpt.com/docs/agent-approvals-security), [Anthropic permissions](https://code.claude.com/docs/en/permissions) |
| `codex_model` | `gpt-5.6-sol` | OpenAI identifies Sol as the frontier model for complex professional work and recommends it for complex reasoning and coding. That matches high-stakes review, design, research, and debugging. [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| `codex_effort` | `max` | Quality-first choice for the hardest work. OpenAI recommends `max` only for the hardest, quality-first tasks and suggests comparing it with `xhigh`; Agent Collab accepts the extra latency for high-stakes peer work. The current live Sol catalog is checked before every run and is authoritative for supported levels, including its current delegation-enabled `ultra`. Explicit user choices are preserved. [OpenAI GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6) |
| `online_research` | `true` | Maps only the provider research tools Agent Collab configures: Codex `web_search=live`, and Claude Code built-in `WebSearch`/`WebFetch` availability. Current-source verification is central to the research use case. Opus 5's API-level server-side WebFetch limitation is separate from Claude Code's built-ins. This setting does not restrict network access through shells, MCP, plugins, or other provider facilities. [OpenAI web-search configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage), [Anthropic Opus 5 migration guide](https://platform.claude.com/docs/en/about-claude/models/migration-guide) |
| `codex_config` | `[]` | Optional additive Codex `-c key=value` entries, a documented CLI surface. Empty by default to minimize hidden policy. Agent Collab rejects overrides of its owned model, effort, sandbox, approval, web-search, and helper keys so the immutable request cannot disagree with the actual launch. [OpenAI Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-exec) |
| `claude_model` | `claude-opus-5` | Anthropic's fixed Opus 5 model ID is the current flagship choice for complex, agentic coding and enterprise work, matching the quality-first peer role. The fixed ID avoids an alias changing beneath an immutable request. Fable remains explicit-only because it is a different cost/capability product choice. [Anthropic migration guide](https://platform.claude.com/docs/en/about-claude/models/migration-guide), [Claude Code changelog](https://code.claude.com/docs/en/changelog) |
| `claude_effort` | `max` | Quality-first choice for intelligence-demanding work. Anthropic says Opus 5 supports the full effort range and warns that `max` can overthink and have diminishing returns. Agent Collab intentionally accepts that cost for its default high-stakes use case while preserving explicit lower choices. `ultracode` is an explicit Claude Code dynamic-orchestration mode whose effective provider effort must attest as `xhigh`. [Prompting Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5), [Claude Code changelog](https://code.claude.com/docs/en/changelog) |
| `claude_tools` | `default` | Keeps Claude's normal built-in tool surface in full-capability mode so the peer can inspect, test, and research. `default` is an exclusive sentinel rather than a list member. Online-research and zero-helper choices are applied separately with `--disallowedTools`. MCP tools and provider-managed policy are outside this selector. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage) |
| `history_retained_runs` | `50` | Internal default for explicit `clear-history` or setup cleanup, not a provider setting and not automatic deletion. The state index independently keeps bounded recent metadata; run directories remain until the operator invokes cleanup. |

### Per-run-only controls

| Control | Default | Reason |
| --- | --- | --- |
| `edit_allowed` / `--edit-allowed` | `false` | Separates launch capability from user authorization. It becomes true only after explicit delegation and remains advisory in full mode; mutation snapshots provide detective evidence. |
| `--peer-model`, `--peer-effort` | resolved defaults | Preserve explicit user choices exactly after documented alias resolution, then require a fresh exact-pair attestation. No fallback is selected or retried. |
| `--online-research`, `--adjudicator`, `--local-subagents`, `--max-local-subagents` | persisted value | Allow one run to change research, advisory adjudication, or helper use without silently changing future runs. Each resolved value is persisted in the immutable request. |
| `--safe-mode` / `--no-safe-mode` | persisted value | Lets a user explicitly change the permission posture for one run without rewriting settings. It never retries a failed posture with the other mode. |
| `--timeout-seconds` | persisted value | Lets one unusually small or large task choose a finite 60–86400 second deadline while preserving the no-indefinite-run invariant. |
| `--claude-max-turns N` | omitted / `null` | Anthropic documents no turn limit by default. The flag is retained only as an explicit per-run work-count budget; it is never persisted as a default or inferred. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-usage) |

### Provider launch audit

| Provider control | Resolved value | Why Agent Collab uses it |
| --- | --- | --- |
| Codex `exec` | non-interactive | Official automation surface for a one-shot peer run. |
| Codex `--strict-config` | enabled | Fails on unrecognized current configuration rather than silently ignoring an enforcement key. |
| Codex `--ephemeral` | enabled | Prevents Codex session rollout persistence; Agent Collab owns the auditable run artifacts. |
| Codex `--json` | enabled | Produces bounded JSONL lifecycle telemetry while the required final report is read only from `--output-last-message`. |
| Codex `--cd` | exact repository root | Makes repository scope explicit and avoids launch-directory ambiguity. |
| Codex `--output-schema` and `--output-last-message` | provider-compatible peer schema and run artifact | Official structured-output path for stable downstream validation; stdout events are never substituted for the final report. The runtime applies the richer canonical schema and request relations afterward. [OpenAI non-interactive mode](https://developers.openai.com/codex/noninteractive) |
| Codex `--ask-for-approval never` | always | A background peer cannot answer interactive approval prompts. Safe mode combines this with read-only sandboxing; full mode is intentionally autonomous and carries the documented risk. |
| Codex `--sandbox` | `danger-full-access` or `read-only` | Full mode supports deep investigation; safe mode is a no-write posture and on Linux first requires a working `bwrap` preflight. |
| Codex `--model`, `model_reasoning_effort` | exact attested pair | Prevents implicit model/effort selection and makes the request auditable. |
| Codex `web_search` | `live` or `disabled` | Makes current research intent explicit instead of relying on the full-access default. |
| Codex `agents.enabled`, `agents.max_concurrent_threads_per_session` | derived helper policy | Uses the current documented multi-agent keys. Agent Collab does not claim a provider-enforced total-call, nesting-depth, or per-helper runtime limit. |
| Claude `-p` | non-interactive print mode | Required for structured one-shot automation and the CLI's budget/turn controls. |
| Claude `--model`, `--effort` | exact attested pair | Overrides implicit selection for the session. Agent Collab removes higher-precedence effort environment variables before launch and checks observed model usage afterward. |
| Claude `--permission-mode` | `bypassPermissions` or `plan` | Full mode preserves the configured powerful tool posture; safe mode uses provider-native plan permissions. `plan` is not an OS container. |
| Claude `--no-session-persistence` | enabled | Prevents the peer transcript from becoming a resumable Claude session; Agent Collab keeps its own bounded evidence. |
| Claude inline `--settings` | exact `availableModels`, empty `fallbackModel`, `switchModelsOnFlag=false`, hooks enabled, finite `Agent` hook | Narrows model selection, disables configured fallback and flagged-message model switching, and runtime-enforces the total Agent-call bound with command-line-priority settings. Managed policy remains authoritative; because non-managed arrays can merge, live and post-run exact-model checks are still required. [Anthropic settings](https://code.claude.com/docs/en/settings), [Anthropic model configuration](https://code.claude.com/docs/en/model-config) |
| Claude `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | `1` | Uses the documented depth control to prevent helpers from recursively spawning another helper layer. The total `Agent`-call hook and absolute peer deadline remain independent bounds. [Anthropic changelog](https://code.claude.com/docs/en/changelog) |
| Claude `--json-schema`, `--output-format json` | provider-compatible peer schema | Uses Claude's official structured-output envelope; the richer canonical schema and request relations are checked after extraction, and bare or cross-provider shapes are rejected. [Anthropic structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs) |
| Claude `--tools`, `--disallowedTools` | resolved built-ins | Starts from the configured built-in surface, then removes web tools when research is off and directly denies `Agent` when helpers are disabled. It does not claim control of MCP or plugin tools. |
| Claude `--max-turns` | omitted unless explicitly requested | Avoids truncating valid tool-heavy work. If opted in, a reached cap is a distinct structured failure. |

Availability has a narrower contract than a peer run. Codex refreshes `codex debug models` and accepts only the exact advertised pair. The Claude probe runs outside the repository with filesystem setting sources, built-in tools, and MCP discovery disabled, uses one turn because the only task is to return `AVAILABLE`, and requires both exact primary-model `modelUsage` and Stop-hook effort telemetry. That one-turn probe cap does not apply to the real peer run.

### Deliberately not enabled

- No default Claude turn or dollar budget is layered over the finite deadline. Either would be a second truncation policy; a turn cap remains explicit-only, and a future monetary cap would also need an explicit product contract.
- Normal peers do not use Claude `--bare`, `--setting-sources`, `--strict-mcp-config`, or troubleshooting `--safe-mode`, nor Codex `--ignore-user-config` or `--ignore-rules`. The full-capability product posture intentionally retains relevant repository guidance, configured tools, plugins, and provider policy. Only the narrow availability probe reduces configuration sources.
- Agent Collab does not append or replace provider system prompts. The compact user brief carries the task contract without claiming higher precedence than repository or managed instructions.
- Fallback chains remain empty and exact-model availability is fail-closed. A fallback would make the persisted request disagree with the model that actually ran.
- Provider effort `max` remains a quality-first Agent Collab default, not a claim that it is the cheapest or universally recommended setting. OpenAI reserves it for the hardest quality-first work, and Anthropic warns that Opus 5 can overthink and show diminishing returns at `max`.

## Configuration

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
- `AGENT_COLLAB_TIMEOUT_SECONDS`
- `AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED`
- `AGENT_COLLAB_MAX_LOCAL_SUBAGENTS`
- `AGENT_COLLAB_ADJUDICATOR_ENABLED`
- `AGENT_COLLAB_HISTORY_RETAINED_RUNS`

Run `doctor` to check versions, authentication, schemas, paths, and resolved defaults:

```bash
python tools/agent-collab/scripts/host.py doctor
```

`doctor` strictly parses and meta-validates every bundled schema. Because 2.0 has no migration layer, an invalid legacy settings or state file must be archived or removed before retrying. Rerun `setup` to create canonical settings; for legacy run state, archive the old run root and start with a fresh default root or explicit `--run-root`. Do not edit installed caches or ask the runtime to coerce old artifacts.

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

```bash
python -m unittest discover -s tests -v
python -m py_compile tools/agent-collab/scripts/*.py
scripts/sync-packages.sh --check
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/agent-collab
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" tools/agent-collab
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" plugins/agent-collab/skills/agent-collab
claude plugin validate . --strict
claude plugin validate plugins/agent-collab --strict
```

Before publication, parse every release JSON file strictly and test isolated marketplace installation on both hosts. Claude installation must pass `--scope user` explicitly.

The Codex and Claude manifests and root `VERSION` must always contain the same Semantic Versioning 2.0.0 version. Record user-visible and breaking changes in the root `CHANGELOG.md`; do not put changelogs inside skills. Version 2.0.0 intentionally rejects 1.x settings and run artifacts rather than translating them or probing compatibility fallbacks.
