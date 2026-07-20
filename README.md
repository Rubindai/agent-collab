# Agent Collab

Agent Collab is a peer-first, challenge-first collaboration plugin for Codex and Claude Code. It launches the opposite product with a neutral brief, keeps the host analysis independent, then validates and combines the evidence.

Version 1.0.0 is a breaking release. It has no compatibility layer for old settings, runs, package paths, installers, flags, prompts, or schemas.

## Defaults

| Peer | Default model | Effort |
| --- | --- | --- |
| Codex | `gpt-5.6-sol` | `max` |
| Claude Code | `claude-opus-4-8` | `max` |

These are deliberate quality-first product defaults requested for Agent Collab. Current provider guidance recommends lower levels for most work—Codex starts everyday work lower, and Anthropic recommends `xhigh` as the usual Opus 4.8 coding starting point—while reserving `max` for the hardest problems. An explicit user selection always takes priority when the exact pair is available.

The user can select a model and reasoning or effort level in natural language. The host translates an explicit selection into the per-run flags `--peer-model` and `--peer-effort`, then requires a fresh availability attestation for that exact pair before launch.

Claude peer effort may be a provider level (`low`, `medium`, `high`, `xhigh`, or `max`) or the explicit Claude Code orchestration mode `ultracode`. `ultracode` requests provider effort `xhigh` and enables dynamic orchestration, so availability telemetry intentionally records `requested_effort=ultracode` and `observed_effort=xhigh`. Agent Collab passes it only when the installed Claude Code accepts it and the live probe confirms that effective level; the same finite Agent-call guard still applies.

Codex effort support is not a hardcoded Agent Collab list. Immediately before launch, the runtime refreshes `codex debug models` and accepts only an effort advertised for the exact requested model. The current `gpt-5.6-sol` Codex CLI catalog advertises `ultra`, which enables automatic delegation, but the refreshed catalog remains authoritative.

Examples:

```text
Use Agent Collab with Fable 5 Max to review this change.
Use Agent Collab with Opus 4.8 Ultracode to audit the release.
Use Agent Collab and run the Codex peer on gpt-5.6-sol at high effort.
```

“Fable 5 Max” is the explicit alias for `claude-fable-5` with `max`. Fable is available only as a user-requested per-run model; it is never a built-in, environment, local, or global default.

Availability is checked before a run is created or a peer is launched:

- Codex must advertise the exact model and effort in the freshly refreshed `codex debug models` catalog.
- Claude Code must complete a bounded, configuration-reduced live probe whose API result `modelUsage` includes the exact resolved model and whose `Stop` hook reports the expected effective effort. The probe runs outside the target repository with filesystem setting sources, built-in tools, and MCP servers disabled; managed provider policy can still apply. Claude Code may also report its documented auxiliary Haiku usage; another primary model is a mismatch. For `ultracode`, the expected provider level is `xhigh`; for every provider-level request it is the requested value. A downgrade or organization cap does not pass as the requested pair.

Only an `available` attestation proceeds. `unavailable` and `unknown` are surfaced with their evidence and stop the launch. Agent Collab never substitutes or retries another model or effort.

Per-run flags have highest priority:

```text
per-run flag > environment > local settings > global settings > built-in
```

The explicit-only Fable rule is stricter than this general precedence order: `claude-fable-5` must come from a user-requested per-run override.

## Requirements

- Codex CLI 0.144.5 or newer
- Claude Code 2.1.214 or newer
- Python 3.10 or newer
- Git for Git-aware workspace snapshots; non-Git directories use filesystem snapshots

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

For a local checkout, pull the repository first. Codex reads the refreshed local source; for Claude, run the explicit plugin update or reinstall the same version only while developing an unpublished release.

Official references:

