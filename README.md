# Agent Collab

Agent Collab is a peer-first, challenge-first collaboration plugin for Codex and Claude Code. It launches the opposite product with a neutral brief, keeps the host analysis independent, then validates and combines the evidence.

Version 6.0.0 requires Python 3.11 or newer, since Python 3.10 reached end of life, and accepts only ASCII decimal numbers for timeouts and numeric flags. It builds on 5.0.x, which added native Windows hosts, runs outside Git repositories without Git installed, pinned headless Codex approval and helper caps regardless of user `config.toml`, disabled provider memory for peers, and fixed macOS process-group supervision. See [CHANGELOG.md](CHANGELOG.md) for rejected values and upgrade steps. Strict schema 2.0 remains in force, and 1.x settings and run artifacts still have no compatibility layer.

**Latest provider-documentation and workflow evaluation: 2026-10-25.** A comprehensive audit checked the provider contracts used by this project against current OpenAI and Anthropic documentation, Codex 0.164.0 source, and independent Agent Collab Claude reviews. See the [source and coverage record](tools/agent-collab/references/provider-audit.md) and [evaluation evidence](#evaluation-evidence) for scope and limitations. Version 3.0.0 appeared on GitHub `main` without a release tag; formal tags begin with `v4.0.0`.

## Defaults

| Peer | Default model | Effort |
| --- | --- | --- |
| Codex | `gpt-6-astra` | `xhigh` |
| Claude Code | `claude-opus-5-5` | `xhigh` |

These are the project defaults requested for Agent Collab. Existing persisted settings and per-run overrides retain precedence; installing this version does not rewrite them. Every requested pair still requires fresh availability evidence, with no fallback.

The user can select a model and reasoning or effort level in natural language. The host translates an explicit selection into the per-run flags `--peer-model` and `--peer-effort`, then requires a fresh availability attestation for that exact pair before launch.

Claude peer effort may be `low`, `medium`, `high`, `xhigh`, or `max`, subject to exact availability. Claude Code also recognizes `ultracode`, an orchestration mode with effective provider effort `xhigh`. Agent Collab rejects an explicit `ultracode` run with `helper_policy_unavailable` before creating run artifacts: a live test showed that Workflow agents bypass the finite Agent-call counter, and the documented Workflow agent budget does not implement this per-run bound. The runtime disables and denies Workflow for ordinary peers. It never substitutes `xhigh` for an explicit `ultracode` request. `check-availability` still reports provider support separately from this execution-policy restriction. [Claude workflows](https://code.claude.com/docs/en/workflows#behavior-and-limits)

Codex effort support is not a hardcoded Agent Collab list. Immediately before launch, the runtime runs `codex debug models` and accepts only an effort advertised for the exact requested model. Codex serves that catalog from a five-minute cache or an online refresh, and can silently fall back to its bundled catalog when a refresh fails, so the attestation reflects the catalog Codex resolves, not a guaranteed network fetch. `codex exec --json` carries no resolved model or effort. Codex reports a reroute as an `item.completed` error item beginning `model rerouted:`; any reroute to another model fails the run. Without such an item, the evidence is the catalog plus the explicit launch arguments, not an independently observed provider selection.

Examples:

```text
Use Agent Collab with Fable 5 Max to review this change.
Use Agent Collab with Opus 5.5 xhigh to audit the release.
Use Agent Collab and run the Codex peer on gpt-6-astra at high effort.
```

“Fable 5 Max” is the explicit alias for `claude-fable-5` with `max`. Fable is available only as a user-requested per-run model; it is never a built-in, environment, local, or global default. Anthropic now lists Fable 5 as legacy but still available. For the newer Fable 5.1, explicitly pass `--peer-model claude-fable-5-1` with the desired effort; the named Fable 5 alias continues to select exactly Fable 5. [Anthropic models](https://platform.claude.com/docs/en/models/overview)

Availability is checked before a run is created or a peer is launched:

- Codex must advertise the exact model and effort in the `codex debug models` catalog it resolves at launch.
- Claude Code must complete a bounded, configuration-reduced live probe whose API result `modelUsage` includes the exact resolved model and whose `Stop` hook reports the expected effective effort. The probe runs outside the target repository with filesystem setting sources, built-in tools, and MCP servers disabled; managed provider policy can still apply. Because user and project settings are skipped, a lower user or project `maxEffortLevel` or a model allow/deny list can still clamp or refuse the real peer; the actual-run Stop and `modelUsage` checks then fail the run. Claude Code may also report its documented auxiliary Haiku usage; another primary model is a mismatch. For `ultracode`, the expected provider level is `xhigh`; for every provider-level request it is the requested value. A downgrade or organization cap does not pass as the requested pair.

Only an `available` attestation proceeds. `unavailable` and `unknown` stop the entire collaboration attempt before a run, peer, helper, host-first-pass, or adjudicator is created, and the host must notify the user with the probe evidence. Model/effort rejection, authentication or authorization denial, and provider quota/rate-limit responses are reported as unavailable; timeouts, malformed telemetry, and indeterminate provider failures remain unknown. Agent Collab never substitutes or retries another model or effort.

Per-run flags have highest priority:

```text
per-run flag > environment > local settings > global settings > built-in
```

The explicit-only Fable rule is stricter than this general precedence order: `claude-fable-5` must come from a user-requested per-run override.

## Requirements

- Codex CLI 0.163.0 or newer
- Claude Code 2.1.295 or newer
- Python 3.11 or newer, invoked as `python3` in the commands below (`py -3` or `python` on Windows)
- Git, inside Git repositories, for the worktree guard and Git-aware snapshots; `start` refuses a Git repository when `git` is not on `PATH`. A folder outside any Git repository uses filesystem snapshots and needs no Git installed
- Linux, macOS, or Windows 10/11. POSIX hosts supervise peers with process groups and `flock`; native Windows uses Job Objects and byte-range locks (see [Windows](#windows)), is covered by CI, and has not yet completed a live peer run. Other platforms are rejected before run artifacts are created.
- On Linux, the default disposable workspace requires `fuse-overlayfs`, `fusermount3` (or `fusermount`), and `bwrap` with working FUSE/user namespaces. `start` runs a write/discard probe before creating a run. Fresh macOS settings default to direct mode with bounded persisted snapshots. Explicit isolated settings on macOS fail before run creation; they never fall back to direct.

These minimums cover the current Codex helper-default controls and Claude's forced helper-model selection. `start` checks the required peer CLI version before creating a run directory. Older CLIs are rejected; no alternate flags or commands are attempted.

## Install

### Linux isolation dependencies

Linux full-capability runs use a disposable workspace and need Bubblewrap, FUSE OverlayFS, and a FUSE unmount tool. From a repository checkout, check for the required commands or install the distribution packages:

```bash
scripts/install-linux-isolation-deps.sh --check
scripts/install-linux-isolation-deps.sh --install
python3 tools/agent-collab/scripts/host.py doctor
```

The script installs only the missing packages among `bubblewrap`, `fuse-overlayfs`, and `fuse3` with `apt-get` on Debian/Ubuntu or `dnf` on Fedora; an existing fuse2 `fusermount` satisfies cleanup, so `fuse3` is not added beside it. It uses `sudo` when needed and skips installation when the commands are already available. With isolated workspace mode selected, `doctor` verifies that FUSE and user namespaces work on this host; having the commands installed alone is insufficient. On other Linux distributions, install those packages with the distribution's package manager, then run `doctor`. The script does not install the Agent Collab plugin; use the host marketplace steps below.

### Windows

Native Windows runs need the native `claude.exe` and `codex.exe` first on `PATH`. Git for Windows is needed only for Git repositories, as on other platforms; Git Bash is not needed, since Agent Collab's Claude hooks use exec form and need no shell. Install Claude Code with `irm https://claude.ai/install.ps1 | iex` or `winget install Anthropic.ClaudeCode`; WinGet installs do not update themselves, so run `winget upgrade Anthropic.ClaudeCode` to stay above the minimum version. Install Codex with the installer from the [Codex README](https://github.com/openai/codex#installing-and-running-codex-cli), `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`. A provider CLI that resolves to an npm `.cmd` or `.bat` shim is rejected before launch, because `cmd.exe` re-parses shim arguments and would corrupt the inline JSON settings and quoted config values. Run the commands in this README with `py -3` (or `python`) in place of `python3`; the runtime sets UTF-8 with LF line endings on its own standard streams.

Each tracked process group is a named Job Object rooted at the peer wrapper or at the gated provider launcher. The root starts suspended and runs only after it is assigned, so nothing it launches predates the job, including the real interpreter behind a virtual-environment `python.exe`. The job kills its members when the root exits and does not permit breakaway, so ordinary and detached descendants stay inside the quiescence proof. It is not a containment boundary: a same-user process can open the job and change its limits, and processes started through system brokers (services, Task Scheduler, WMI, COM servers, WSL, Docker) start outside it. Windows has no group-wide graceful signal, so `cancel` and the deadline terminate the job directly. Job names live in the Windows session that started the run; from another session, `cancel` finds the root alive but the job invisible and refuses to release the guard, so run it from the starting session.

The detached wrapper breaks away from the host CLI's job so that it outlives the `start` command. If that job forbids breakaway, `start` reports the error after creating the run and rolls back the guard and run directory. Breakaway stops at the first enclosing job that forbids it: in that case the wrapper stays in that outer job and ends when the job closes, which fails the run without releasing the guard early.

Fresh settings use direct workspace mode with mutation snapshots, as on macOS; explicit isolated mode fails before run creation. Codex safe mode is unavailable on native Windows: Agent Collab cannot verify which Codex Windows sandbox, if any, is active, so `start` reports `sandbox_unavailable`. Use full mode, a Claude peer (whose safe mode uses `plan` permissions), or WSL2, which runs Agent Collab as Linux with the disposable workspace. Do not drive one worktree from native Windows and WSL at the same time: their locks do not exclude each other. Host commands treat process identities that another platform recorded as unknown and refuse to finish, complete, or cancel such a run, so it stays guarded until it is handled from the platform that started it. The same applies to a worktree shared between Linux and macOS. POSIX modes are not applied on Windows. Run files inherit the access control list of their directory, which is restricted to the user inside the user profile but may be broader for a checkout elsewhere, such as `C:\src`.

Native Windows support is covered by mocked unit tests and by the Windows CI job, `tests/windows_runtime_checks.py` on Python 3.11 and 3.15, which passes on GitHub's `windows-latest` runner. It exercises Job Object containment of detached descendants, suspended start, a virtual-environment interpreter, startup gates, NTSTATUS exit-code relay, byte-range locks, UTF-8 stdio, executable resolution, and `doctor`. The detached-launch check is skipped there because the runner's own job forbids breakaway, which is the case `start` fails closed on. No live Windows peer run has been performed.

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

For a local checkout, pull the repository first and rerun `codex plugin add agent-collab@agent-collab`, then start a new Codex session. Current Claude Code reads a local marketplace's relative-path plugin directly from that checkout; use `/reload-plugins` or start a new session after edits. `installed_plugins.json` still records a cache `installPath`; `claude plugin list --json` shows the real load folder as `readFromFolder`. Hosted Claude marketplaces use cached copies and the update flow above. Check the installed source and package contents, not only the version or cache timestamp. Direct cache edits are unsupported. [Claude plugin loading](https://code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins), [Codex plugin commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-plugin)

Official references:

- [OpenAI: Package plugins](https://developers.openai.com/plugins/build/plugins)
- [OpenAI: Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [OpenAI: Current model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI: GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md)
- [OpenAI: Codex models](https://learn.chatgpt.com/docs/models)
- [OpenAI: `codex debug models`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-debug-models)
- [OpenAI: GPT-6 Astra prompting guidance](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#prompting-best-practices)
- [OpenAI: Rethinking skills and prompts for GPT-6 Astra](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
- [OpenAI: Codex prompting](https://learn.chatgpt.com/docs/prompting)
- [OpenAI: Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Project instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [OpenAI: API prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic: Create plugin marketplaces](https://code.claude.com/docs/en/plugins/create-marketplace)
- [Anthropic: Plugin manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference)
- [Anthropic: Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
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

Codex (plugin skills are namespaced, so the explicit mention is the full skill name):

```text
Use $agent-collab:agent-collab to review the current diff.
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
codex exec --strict-config --ephemeral --json --cd <repo>
  --sandbox danger-full-access|read-only
  --output-schema <provider schema> --output-last-message <tmp>/peer-output.json
  --model <resolved-model>
  [-c <user codex_config entry>]...
  -c model_reasoning_effort="<resolved-effort>"
  -c web_search="live|disabled"
  -c approval_policy="never" -c approvals_reviewer="user"
  -c features.memories=false
  -c memories.use_memories=false -c memories.generate_memories=false
  -c features.agent_message_board=false
  -c agents.enabled=true -c agents.max_concurrent_threads_per_session=<N> -c agents.max_depth=1
  -c features.multi_agent_v2.max_concurrent_threads_per_session=<N+1>
  -c features.multi_agent_v2.expose_spawn_agent_model_overrides=false
  -c agents.default_subagent_model="<model>" -c agents.default_subagent_reasoning_effort="<effort>"
  -

claude -p
  --model <resolved-model>
  --effort <resolved-effort>
  --permission-mode bypassPermissions|plan
  --no-session-persistence
  --settings '{"availableModels":["<model>"],"fallbackModel":[],"switchModelsOnFlag":false,
              "crossSessionInbound":"refuse","disableWorkflows":true,"ultracode":false,
              "autoMemoryEnabled":false,"disableAllHooks":false,"env":{...},
              "hooks":{"PreToolUse":[Agent counter],"Stop":[effort capture]}}'
  --json-schema <provider schema>
  --output-format json
  --disallowedTools Workflow[,WebSearch,WebFetch],Agent(fork)|Agent
  --tools default|<list>
```

`--tools` is last because it is variadic. `Agent(fork)` is denied while helpers are enabled; with helpers disabled the whole `Agent` tool is denied instead. With helpers disabled, Codex receives `agents.enabled=false` and `features.multi_agent_v2=false` instead of the helper caps. The Claude peer process (and its settings `env`) also sets `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, which keeps provider-launched helpers to one layer, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_WORKFLOWS=1`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0`, and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` equal to the peer deadline so print mode does not drop background helper results after its default ten idle minutes. The finite total `Agent`-call hook remains a separate bound.

The normal Claude command deliberately omits `--max-turns`. Anthropic documents no turn limit by default. A user can opt into a positive cap for one run with `--claude-max-turns N`; only then does Agent Collab add `--max-turns N`.

Agent Collab safe mode changes a Codex peer to `read-only` and a Claude peer to `plan`; it uses direct workspace mode unless an explicit incompatible isolated selection is rejected. On Linux, a safe-mode Codex peer initializes the provider's `bwrap` backend with `codex sandbox --permission-profile :read-only` and a no-op command. Codex resolves its own backend, including npm-bundled resources; Agent Collab does not guess installation paths. This capability check uses the fixed built-in profile without managed-profile remapping; the actual peer still loads managed policy, so the check does not attest its effective permissions. On macOS, Codex applies its own Seatbelt sandbox, which Agent Collab does not preflight; on native Windows, Codex safe mode fails with `sandbox_unavailable`. A safe-mode Claude peer uses provider-native `plan` permissions, not an OS container. Full-capability isolated mode separately preflights FUSE OverlayFS and `bwrap` for either peer, then gives the provider full tool permissions inside a disposable copy-on-write workspace. A failed preflight returns structured `sandbox_unavailable` evidence before run creation. Online research can be changed independently with `--online-research` or `--no-online-research`.

The sandbox initialization check does not establish a complete safe-mode provider run. It is separate from the disposable-workspace write/discard preflight and exact-pair availability attestation.

The online-research toggle controls the provider's built-in web research tools used by Agent Collab. For Claude Code these are the CLI's built-in `WebSearch` and `WebFetch` tools. This toggle is not a general network-isolation guarantee: full-capability shells, MCP tools, plugins, and provider configuration may have separate network behavior.

`codex exec` does not inherit the root `--ask-for-approval` flag, and it drops its headless `never` default when the user's config sets `approvals_reviewer = "auto_review"`. The explicit `approval_policy` and `approvals_reviewer` overrides keep every peer non-interactive with no auto-reviewer, in both modes. Managed Codex configuration can still outrank command-line overrides.

Persisted settings, state, requests, helper reports, and synthesis artifacts use strict schema 2.0 contracts. Old, incomplete, noncanonical, or wrongly typed files are rejected rather than migrated or coerced. Claude success is accepted only from `structured_output`; Codex success is accepted only from its required `--output-last-message` artifact. The provider-facing `peer-report-provider.schema.json` intentionally uses the portable structured-output subset accepted by both CLIs. The runtime then validates the result against the canonical `peer-report.schema.json` and semantic request relations, including exact run, origin, host, peer, mode, and target binding. `peer-normalization.schema.json` separately constrains raw-provider extraction and canonical-report validation evidence; host reconstruction preserves extraction warnings.

Run directories are created with owner-only permissions (`0700`) and runtime JSON, raw output, stderr, snapshots, settings, state, locks, and counters are written as owner-only files (`0600`) on POSIX systems, independent of the caller's umask. Writes reject symlink destinations where the platform exposes `O_NOFOLLOW`. On Windows, POSIX modes are not applied and files inherit their directory's access control list (see [Windows](#windows)). Artifacts use LF line endings on every platform.

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

Full capability is the configured default. On Linux, the default disposable workspace lets the peer write temporarily to an overlay of the repository and HOME; those writes are discarded after the run. The rest of the host filesystem is mounted read-only, except a private temporary directory for provider outputs and the peer product's credential file (`auth.json` under `CODEX_HOME`, default `~/.codex`, or `.credentials.json` under `CLAUDE_CONFIG_DIR`, default `~/.claude`), which is bound through so a rotated refresh token persists and the host stays logged in. A symlinked credential file is not bound, so a token refreshed during the run is discarded with the overlay. `/tmp` and `/var/tmp` are fresh empty tmpfs mounts, `/dev` holds only the minimal device set, and the peer runs in its own PID namespace. Host-owned run artifacts are not writable from the sandbox. On macOS and native Windows, fresh settings default to direct host access with before/after mutation snapshots. These snapshots detect included changes after the fact; they do not prevent or undo edits.

Neither posture is a containment boundary. The isolated sandbox deliberately keeps host-facing facilities reachable so full-capability peers can run real tooling: networking, MCP tools, plugins, the user's Docker daemon socket (root-equivalent for members of the `docker` group, and its bind mounts resolve the real repository rather than the overlay), the D-Bus session bus and systemd user manager (which can start processes outside the sandbox), SSH agents whose socket lives under `/run` (such as systemd or desktop keyring agents), and external services. Sockets under the host `/tmp` (a classic `ssh-agent`, X11) are hidden by the fresh tmpfs, and sockets under HOME are seen only through the overlay. Writes made through those channels persist, and isolated mode takes no content snapshot that would detect them. Anthropic recommends `bypassPermissions` only inside isolated containers, virtual machines, or development containers without internet access where host damage is impossible. Select safe mode for provider-native restrictions; on Linux, use `--workspace-mode direct` when direct host access is intended:

- Codex peers use `danger-full-access` with `approval_policy="never"` and `approvals_reviewer="user"` passed to `codex exec`.
- Claude peers use `bypassPermissions`; `--tools default` selects Claude Code's default built-in set (on macOS, Linux, and WSL that set omits `Glob` and `Grep`, and some tools are opt-in). `bypassPermissions` skips the permission layer and does not protect against prompt injection or unintended actions. Provider and organization policy can still refuse this mode, and on Linux and macOS Claude Code refuses it as root or under `sudo` outside a sandbox it recognizes; the availability probe runs in `plan` mode and does not catch that. [Anthropic permission modes](https://code.claude.com/docs/en/permission-modes)
- Native helpers keep the host's permission context in full mode; Claude subagents that run in the background receive Claude Code's documented background tool subset. Agent Collab adds no helper `tools` or `disallowedTools` frontmatter restriction; the peer launch separately applies research and zero-helper controls, plus Claude-specific conversation-fork controls, while provider, organization, session, and tool-specific policies still apply. Configured MCP tools are not restricted by Claude's `--tools` built-in-tool selector.

For a non-Git workspace, the Codex peer launcher passes `--skip-git-repo-check` after a lightweight Git probe in isolated mode or the filesystem snapshot in direct mode. Codex keeps its repository check for Git workspaces. [OpenAI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode#git-repository-required)

Capability is not permission to make durable edits. `edit_allowed` defaults to `false`; `--edit-allowed` requires explicit delegation and must be paired with `--workspace-mode direct`, because isolated writes are discarded. Isolated mode writes a small boundary receipt instead of hashing repository contents. Its host mutation result describes the overlay boundary and does not detect changes made outside it. Provider reports and hook receipts are written to the private temporary directory and read by the wrapper after the provider exits; the run directory itself is not writable from the sandbox. The peer can still affect network services and sockets that remain accessible.

Direct mode retains before/after mutation snapshots as detective evidence. The baseline JSON must fit the 16 MiB reader limit, so large tracked-status and file manifests retain complete digests and counts with bounded path samples; Git index and patch output is streamed into digests. The persisted snapshot stays bounded as the file count grows, but scanning included contents still scales with file count and bytes. A very large ignored tree can exhaust the finite 300-second snapshot deadline. Direct mode covers tracked, untracked, most ignored, and selected Git-control surfaces. Tracked files are compared through `git status`, raw `git diff --binary --no-textconv --submodule=diff` digests, a `git ls-files --stage -v` index digest (which also records assume-unchanged and skip-worktree flags), and a tracked-path manifest of file type and permission bits, with content hashes for paths Git deliberately skips. A read-only `git status` that refreshes the index stat cache is not reported as a mutation. Snapshot Git commands ignore inherited `GIT_*` variables and disable fsmonitor. Untracked and ignored files are excluded at any depth inside directories named `.cache`, `.git`, `.hg`, `.mypy_cache`, `.next`, `.pytest_cache`, `.ruff_cache`, `.svn`, `.temp`, `.tmp`, `.tox`, `.venv`, `__pycache__`, `build`, `coverage`, `dist`, `htmlcov`, `node_modules`, `target`, `temp`, `tmp`, or `venv`; tracked files in those directories remain covered. Untracked files inside submodules, transient changes reverted between snapshots, and changes outside the repository are not observed. The wrapper reuses the host's committed baseline instead of scanning twice. Interrupted cancellation reuses its committed snapshot diagnostic.

Only one Agent Collab workflow can be active per Git worktree. `start` atomically acquires a guard under the worktree Git metadata before creating artifacts; every runtime copy for that worktree sees the same guard, a symlinked guard is rejected, and the bounded Git probes that locate it ignore inherited `GIT_*` variables. Because each host keeps its own run root, `status`, `result`, `finish`, `complete`, and `cancel` also resolve the active run by ID through that guard. A stable per-worktree OS file lock serializes every guard check, stale-start recovery, acquisition, update, and release with a bounded lock wait, so a stale recoverer cannot delete a replacement run. Unavailable process-identity evidence preserves the guard; only a proven dead or replaced starting launcher permits automatic recovery. A separate bounded per-run operation lock serializes terminal `finish`, `complete`, and `cancel` transitions without holding a lock while `finish` waits for a live provider. A startup gate holds the peer wrapper's work until that guard and its process artifact are committed (the wrapper only validates its request and records a pending provider tracker first), and a second gate prevents provider exec until a strict provider-process tracker is committed. The guard remains active across the peer, host-local helpers, adjudication, and synthesis. `complete` or `cancel` releases it only after tracked wrapper/provider groups are terminal and quiescent. On POSIX this is process-group supervision, not descendant containment: detached groups, remote work, and external sessions are outside that proof. On Windows the tracked group is a Job Object that ordinary and detached descendants cannot break away from; brokered launches, remote work, and external sessions are outside it, and a same-user process can still change the job. This guard is the hard cross-product recursion boundary. The runtime blocks Claude peer `Agent` calls above the resolved total-call bound and sets Claude's documented subagent-spawn depth to one layer; a zero bound directly denies `Agent`. It configures Codex's concurrent spawned-thread cap through both `agents.max_concurrent_threads_per_session` and the `features.multi_agent_v2` table, which Codex reads first, so a user `config.toml` cannot widen it. For models whose Codex catalog entry selects multi-agent V2, including the default `gpt-6-astra`, model and effort override fields are hidden from the spawn tool schema (`expose_spawn_agent_model_overrides=false`); the handler can still accept supplied fields, so the brief forbids conflicting overrides on both versions. A model on multi-agent V1 exposes those fields and has nesting depth pinned with `agents.max_depth=1`. Neither version universally rejects conflicting helper selections. Codex total helper calls, and nesting depth under V2, remain explicit instructions rather than provider-enforced ceilings. An explicitly requested Codex `ultra` effort runs at the catalog's multi-agent effort (`xhigh` for `gpt-6-astra`) with proactive task delegation, and is propagated to helper defaults under the same bounds. The Claude `Agent`-call counter lives in the provider's writable temporary directory, so it bounds cooperative use; a peer with shell access could reset it. Outbound cross-session tools (`SendMessage`, `ListAgents`) remain available to a Claude peer; only inbound messages are refused. The absolute peer deadline bounds the tracked provider execution. The host evaluates that deadline against wall-clock time recorded at launch, so time spent suspended counts toward it. Helper-report admission is capped at the resolved bound, eight by default. Workflow agents were observed bypassing the Agent hook in an isolated live zero-helper test, so Workflow is disabled in command-line settings and denied as a tool. The brief also restricts delegation to ordinary native helpers. Skills with `context: fork` also create subagents; this audit did not establish that those spawns pass through the Agent-call hook. The instruction to delegate only through ordinary native Agent helpers applies to them too. Other facilities such as shells, MCP, mods (whose `agent.spawn` API bypasses the `Agent` tool), remote sessions (including RemoteTrigger), scheduled work (including CronCreate), and external processes remain outside a universal spawn cap. Host-native helper launch discipline is also stated in the skill, but the shared runtime cannot intercept every host tool call.

Use helpers when work benefits from separate context or independent verification; keep tightly coupled work in the main loop. The role catalog offers optional lenses, not a required pipeline. The spawner starts independent helpers in fresh contexts with a neutral bounded task, required inputs, edit authorization, remaining deadline, and expected evidence. Codex spawners must explicitly pass `fork_turns="none"`, because omission inherits parent history; this is an instruction, while Claude forks are disabled by launch controls. Do not copy parent conversation history or use custom agents or explicit spawn arguments that override the spawning agent's model or effort. Reuse a helper for follow-up work. The Codex prompt explicitly limits total spawns to the resolved helper count even after threads close. Concurrency is provider-enforced, V1 configures `agents.max_depth=1`, and V2 nesting depth remains instructional. Claude inbound cross-session messages are refused to preserve review independence. Its own helper `SendMessage` resumes remain available and do not consume an `Agent` call, so the call counter is not a cap on all resumed work; the absolute peer deadline still applies. For explicitly authorized parallel edits, assign disjoint writable paths and one integration owner, serialize overlaps, and preserve other agents' changes. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Anthropic subagents](https://code.claude.com/docs/en/sub-agents)

Cancellation is append-only. It never replaces `peer-report.json`, `peer-normalization.json`, `host-result.json`, `host-synthesis.json`, or `claim-matrix.json`. After proving process-tree quiescence it writes immutable `cancel-result.json`, hashes the designated pre-existing report, raw-output, normalization, result, synthesis, and claim-matrix artifacts, and records mutation comparison separately in `cancel-workspace-mutation.json`. Other telemetry files are not covered by those hashes. If a valid terminal peer report already exists, `cancel` keeps the guard and directs the host to `finish` so completed evidence is not reclassified as cancellation.

For Claude runs, the runtime removes conflicting effort environment overrides, sets `CLAUDE_CODE_EFFORT_LEVEL` to the requested provider level, and supplies command-line settings that narrow selection with `availableModels` containing the requested model, empty `fallbackModel`, set `switchModelsOnFlag=false`, and explicitly keep hooks enabled. Command-line settings outrank user, project, and local settings; managed policy remains authoritative. Current Claude Code replaces the ordered `fallbackModel` chain by precedence, while non-managed `availableModels` arrays can merge and model entries can prefix-match extended identifiers. `availableModels` is not an exact model lock. Refusal fallback is separate: `switchModelsOnFlag=false` ends a flagged non-interactive main turn with a refusal, but subagents have documented exceptions. Exact-model live attestation and post-run `modelUsage` checks therefore remain necessary; these are not a universal prevention guarantee for provider substitutions. Claude Code checks the fallback model against `availableModels` and blocks a fallback that list excludes. The probe requires its Stop hook to run, so policies that suppress it fail launch closed. Agent Collab never selects or retries a fallback and rejects observable primary-model mismatches. The exact `modelUsage` match and the auxiliary-Haiku allowance target first-party model identifiers; Bedrock, Vertex, Foundry, or gateway identifiers fail closed as unexpected models. The runtime depends on non-bare `-p`, which loads OAuth sign-in and `--settings` hooks; if Claude Code makes `--bare` the `-p` default, the Stop-hook evidence fails closed until an explicit opt-out is adopted. [Settings precedence](https://code.claude.com/docs/en/settings#lists-merge-instead-of-overriding), [Model fallback](https://code.claude.com/docs/en/model-config#automatic-model-fallback), [Headless mode](https://code.claude.com/docs/en/headless)

Helper selection also follows [Anthropic's guidance on folding subagents into the main loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop): separate context and independent verification justify delegation; repeated shared-context handoffs usually belong in the main loop. Agent Collab retains its independent opposite-product peer. No quality, cost, or latency improvement is claimed without comparative evaluation.

## Settings audit

The original settings audit was completed on 2026-07-26; all settings and launch-control claims were reassessed on 2026-10-21 with Codex CLI `0.163.0` (including its source) and Claude Code `2.1.305`. A focused 2026-10-25 refresh checked helper, prompt, and skill contracts against official documentation retrieved that day, Codex 0.164.0 source, and Claude Code 2.1.310 validation, correcting V1/V2 depth, spawn-field exposure, and `availableModels` wording. Provider documentation defines what each control does; defaults remain Agent Collab product decisions. The helper count, deadline, and high-effort defaults have not been established as cost or latency optima.

### Persisted Agent Collab settings

| Setting | Default | Provider mapping and justification |
| --- | --- | --- |
| `local_subagents_allowed` | `true` | Enables bounded independent lenses when they materially improve coverage. Claude uses an `Agent` pre-tool hook; Codex uses documented multi-agent controls. Turning it off directly denies Claude `Agent` and sets Codex `agents.enabled=false` and `features.multi_agent_v2=false`. |
| `max_local_subagents` | `8` | Finite per-run helper bound. For Claude this is a runtime-enforced total `Agent`-call count plus a documented one-layer spawn-depth limit. For Codex it maps to `agents.max_concurrent_threads_per_session=8`; the total-call bound is instructional. V1 nesting is configured with `agents.max_depth=1`; V2 nesting remains instructional. Eight is a product-selected ceiling, not a measured optimum. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| `adjudicator_enabled` | `true` | Internal Agent Collab control, not a provider setting. It requests a separate host-local advisory challenge after the host-first and peer reports. This independence is a host procedure; the runtime validates the artifact and rebinding but cannot prove the adjudicator's process history. It replaces the removed `profile`, whose only runtime effect was adjudication. |
| `agent_timeout_seconds` | `2700` | Internal absolute wall-clock deadline, persisted as a JSON number and constrained to 60–86400 seconds. Forty-five minutes is the project-selected budget, not a guarantee that every review will finish. This is the mandatory run bound; it is not a model turn count. |
| `safe_mode` | `false` | Deliberate full-capability product default for deep repository investigation. Codex maps to `danger-full-access`; Claude maps to `bypassPermissions`. This is not the generally safest provider posture: OpenAI says to use caution with full access, and Anthropic says bypass mode belongs only in isolated containers, VMs, or dev containers without internet access. Operators should enable safe mode when that risk is unacceptable. [OpenAI approvals and sandboxing](https://learn.chatgpt.com/docs/agent-approvals-security), [Anthropic bypassPermissions warning](https://code.claude.com/docs/en/permission-modes#skip-all-checks-with-bypasspermissions-mode) |
| `workspace_mode` | Linux: `isolated`; macOS and Windows: `direct` | Linux uses copy-on-write HOME and repository overlays with full provider tools inside, after a successful `fuse-overlayfs` and `bwrap` write/discard preflight. macOS and Windows use direct host filesystem access and bounded persisted mutation snapshots by default; snapshots do not prevent edits and still read included file contents. Explicit settings and per-run choices are preserved. `setup` writes every resolved value, including this platform default, so a settings file copied from Linux selects `isolated` explicitly on macOS or Windows and fails there; keep settings files per platform, or run `setup --workspace-mode direct` on that platform. Safe mode resolves to `direct` unless a conflicting isolated selection is explicitly requested. |
| `codex_model` | `gpt-6-astra` | Explicit GPT-6 Astra default for independent peer work. Availability still requires the exact ID in the refreshed Codex catalog. [OpenAI GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md) |
| `codex_effort` | `xhigh` | Project-selected default. Preserve per-run overrides and validate each pair against the live model entry; this is not a universal provider recommendation. |
| `online_research` | `true` | Maps only the provider research tools Agent Collab configures: Codex `web_search=live`, and Claude Code built-in `WebSearch`/`WebFetch` availability. Current-source verification is central to the research use case. This setting does not restrict network access through shells, MCP, plugins (including Codex's bundled browser plugins), or other provider facilities. [OpenAI web-search configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [Anthropic CLI reference](https://code.claude.com/docs/en/cli-reference) |
| `codex_config` | `[]` | Optional additive Codex `-c key=value` entries, a documented CLI surface. Empty by default to minimize hidden policy. Agent Collab rejects every entry in, or a parent table of, the namespaces it owns: model and effort selection (`model`, `model_reasoning_effort`, `plan_mode_reasoning_effort`, `review_model`, `service_tier`, `daybreak`), provider routing and auth (`model_provider`, `model_providers`, `openai_base_url`, `chatgpt_base_url`, `oss_provider`, `cli_auth_credentials_store`, `forced_login_method`, `forced_chatgpt_workspace_id`, `responses_api_metadata`, `apps_mcp_product_sku`, `experimental_realtime_webrtc_call_base_url`, `experimental_realtime_ws_base_url`, `experimental_realtime_ws_model`), the catalog (`model_catalog_json`), approval (`approval_policy`, `approvals_reviewer`, `auto_review`), sandboxing (`sandbox_mode`, `sandbox_workspace_write`, `default_permissions`, `permissions`), research and tools (`web_search`, `tools`), memory (`memories`), helpers (`agents`, `features`), and profiles. The immutable request therefore cannot disagree with the actual launch or its attestation. [OpenAI Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-exec) |
| `claude_model` | `claude-opus-5-5` | Fixed Opus 5.5 ID; the live probe must observe this exact primary model. Fable remains explicit-only. [Anthropic Opus 5.5 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5) |
| `claude_effort` | `xhigh` | Project-selected default, verified through Stop-hook telemetry. Explicit `max` remains supported when attested. `ultracode` provider support can be probed, but bounded peer execution rejects that orchestration mode. [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort) |
| `claude_tools` | `default` | Keeps Claude's normal built-in tool surface in full-capability mode so the peer can inspect, test, and research. `default` is an exclusive sentinel rather than a list member. Online-research, zero-helper, and conversation-fork controls are applied separately with `--disallowedTools`. MCP tools and provider-managed policy are outside this selector. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-reference) |
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
| `--claude-max-turns N` | omitted / `null` | Anthropic documents no turn limit by default. The flag is retained only as an explicit per-run work-count budget; it is never persisted as a default or inferred. [Anthropic CLI reference](https://code.claude.com/docs/en/cli-reference) |

### Provider launch audit

| Provider control | Resolved value | Why Agent Collab uses it |
| --- | --- | --- |
| Codex `exec` | non-interactive | Official automation surface for a one-shot peer run. |
| Codex `--strict-config` | enabled | Fails on unrecognized current configuration rather than silently ignoring an enforcement key. |
| Codex `--ephemeral` | enabled | Prevents Codex session rollout persistence; Agent Collab owns the auditable run artifacts. |
| Codex `--json` | enabled | Produces bounded JSONL lifecycle telemetry retained in `peer-stdout.txt`, including usage when emitted. The required final report is read only from `--output-last-message`; telemetry is never a report fallback. |
| Codex `--cd` | exact repository root | Makes repository scope explicit and avoids launch-directory ambiguity. |
| Codex `--output-schema` and `--output-last-message` | provider-compatible peer schema and run artifact | Official structured-output path for stable downstream validation; stdout events are never substituted for the final report. The runtime applies the richer canonical schema and request relations afterward. [OpenAI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) |
| Codex `approval_policy="never"`, `approvals_reviewer="user"` | always | A background peer cannot answer interactive approval prompts. `codex exec` ignores the root `--ask-for-approval` flag and rebuilds its approval policy from config when the user's reviewer is `auto_review`; pinning both keys keeps the peer headless with no auto-reviewer. Safe mode combines this with read-only sandboxing; full mode is intentionally autonomous and carries the documented risk. |
| Codex `features.memories=false`, `memories.use_memories=false`, `memories.generate_memories=false` | always | Disables automatic memory injection, dedicated memory tools, and memory-generation inputs. The subsystem controls remain effective if an inherited provider feature alias re-enables the feature. `--ephemeral` separately prevents session persistence and startup memory generation. Full-mode shell access can still read files; the brief forbids consulting saved memories. [OpenAI configuration](https://learn.chatgpt.com/docs/config-file/config-reference) |
| Codex `features.agent_message_board=false` | always | Prevents inherited local or remote board configuration from supplying cross-session context, including an external receiver that can run in ephemeral mode. Native local-helper messaging remains available. Managed provider policy remains authoritative. [Codex board gate](https://github.com/openai/codex/blob/rust-v0.164.0/codex-rs/core/src/agent_message_board.rs) |
| Codex `--sandbox` | `danger-full-access` or `read-only` | Full mode supports deep investigation; safe mode is a no-write posture and on Linux first requires a working `bwrap` preflight. |
| Codex `--model`, `model_reasoning_effort` | exact attested pair | Prevents implicit model/effort selection and makes the request auditable. |
| Codex `web_search` | `live` or `disabled` | Makes current research intent explicit instead of relying on the full-access default. |
| Codex `agents.enabled`, `agents.max_concurrent_threads_per_session`, `agents.max_depth`, `features.multi_agent_v2.*` | derived helper policy | Uses the current multi-agent keys. `features.multi_agent_v2` outranks the `agents` keys, so it is pinned too: disabled with helpers, otherwise capped at N+1 threads (the root counts) with model and effort override fields hidden from the spawn tool schema. Hiding fields does not make supplied values universally invalid at runtime; the brief forbids conflicting overrides on either version. Those V2 controls apply to models whose catalog entry selects V2; V1 models keep per-spawn overrides and nest at most `agents.max_depth=1`, a key in Codex's published config schema. Agent Collab does not claim a provider-enforced total-call or per-helper runtime limit, or a nesting-depth limit under V2. |
| Codex `agents.default_subagent_model`, `agents.default_subagent_reasoning_effort` | exact requested peer pair | Overrides ambient helper defaults. Custom agent definitions and explicit spawn arguments can still override model or effort, so the brief forbids conflicting agents; this is not a universal helper-selection guarantee. [OpenAI subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) |
| Claude `-p` | non-interactive print mode | Required for structured one-shot automation and the CLI's budget/turn controls. |
| Claude `--model`, `--effort` | exact attested pair | Overrides implicit selection for the session. Agent Collab pins the effective effort in process and command-line settings environments and checks observed model usage afterward. Managed policy remains authoritative. The actual peer must produce valid Stop-hook effort evidence; missing, invalid, or conflicting observations fail with `effort_unverified`. |
| Claude `--permission-mode` | `bypassPermissions` or `plan` | Full mode preserves the configured powerful tool posture; safe mode uses provider-native plan permissions. `plan` is not an OS container and can permit classifier-approved commands. |
| Claude `--no-session-persistence` | enabled | Prevents the peer transcript from becoming a resumable Claude session; Agent Collab keeps its own bounded evidence. |
| Claude inline `--settings` | exact model controls, resolved `env` pins, inbound refusal requested subject to managed policy, finite fail-closed `Agent` hook | Narrows model selection, disables configured fallback and flagged-message model switching, and enforces the total Agent-call bound with command-line-priority settings. Hook execution failures and timeouts block the call (`onFailure: block`, requiring 2.1.295+). Resolved helper pins are also supplied in settings `env` because ordinary settings can overwrite inherited process variables. Managed policy remains authoritative; because non-managed arrays can merge, live and post-run exact-model checks are still required. [Anthropic settings](https://code.claude.com/docs/en/settings), [hook failure policy](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails), [environment precedence](https://code.claude.com/docs/en/env-vars#precedence) |
| Claude `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` | exact peer model, `1` | Forces the helper model instead of relying on inheritance or the environment default alone. Force support requires Claude Code 2.1.257+. [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| Claude `CLAUDE_CODE_EFFORT_LEVEL` | exact effective effort | Overrides helper frontmatter and invocation effort. Provider caps can still apply and must pass availability attestation plus actual-run Stop verification. Explicit `ultracode` execution is blocked by the finite-helper policy. [Anthropic model configuration](https://code.claude.com/docs/en/model-config) |
| Claude `CLAUDE_CODE_FORK_SUBAGENT`, `--disallowedTools Agent(fork)` | `0`, denied | Prevents copying the parent conversation into independent helper contexts; fork mode is on by default, including in print mode since Claude Code 2.1.302. [Anthropic subagents](https://code.claude.com/docs/en/sub-agents) |
| Claude `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | `1` | Uses the documented depth control to prevent helpers from recursively spawning another helper layer. The total `Agent`-call hook and absolute peer deadline remain independent bounds. [Anthropic subagents](https://code.claude.com/docs/en/sub-agents#let-subagents-spawn-their-own-subagents) |
| Claude `autoMemoryEnabled=false`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | disabled | Keeps prior auto-memory notes out of the independent peer; the environment variable takes precedence for the session. [Anthropic memory](https://code.claude.com/docs/en/memory) |
| Claude `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` | peer deadline in ms | Print mode otherwise stops waiting for background helpers after ten idle minutes and drops their results; the absolute peer deadline remains the liveness bound. [Anthropic environment variables](https://code.claude.com/docs/en/env-vars) |
| Claude Stop hook | bounded `claude-effort.json` evidence | Records observed main-peer effort; missing evidence or any mismatch rejects the result, even if later observations match. Private pending markers make capture failures and interruptions after bookkeeping begins visible to the wrapper. Failures before the capture executable starts remain outside that observation boundary. `onFailure` does not apply to Stop hooks. Receipts, markers, and locks use `0600` modes on POSIX. Both Claude hooks use exec form (`command` plus `args`), so no shell parses them on any platform. [Hook effort fields](https://code.claude.com/docs/en/hooks#common-input-fields) |
| Claude `disableWorkflows`, `CLAUDE_CODE_DISABLE_WORKFLOWS`, `--disallowedTools Workflow` | disabled, `1`, denied | Prevents Workflow agents from bypassing the Agent-call counter. Explicit `ultracode` fails before launch; other full-mode capabilities retain their configured policy. |
| Claude `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | `0` | Keeps agent teams, whose teammates are separate sessions outside the `Agent`-call counter, off for the peer. [Anthropic environment variables](https://code.claude.com/docs/en/env-vars) |
| Claude `--json-schema`, `--output-format json` | provider-compatible peer schema | Uses Claude's official structured-output envelope; the richer canonical schema and request relations are checked after extraction, and bare or cross-provider shapes are rejected. [Anthropic structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs) |
| Claude `--tools`, `--disallowedTools` | resolved built-ins | Starts from the configured built-in surface, then removes web tools when research is off and directly denies `Agent` when helpers are disabled. It does not claim control of MCP or plugin tools. |
| Claude `--max-turns` | omitted unless explicitly requested | Avoids truncating valid tool-heavy work. If opted in, a reached cap is a distinct structured failure. |

Availability has a narrower contract than a peer run. Codex resolves `codex debug models` and accepts only the exact advertised pair. The Claude probe runs outside the repository with filesystem setting sources (an empty `--setting-sources`, the CLI form of the SDK's `settingSources: []`), built-in tools, and MCP discovery disabled, uses one turn because the only task is to return `AVAILABLE`, and requires both exact primary-model `modelUsage` and Stop-hook effort telemetry. That one-turn probe cap does not apply to the real peer run.

The runtime neutralizes inherited thinking overrides in the process environment and supplies command-line environment pins. Boolean `0` values are documented; the empty `MAX_THINKING_TOKENS` pin is not independently specified by current provider documentation. Exact effort is checked through Stop telemetry, but this does not prove every internal thinking-budget behavior. [Claude environment variables](https://code.claude.com/docs/en/env-vars)

### Deliberately not enabled

- No default Claude turn or dollar budget is layered over the finite deadline. Either would be a second truncation policy; a turn cap remains explicit-only, and a future monetary cap would also need an explicit product contract.
- Normal peers do not use Claude `--bare`, `--setting-sources`, `--strict-mcp-config`, or troubleshooting `--safe-mode`, nor Codex `--ignore-user-config` or `--ignore-rules`. The full-capability product posture intentionally retains relevant repository guidance, configured tools, plugins, and provider policy. Only the narrow availability probe reduces configuration sources. In either full or safe mode, a non-bare Claude `-p` peer can run project hooks and connect project MCP servers without a workspace-trust prompt. Plan permissions do not prevent that configuration loading. Codex peers also retain configured hooks and MCP. Review repository configuration before using it with an untrusted project. [Claude headless behavior](https://code.claude.com/docs/en/headless), [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- Agent Collab does not append or replace provider system prompts. The compact user brief carries the task contract without claiming higher precedence than repository or managed instructions.
- Fallback chains remain empty and exact-model availability is fail-closed. A fallback would make the persisted request disagree with the model that actually ran.
- Both peer defaults use `xhigh` as a project choice. Persisted settings and explicit per-run overrides retain their precedence; upgrading the package does not rewrite existing settings. Effort changes are never silently substituted.

## Configuration

### Cache, performance, and cost

Independent reviews keep fresh contexts. A normal Claude helper does not read its parent's conversation cache on its first request; a fork does because it copies that conversation. Resuming a helper can reuse its own warm cache. Main `-p` turns default to one-hour caching within subscription included usage, or five minutes with API/usage billing; subagents normally get five minutes. Longer TTLs cost more to write, so Agent Collab leaves them provider-managed. Opus 5.5 effort changes preserve cache on supported direct API/subscription paths, with provider and policy exceptions. Plugin skills and agents are appended without invalidating the existing prefix. [Claude caching](https://code.claude.com/docs/en/prompt-caching)

Shared outcome and evidence text precedes run identity and task text to improve the opportunity for matching prefixes. Actual hits depend on provider prefixes, minimum lengths, routing, and expiry. OpenAI API caching controls are not automatically Codex CLI controls, and no undocumented cache option is injected. [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)

Use the fewest independent helpers needed and reuse them for follow-ups. Exact requested models and efforts remain fixed. Compare representative tasks before claiming savings: include completion quality, elapsed time, input/output and cached tokens, helpers, adjudication, and the fresh availability probe. `peer-stdout.txt` retains bounded Codex telemetry and Claude's `peer.raw.json` retains its result envelope; neither is a complete billing ledger. Probe charges and host/adjudicator usage are not aggregated. Reported provider cost estimates are not invoices. [Claude cost tracking](https://code.claude.com/docs/en/costs)

### Repository instructions

`AGENTS.md` remains the shared contract and `CLAUDE.md` contains only `@AGENTS.md`. Every supported Claude Code version (2.1.295 and newer) can read `AGENTS.md` directly; the import is kept because Anthropic documents it as never loading the file twice and as the way to keep `AGENTS.md` available in sessions where direct support is unavailable, such as when the built-in AGENTS.md plugin is disabled. The repository guidance fits Codex's default 32 KiB combined project-instruction budget and Claude's 200-line target; parent or global instructions can consume the rest. Imports do not reduce token cost. [Claude memory](https://code.claude.com/docs/en/memory#remove-an-earlier-agents-md-workaround), [Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

### Setup and overrides

The commands below run the copy in a repository checkout. An installed plugin runs the same commands from its own skill directory (`<skill-root>/scripts/host.py`); the host agent resolves that path from the skill.

Interactive setup:

```bash
python3 tools/agent-collab/scripts/host.py setup
```

One-run overrides:

```bash
python3 tools/agent-collab/scripts/host.py start \
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

Boolean keys accept `1/0`, `true/false`, `yes/no`, or `on/off`; an empty value is rejected rather than treated as false. Integer keys accept ASCII digits only.

Run `doctor` to check versions, authentication, schemas, paths, resolved defaults, and the selected workspace backend:

```bash
python3 tools/agent-collab/scripts/host.py doctor
```

`doctor` strictly parses every bundled schema and meta-validates it when Python `jsonschema` is installed; its `meta_validator` field distinguishes those checks. Release verification requires full schema meta-validation. Because 2.0 has no migration layer, an invalid legacy settings or state file must be archived or removed before retrying. `setup --reset local|global|all` removes Agent Collab settings files (`--dry-run` previews); archive a file first if you need it, then rerun `setup` to create canonical settings. For legacy run state, archive the old run root and start with a fresh default root or explicit `--run-root`. Do not edit installed caches or ask the runtime to coerce old artifacts.

After the host has synthesized a run:

```bash
python3 tools/agent-collab/scripts/host.py complete RUN_ID
```

### Storage locations

| Data | Location |
| --- | --- |
| Global settings | `$AGENT_COLLAB_HOME/settings.json`, else `${XDG_CONFIG_HOME:-~/.config}/agent-collab/settings.json` |
| Local settings and run history (installed plugin) | `<state root>/repos/<repo-name>-<hash>/settings.local.json` and `.../runs/` |
| State root | `$AGENT_COLLAB_STATE_HOME`; else `$CODEX_HOME/agent-collab` when the runtime is installed under `CODEX_HOME`; else `${XDG_STATE_HOME:-~/.local/state}/agent-collab` |
| Local settings and run history (this repository's own checkout) | `tools/agent-collab/settings.local.json` and `tools/agent-collab/runs/`, only when the target repository is the Agent Collab checkout; other repositories use the state root |
| Worktree guard | `<git dir>/agent-collab/active-v2.json`; non-Git folders use `${XDG_STATE_HOME:-~/.local/state}/agent-collab/workspace-guards/` |

On Windows, `~` is `%USERPROFILE%`. Local settings written by `setup` from a checkout therefore do not affect an installed plugin, and a Codex host and a Claude host keep separate run histories for the same repository; the active run is still found by ID through the shared worktree guard. Agent Collab ignores an inherited `CLAUDE_PLUGIN_DATA`: Claude Code never exports it to Bash-tool commands, so any value present belongs to another plugin, whose data directory Claude Code deletes on uninstall. Versions before 5.0.0 could store state under such a directory; archive it if you need that history.

## Repository layout

```text
.agents/plugins/marketplace.json                  Codex repo marketplace
.claude-plugin/marketplace.json                   Claude Code marketplace
.github/workflows/ci.yml                          CI: release checks in --ci mode (Linux, macOS) and Windows runtime checks; Python 3.11, 3.15
plugins/agent-collab/                             Dual-host package; skill and agents are synchronized copies, manifests are edited here
tools/agent-collab/                               Canonical skill, runtime, schemas, references, and host-agent assets
tools/agent-collab/agents/                        Canonical Codex metadata and Claude helper agents
scripts/sync-packages.sh                          Package synchronization and byte/mode parity check
scripts/release-check.sh                          Every release verification step
scripts/install-linux-isolation-deps.sh           Linux isolated-workspace dependencies
tests/test_agent_collab_runtime.py                Runtime and package tests, plus shared fixtures
tests/test_agent_collab_safety.py                 Process, recursion, snapshot, and strict-JSON safety tests
tests/test_agent_collab_hardening.py              Isolation boundary, Codex policy, snapshot, schema-parity, and lifecycle hardening tests
tests/test_agent_collab_lifecycle_audit.py        Lifecycle and guard transition tests
tests/test_agent_collab_selection_audit.py        Model/effort selection evidence tests
tests/test_agent_collab_final_audit.py            Helper counters and Fable admission tests
tests/test_agent_collab_probe_interrupt.py        Availability probe interruption and cleanup tests
tests/test_agent_collab_windows.py                Windows launch, resolution, gate, and Job Object dispatch logic (any host)
tests/windows_runtime_checks.py                   Windows-only Job Object, gate, lock, and doctor checks (run by Windows CI)
AGENTS.md                                         Repository maintenance contract
VERSION                                           Release version
CHANGELOG.md                                      Release history
```

Edit shared skill and runtime resources only under `tools/agent-collab/`, then synchronize the dual-host package:

```bash
scripts/sync-packages.sh
scripts/sync-packages.sh --check
```

## Verify

Run every release check with one command. Release mode fails if any step, including the official validators and isolated installations, cannot run; `--ci` reports steps that need provider CLIs as skipped and tolerates skipped unit tests. Both scripts and `sync-packages.sh` use the interpreter named by `PYTHON` (default `python3`); set `PYTHON=python` on Windows:

```bash
scripts/release-check.sh
scripts/release-check.sh --ci
```

It runs, in order:

```bash
python3 -m unittest discover -s tests -v
scripts/sync-packages.sh --check
python3 -m py_compile tools/agent-collab/scripts/*.py
# strict parse of every tracked JSON file and meta-validation of every schema (needs jsonschema)
# VERSION, both manifests, and the CHANGELOG entry agree
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" tools/agent-collab
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" plugins/agent-collab/skills/agent-collab
claude plugin validate . --strict
claude plugin validate plugins/agent-collab --strict
# isolated installs: CLAUDE_CONFIG_DIR=<tmp> with --scope user, and CODEX_HOME=<tmp>
```

On Windows, the discovery suite's POSIX assertions do not apply; run the Windows runtime checks instead, as CI does:

```bash
py -3 tests/windows_runtime_checks.py -v
```

Current Plugin Creator guidance keeps this existing `.codex-plugin/plugin.json` format supported as a compatibility fallback; it recommends a root `plugin.json` for new portable packages, which does not require migrating this dual-host package. Codex verification covers strict package JSON, schema and version consistency, official skill validation, and isolated marketplace installation. Test representative requests in a fresh conversation after installation. Public-directory publication additionally requires the submission portal's automated checks. This package has no license declaration or icon: OpenAI requires listing icons before submission and lists the license field as optional; Anthropic's published checklist requires a license but does not list an icon as a blocking requirement. The missing icons mean this package does not meet OpenAI's documented Codex distribution metadata requirements. Local marketplace installation remains supported; passing local validators does not establish directory acceptance. [OpenAI package formats](https://developers.openai.com/plugins/build/plugins), [OpenAI local plugin testing](https://developers.openai.com/plugins/deploy/connect-chatgpt), [OpenAI submission checks](https://developers.openai.com/plugins/deploy/submission), [Anthropic submission checks](https://claude.com/docs/plugins/pre-submission-checklist)

### Tagged releases

GitHub `main` is an installable marketplace source. Any push that changes `plugins/agent-collab/` must carry a new Semantic Versioning release; do not publish different package contents under the same manifest version. Use a major bump for incompatible settings or runtime contracts, a minor bump for backward-compatible functionality, and a patch bump for compatible fixes. The 3.0.0 source was on `main` without a tag or GitHub Release. Formal annotated tags start at `v4.0.0`; do not backfill `v3.0.0` without release evidence.

For each release, update `VERSION`, both plugin manifests, and the root `CHANGELOG.md`, then synchronize the package and run `scripts/release-check.sh`. Commit the verified release state on `main` before creating a new annotated `v<version>` tag on that exact commit. Confirm that the remote branch has not changed, push the branch and tag, and verify both remote refs point to the intended commit. Publish a GitHub Release from the existing tag, with notes drawn from the matching root changelog entry. Never move or reuse a release tag. A version tag identifies an exact package; marketplace sources that track `main` still follow the branch, while sources pinned to a tag remain fixed until users choose a newer tag. [Semantic Versioning](https://semver.org/spec/v2.0.0.html), [Codex Git refs](https://developers.openai.com/plugins/build/plugins), [Claude marketplace versions and refs](https://code.claude.com/docs/en/plugins/host-marketplace), [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

Use the same version and commit for the tag, manifests, changelog entry, and GitHub Release. `gh release create` must use `--verify-tag` so it cannot silently create a tag at another commit. Check the release URL and tag target after publication. The root changelog is the maintained history; GitHub Release notes are a copy of that version's entry, not a separate source of truth. [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)

### Evaluation evidence

The broader 2026-10-25 provider audit covered launch configuration, models and effort, helpers, permissions, hooks, memory, research, output, prompting, skills, caching, packaging, installation, and validation. It combined an independent host pass, an OpenAI-focused helper, an opposite-product Claude review, and fresh advisory adjudication. Adopted findings fixed Codex memory-alias precedence, remote message-board ingress, npm sandbox initialization, and explicit fresh-context helper selection. The [coverage record](tools/agent-collab/references/provider-audit.md) links the sources and separates product choices from defects.

The 2026-10-25 guidance refresh checked current Codex and Claude prompting, subagent, skill, and model-selection guidance, including Anthropic's main-loop article. Independent Claude `claude-opus-5-5`/`xhigh` reviews ran before and after the first implementation checkpoint. The follow-up review confirmed the V1/V2 source claims and preserved workflow, and identified a missing instruction to pass the delegation ban to fresh-context helpers; that instruction and its prompt-contract checks were added. The entry point now routes to required run-selection and artifact guidance, with detailed runtime/failure sections consulted when relevant. This is a maintenance change, not evidence of improved model performance.

After the comprehensive audit changes, all 228 tests passed without skips on Python 3.11.17 and 3.15.1. `scripts/release-check.sh` passed in release mode using Codex CLI 0.164.0 and Claude Code 2.1.310, including both official validators and isolated marketplace installations. The local Codex skill validator matched the file shipped in the 0.164.0 source. The live comprehensive-audit peer returned a valid schema 2.0 report; API usage contained the requested Opus model and allowed auxiliary Haiku, its Stop receipt reported `xhigh`, and the disposable workspace cleanup completed. Tests inside the peer had two environmental skips; the independent host checks above covered those checks without skips.

The 2026-10-22 review checked settings, instructions, and every documentation file against current Claude Code and Codex documentation, the Codex 0.163.0 source, and the implementation, through three independent lenses: Claude Code conformance, Codex conformance, and documentation against code. It found no wrong or deprecated provider setting, flag, hook field, or manifest field; the host re-verified each adopted finding, and 6.0.0 fixes them with regression tests. The Codex helper overrides, including `agents.max_depth`, were accepted by `codex exec --strict-config` against an unauthenticated `CODEX_HOME`, so no model ran. `scripts/release-check.sh` passed in release mode with Codex CLI 0.163.0 and Claude Code 2.1.306, and the unit suite passed on Python 3.11, 3.13, 3.14, and 3.15.

The 2026-10-21 audit reviewed the runtime, schemas, tests, packaging, and documentation against current Claude Code documentation, Codex documentation, and the Codex 0.163.0 source. It combined an independent Agent Collab Codex peer (`gpt-6-astra`/`xhigh`, isolated workspace), four host-local lenses, and advisory adjudication; every material claim was re-verified by the host. Confirmed findings were fixed in 5.0.0 with regression tests, including a real fuse-overlayfs and bwrap integration test of the isolated boundary. `scripts/release-check.sh` then passed in release mode: the full unit suite, package byte and mode parity, compilation, strict parsing of every tracked JSON file, schema meta-validation, version consistency, both official validators, and isolated installations on both hosts. Codex launch overrides were checked against `codex exec --strict-config` without starting a session.

The latest results are scoped to this Linux checkout with the versions recorded above. CI is configured for Linux, macOS, and Windows with Python 3.11 and 3.15; its first macOS run found the zombie EPERM defect fixed in 5.0.1. The 2026-10-25 work exercised Codex-hosted Claude review, but did not rerun macOS or native Windows CI, a live Windows peer, live safe-mode approval behavior, or comparative quality, latency, and cost measurements. Fresh availability probes remain mandatory for every run, since account and model access is not a durable guarantee. Earlier evaluations are recorded in [CHANGELOG.md](CHANGELOG.md).

The Codex and Claude manifests and root `VERSION` must always contain the same Semantic Versioning 2.0.0 version. Record user-visible and breaking changes in the root `CHANGELOG.md`; do not put changelogs inside skills. Version 2.0.0 intentionally rejects 1.x settings and run artifacts rather than translating them or probing compatibility fallbacks.