- [OpenAI: Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [OpenAI: Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [OpenAI: Current model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI: GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [OpenAI: Codex models](https://learn.chatgpt.com/docs/models)
- [OpenAI: `codex debug models`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-debug-models)
- [OpenAI: Codex prompting](https://developers.openai.com/codex/prompting)
- [OpenAI: Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Anthropic: Create plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [Anthropic: Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Anthropic: Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- [Anthropic: Model and effort configuration](https://code.claude.com/docs/en/model-config)
- [Anthropic: Claude Opus 4.8 model](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8)
- [Anthropic: Prompting Claude Opus 4.8](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-4-8)
- [Anthropic: Claude Fable 5 model](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5)
- [Anthropic: Prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
- [Anthropic: Effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic: Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Anthropic: Structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [Anthropic: Permission modes](https://code.claude.com/docs/en/permission-modes)
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
  --max-turns <resolved-limit>
  --no-session-persistence
  --settings '{"fallbackModel":[],"disableAllHooks":false,"hooks":{...}}'
  --json-schema ...
  --output-format json
```

Agent Collab safe mode changes a Codex peer to `read-only` and a Claude peer to `plan`; it is unrelated to Claude Code's separate `--safe-mode` troubleshooting flag. On Linux, a safe-mode Codex peer preflights the `bwrap` backend used by the Codex read-only sandbox. If that backend is missing or cannot initialize, `start` emits a structured sandbox-unavailable result and stops before creating a run. A safe-mode Claude peer uses Claude's provider-native `plan` permission mode; `plan` is a permission boundary, not an OS container. Agent Collab itself neither requests nor preflights `bwrap` for Claude or in full-capability mode, although Claude settings loaded from other policy sources can independently enable Claude's sandbox. Online research can be changed independently with `--online-research` or `--no-online-research`.

The reported pre-1.0 message `Codex sandbox is still broken (bwrap user-namespace failure) — implementing directly as planned.` was investigated. Its claimed historical cause is not present in this repository or its Git history, so that provenance is unverified. The current Linux host does reproduce a Codex `bwrap` prerequisite failure while configuring the isolated loopback interface. Version 1.0.0 gives the message no authorization meaning: a peer failure never authorizes direct host implementation, Agent Collab full mode does not request `bwrap`, and a safe-mode Codex preflight emits `sandbox_unavailable` and stops before launch artifacts are created.

The online-research toggle controls the provider's built-in web research tools used by Agent Collab. It is not a general network-isolation guarantee: full-capability shells, MCP tools, plugins, and provider configuration may have separate network behavior.

Persisted settings, state, requests, helper reports, and synthesis artifacts use strict schema 2.0 contracts. Old, incomplete, noncanonical, or wrongly typed files are rejected rather than migrated or coerced. Claude success is accepted only from `structured_output`; Codex success is accepted only from its required `--output-last-message` artifact.

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

Peer prompts use one compact, escaped XML brief with role, outcome, evidence, boundaries, task, and stop sections. This follows current Codex guidance to state the goal, relevant context, output, boundaries, and completion condition, together with Claude Code guidance to give specific context and a check the agent can use to verify its work. The brief starts with the desired result, includes only process constraints that matter to independence or safety, and does not reveal host conclusions or suspected findings. In review mode it asks the peer to surface every in-scope issue, including uncertain and low-severity candidates, and to label severity and confidence for host verification instead of suppressing recall with a vague importance filter. Request JSON and response schemas are not duplicated into the prompt; the CLI output controls and runtime validation handle the structured report.

The prompt is an instruction to the peer, not a sandbox or a higher-precedence security boundary. Provider-managed instructions and repository guidance may still load through the peer CLI. Technical guarantees in this document refer only to runtime checks such as launch validation, structured-output validation, the workflow guard, the Codex safe-mode sandbox preflight, and Claude's selected permission mode.

## Permissions and recursion

Full capability is the configured default launch posture for deep investigation and validation. It is powerful: use it only where host-level consequences are acceptable. OpenAI advises caution with `danger-full-access`, and Anthropic recommends `bypassPermissions` only inside isolated containers, virtual machines, or development containers without internet access where host damage is impossible; trusting a repository is not a substitute for that isolation. Select Agent Collab safe mode explicitly when full host access is not appropriate:

- Codex peers use `danger-full-access` with approval set to `never`.
- Claude peers use `bypassPermissions`; `--tools default` exposes every current built-in tool. `bypassPermissions` skips the permission layer and does not protect against prompt injection or unintended actions. Provider and organization policy can still refuse this mode.
- Native helpers retain the full tool and permission context of the host in full mode. Agent Collab adds no helper `tools` or `disallowedTools` restriction, while provider, organization, session, and tool-specific policies still apply. Configured MCP tools are not restricted by Claude's `--tools` built-in-tool selector.

Capability is not permission to edit. `edit_allowed` defaults to `false` independently of the launch permission mode. The host may pass `--edit-allowed` only when the user explicitly delegates repository changes. In full-capability mode the no-edit instruction is advisory and mutation detection is detective, not a preventive filesystem boundary. The host records changes visible between its before/after snapshots—including tracked, untracked, most ignored, and selected Git-control surfaces—even if the peer crashes or is cancelled. Known high-volume generated or dependency directories are excluded, and transient changes reverted before a snapshot are not observable, so the diagnostic cannot prove attribution or complete immutability.

Only one Agent Collab workflow can be active per Git worktree. `start` atomically acquires a guard under the worktree Git metadata before creating artifacts; every runtime copy for that worktree sees the same guard. A stable per-worktree OS file lock serializes every guard check, stale-start recovery, acquisition, update, and release with a bounded lock wait, so a stale recoverer cannot delete a replacement run. A startup gate prevents the peer wrapper from running until that guard and its process artifact are committed, and a second gate prevents provider exec until a strict provider-process tracker is committed. The guard remains active across the peer, host-local helpers, adjudication, and synthesis. `complete` or `cancel` releases it only after tracked wrapper/provider groups are terminal and quiescent. This guard is the hard cross-product recursion boundary. The runtime separately blocks Claude peer `Agent` calls above the resolved per-run bound, configures Codex peer thread/depth/runtime ceilings, and rejects helper-report admission beyond that bound, eight helpers by default. Host-native helper launch discipline is also stated in the skill, but the shared runtime cannot intercept every host tool call; full tools and permissions do not relax the enforceable limits.

For Claude runs, the runtime removes session-effort environment overrides, supplies an empty configured `fallbackModel` chain, and explicitly keeps its CLI hook controls enabled. Command-line settings outrank user, project, and local settings; managed policy remains authoritative. The availability probe requires its Stop hook to run, so managed policies that suppress non-managed hooks fail the launch closed. A zero helper limit also directly disallows `Agent`. The preflight requires live API `modelUsage` and the expected Stop effective effort to match the request; post-run usage is checked again. Agent Collab never selects or retries another model or effort.

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
  --online-research
```

Environment keys:

- `CODEX_AGENT_COLLAB_MODEL`
- `CODEX_AGENT_COLLAB_EFFORT`
- `CLAUDE_AGENT_COLLAB_MODEL`
- `CLAUDE_AGENT_COLLAB_EFFORT`
- `AGENT_COLLAB_ONLINE_RESEARCH`
- `AGENT_COLLAB_SAFE_MODE`
- `AGENT_COLLAB_TIMEOUT_SECONDS`
- `AGENT_COLLAB_LOCAL_SUBAGENTS_ALLOWED`
- `AGENT_COLLAB_MAX_LOCAL_SUBAGENTS`

Run `doctor` to check versions, authentication, schemas, paths, and resolved defaults:

```bash
python tools/agent-collab/scripts/host.py doctor
```

After the host has synthesized a run:

```bash
python tools/agent-collab/scripts/host.py complete RUN_ID
```

## Repository layout

```text
.agents/plugins/marketplace.json                  Codex repo marketplace
.claude-plugin/marketplace.json                   Claude Code marketplace
plugins/agent-collab/                             Unified Codex/Claude package and Claude helper agents
tools/agent-collab/                               Canonical skill workflow, runtime, references, and schemas
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

The Codex and Claude manifests and root `VERSION` must always contain the same Semantic Versioning 2.0.0 version. Record user-visible and breaking changes in the root `CHANGELOG.md`; do not put changelogs inside skills. Version 1.0.0 intentionally rejects legacy settings, artifacts, flags, and paths rather than translating them or probing compatibility fallbacks.
