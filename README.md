# Agent Collab

Agent Collab is a peer-first, challenge-first collaboration plugin for Codex and Claude Code. It launches the opposite product with a neutral brief, keeps the host analysis independent, then validates and combines the evidence.

Version 6.0.1 fixes Codex memory and message-board isolation, improves Linux safe-mode initialization, refreshes peer and helper guidance, and adds MIT licensing and plugin branding. It retains the Python 3.11 minimum and ASCII decimal numeric flags introduced in 6.0.0. See [CHANGELOG.md](CHANGELOG.md) for changes and upgrade steps. Strict schema 2.0 remains in force, and 1.x settings and run artifacts still have no compatibility layer.

**Latest provider-documentation and workflow evaluation: 2026-10-25.** A comprehensive audit checked the provider contracts used by this project against current OpenAI and Anthropic documentation, Codex 0.164.0 source, and independent Agent Collab Claude reviews. See the [source and coverage record](tools/agent-collab/references/provider-audit.md) and [evaluation evidence](#evaluation-evidence) for scope and limitations. Version 3.0.0 appeared on GitHub `main` without a release tag; formal tags begin with `v4.0.0`.

## Defaults

| Peer | Default model | Effort |
| --- | --- | --- |
| Codex | `gpt-6-astra` | `xhigh` |
| Claude Code | `claude-opus-5-5` | `xhigh` |

These are the project defaults requested for Agent Collab. Existing persisted settings and per-run overrides retain precedence; installing this version does not rewrite them. Every requested pair still requires fresh availability evidence, with no fallback.

The user can select a model and reasoning or effort level in natural language. The host translates an explicit selection into the per-run flags `--peer-model` and `--peer-effort`, then requires a fresh availability attestation for that exact pair before launch.

Use `--peer-model` and `--peer-effort` for an explicit per-run choice. Examples include `claude-opus-5-5`/`xhigh`, `gpt-6-astra`/`high`, and explicit-only `claude-fable-5`/`max` (“Fable 5 Max”). For an explicit Fable 5.1 request, use `--peer-model claude-fable-5-1` and the requested `--peer-effort`; “Fable 5 Max” retains its original `claude-fable-5`/`max` meaning. Fable cannot be a built-in, environment, local, or global default. Every choice needs fresh exact-pair attestation. Never substitute a fallback. [Fable 5.1 model ID](https://platform.claude.com/docs/en/models/fable-5-1/overview)

Codex effort support comes from the exact model in the catalog resolved by `codex debug models`; it is not hardcoded here. Claude accepts `low|medium|high|xhigh|max|ultracode`, but bounded Agent Collab execution rejects `ultracode` before creating artifacts because Workflow orchestration bypasses the Agent-call counter. Fresh exact-pair availability is required before any peer, helper, host pass, or adjudicator begins. See [run selection](tools/agent-collab/references/launch.md#resolve-the-run) for probes, aliases, precedence, and failure handling.

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
- [Anthropic: Claude Fable 5.1 model](https://platform.claude.com/docs/en/models/fable-5-1/overview)
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

## Workflow and evidence

Use `start` with a neutral brief before host analysis, then follow the returned `next_action` through `advance`. Each action includes executable argv, the input path, and the applicable semantic template. `host_policy` exposes the resolved helper, research, authorization, workspace, and deadline settings. The runtime validates prerequisites before launch and directs the remaining stages from admitted artifacts:

- `first_pass`: save independent host judgments before reading peer output; submit with `--first-pass`.
- `waiting`: each guided call waits up to 60 seconds by default, then returns a successful response requesting continuation. Resume after a host tool interruption too; no continuation extends the finite peer deadline.
- `adjudicator`: when enabled and available, obtain a fresh advisory report and submit with `--adjudicator`. Disabled or exhausted local helpers route directly to synthesis. An actual advisory report consumes one slot in the combined host helper-report bound. An `advisory_pending` marker always means no report was supplied; disclose unavailable adjudication in synthesis. Failed peers remain eligible for advisory review of their evidence.
- `synthesis`: read the returned `evidence_file` completely, verify claims, edit `synthesis_input`, and submit with `--synthesis` immediately before answering.

`advance RUN` resumes the current stage without inferring submission from edited files. A response with `next_action: null` follows validated closure. The usual path uses four CLI calls with adjudication and three without, including `start`, plus reading the evidence files; waiting or new evidence can require more. The interface has two command names, not a promise of two interactions. Control JSON stays compact and puts `next_action` first, so large reviews do not bury the action in an escaped JSON string.

The normal workflow is self-contained in [SKILL.md](tools/agent-collab/SKILL.md). The [synthesis contract](tools/agent-collab/references/synthesize.md) documents low-level operations and evidence details; the [launch policy](tools/agent-collab/references/launch.md) covers overrides and failures. Read these references only when applicable. [AGENTS.md](AGENTS.md) owns contributor invariants.

Stored settings and artifacts use strict schema 2.0: no legacy translation, coercion, duplicate JSON keys, non-finite numbers, or silently ignored fields. Runtime-owned files, locks, and newly created storage parents are private on POSIX; Windows inherits directory ACLs. Provider output is normalized only from the expected product envelope and then independently validated. Host judgments require explicit CLI admission, the first admitted pass is preserved, and the saved request is checked against its launch-time digest. Hashes bind evidence freshness; they do not prove a conclusion or that the host read it.

Every peer has a finite absolute deadline, 2700 seconds by default. Claude has no default turn cap; `--claude-max-turns N` is an explicit override and a reached turn cap is distinct from timeout. Do not replace a live peer before its deadline unless the user stops the run. Cancellation preserves immutable cancellation evidence rather than overwriting the peer's completed work.

## Runtime organization

The host command adapter coordinates launch, finalization, and cancellation. `advance` reuses the same finalization and closure functions as the low-level `finish` and `complete` commands; it introduces no second lifecycle or persisted state format. `host_config.py` owns settings and prerequisite resolution; `registry.py` owns locked run-instance transitions; `artifacts.py` validates and assembles evidence; `review.py` binds compact judgments to a captured revision. `peer.py` separates preparation, provider execution, output checks, normalization, and mutation verification. Runtime modules use ordinary Python imports and share one module instance; executable entry points establish their trusted sibling directory even with Python safe-path mode, and Windows-only bindings remain loaded on demand. `validation.py` shares exact field, type, enum, and bounded-number checks through Python rule tables; request binding, evidence hashes, authorization, and lifecycle relations remain explicit in their owning modules. It is not a JSON Schema interpreter. Provider waits remain outside the run-operation lock. `peer-report.schema.json` is authoritative; `provider_schema.py` generates the self-contained provider projection during package synchronization and checks it without writing under `--check`. New unsupported constraints require an explicit projection decision.

## Compact host inputs

Hosts supply judgments in small JSON files; the runtime writes canonical envelopes and hashes. `advance RUN --first-pass FILE` admits the first pass, waits, captures a revision, and saves its readable `evidence.md` under the same operation lock. The receipt binds both the source documents and rendered bytes; tampered displays fail verification. Optional `--helpers FILE` admits actual helper reports. `advance RUN --adjudicator FILE` updates advisory evidence and returns the new revision. Submit evidence changes separately from `--synthesis FILE`; changed evidence invalidates an old draft without rewriting its judgments. The runtime requests stages and checks artifact bindings; it cannot prove reading history, advisory availability, unreported helper launches, or semantic completeness. Low-level `finish`, `result --format markdown --review DIR`, and `complete` remain available for inspection and tooling; see the [synthesis contract](tools/agent-collab/references/synthesize.md#artifact-shapes).

## Concurrent run ownership

Independent sessions can run Agent Collab in the same worktree. Each registry entry has a random ownership token, so retrying an old cancellation or completion cannot release a newer run that reused its ID. Registry admission reserves space for later lifecycle updates.

Use `host.py status --active --repo-root PATH` to list all active owners, including other run roots. `result` requires an explicit run ID or directory. Startup no longer scans every other launcher's process identity. Use `host.py recover RUN_ID --repo-root PATH` for an abandoned **starting** entry: recovery requires a proven dead launcher, preserves artifacts, and refuses running entries or unknown identity evidence. Finish or cancel established runs normally.

Exact active IDs take precedence over local history and unique prefixes; an explicit run directory selects that directory. Git subdirectories passed through `--repo-root` resolve to the worktree root. Linux process identities include the observing PID namespace: a different or unreadable namespace is unknown, never proof that a launcher exited. Unreaped zombie wrappers are terminal, and repeated `finish` calls preserve normalization failure evidence and the same review revision.

## Permissions and recursion

Full capability and edit authorization are separate. By default, Codex uses `danger-full-access` and Claude uses `bypassPermissions`, while `edit_allowed=false` instructs the peer not to edit. That instruction and mutation detection are not preventive enforcement. Bypass mode does not protect against prompt injection: repository text, tool output, or online sources can induce unintended actions with the peer’s granted capabilities. [Anthropic permission guidance](https://code.claude.com/docs/en/permission-modes#skip-all-checks-with-bypasspermissions-mode) Anthropic recommends `bypassPermissions` only in isolated containers, VMs, or development containers without internet access where host damage is impossible.

Fresh Linux settings use `fuse-overlayfs` and `bwrap` for a disposable view of HOME and the repository. Fresh macOS and Windows settings use direct mode with bounded before/after snapshots. Explicit settings remain authoritative. Durable delegated edits require `--edit-allowed --workspace-mode direct`; unavailable isolation fails closed without a direct fallback.

Neither posture provides complete containment or observation. In isolated mode, overlay writes are discarded, but networking and host-facing sockets under `/run`, including Docker and D-Bus/systemd, remain reachable; effects through them can persist undetected. Direct-mode snapshots detect included changes after the fact, cannot undo them, exclude selected generated/dependency directories, and miss changes reverted between captures. Concurrent sessions can contribute changes; snapshots do not establish attribution.

Use `--safe-mode` for provider-native restrictions. Linux Codex requires a `bwrap` initialization preflight; native Windows Codex safe mode fails closed. Claude uses `plan` permissions, not an OS container. Research controls limit configured provider research tools, not all networking through shells, MCP, or plugins.

Peer depth markers, CLI shadows, and helper instructions deter recursion but are not a hard cross-product boundary. Claude's ordinary Agent calls have a runtime counter; Codex's total calls and V2 nesting remain instructional, while configured concurrency is enforced by the provider. See [runtime boundaries](tools/agent-collab/references/launch.md#runtime-boundaries-and-failures) and the [provider control audit](tools/agent-collab/references/provider-launch.md) for precise platform, helper, and policy limits.

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

The [provider launch reference](tools/agent-collab/references/provider-launch.md) owns the detailed Codex/Claude flags, hook semantics, model and effort evidence, and deliberate exclusions. Consult it when auditing those controls rather than loading its full tables for every run.

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
python3 tools/agent-collab/scripts/host.py complete RUN_ID --synthesis SYNTHESIS_INPUT
```

### Storage locations

| Data | Location |
| --- | --- |
| Global settings | `$AGENT_COLLAB_HOME/settings.json`, else `${XDG_CONFIG_HOME:-~/.config}/agent-collab/settings.json` |
| Local settings and run history (installed plugin) | `<state root>/repos/<repo-name>-<hash>/settings.local.json` and `.../runs/` |
| State root | `$AGENT_COLLAB_STATE_HOME`; else `$CODEX_HOME/agent-collab` when the runtime is installed under `CODEX_HOME`; else `${XDG_STATE_HOME:-~/.local/state}/agent-collab` |
| Local settings and run history (this repository's own checkout) | `tools/agent-collab/settings.local.json` and `tools/agent-collab/runs/`, only when the target repository is the Agent Collab checkout; other repositories use the state root |
| Worktree guard | `<git dir>/agent-collab/active-v2.json`; non-Git folders use `${XDG_STATE_HOME:-~/.local/state}/agent-collab/workspace-guards/` |

On Windows, `~` is `%USERPROFILE%`. Local settings written by `setup` from a checkout therefore do not affect an installed plugin, and a Codex host and a Claude host keep separate run histories for the same repository; an active run is still found by ID through the shared worktree guard registry. Agent Collab ignores an inherited `CLAUDE_PLUGIN_DATA`: Claude Code never exports it to Bash-tool commands, so any value present belongs to another plugin, whose data directory Claude Code deletes on uninstall. Versions before 5.0.0 could store state under such a directory; archive it if you need that history.

## Repository layout

```text
.agents/plugins/marketplace.json                  Codex repo marketplace
.claude-plugin/marketplace.json                   Claude Code marketplace
.github/workflows/ci.yml                          CI: release checks in --ci mode (Linux, macOS) and Windows runtime checks; Python 3.11, 3.15
plugins/agent-collab/                             Dual-host package; payload and metadata assets are synchronized copies, manifests are edited here
tools/agent-collab/                               Canonical skill, runtime, schemas, references, and host-agent assets
tools/agent-collab/agents/                        Canonical Codex metadata and Claude helper agents
tools/agent-collab/assets/                        Canonical SVG logo and composer icon
tools/agent-collab/README.md                      Canonical package overview
tools/agent-collab/evals/                         Canonical native Claude activation and synthetic outcome evaluations
scripts/sync-packages.sh                          Package synchronization and byte/mode parity check
scripts/release-check.sh                          Every release verification step
scripts/ci-provider-checks.sh                     Independent no-auth provider validators and isolated installs
scripts/install-linux-isolation-deps.sh           Linux isolated-workspace dependencies
tests/test_agent_collab_runtime.py                Runtime and package tests, plus shared fixtures
tests/test_agent_collab_safety.py                 Process, recursion, snapshot, and strict-JSON safety tests
tests/test_agent_collab_hardening.py              Isolation boundary, Codex policy, snapshot, schema-parity, and lifecycle hardening tests
tests/test_agent_collab_lifecycle_audit.py        Lifecycle and guard transition tests
tests/test_agent_collab_simplification.py         Concurrent ownership, revision freshness, compact inputs, and schema projection tests
tests/test_agent_collab_selection_audit.py        Model/effort selection evidence tests
tests/test_agent_collab_final_audit.py            Helper counters and Fable admission tests
tests/test_agent_collab_probe_interrupt.py        Availability probe interruption and cleanup tests
tests/test_agent_collab_windows.py                Windows launch, resolution, gate, and Job Object dispatch logic (any host)
tests/windows_runtime_checks.py                   Windows-only Job Object, gate, lock, and doctor checks (run by Windows CI)
AGENTS.md                                         Repository maintenance contract
VERSION                                           Release version
CHANGELOG.md                                      Release history
LICENSE                                           MIT license, also synchronized into the package
```

Edit shared skill and runtime resources only under `tools/agent-collab/`, then synchronize the dual-host package:

```bash
scripts/sync-packages.sh
scripts/sync-packages.sh --check
```

## Verify

Run every release check with one command. Release mode fails if any step, including the official validators and isolated installations, cannot run; `--ci` tolerates platform-specific skipped unit tests; all four provider validation and installation checks remain mandatory in both modes. CI installs the latest stable provider CLIs and validator dependencies. Both scripts and `sync-packages.sh` use the interpreter named by `PYTHON` (default `python3`); set `PYTHON=python` on Windows:

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
scripts/ci-provider-checks.sh codex-skill
scripts/ci-provider-checks.sh claude-validator
scripts/ci-provider-checks.sh claude-install
scripts/ci-provider-checks.sh codex-install
```

The four provider checks are independently runnable and require no model calls or credentials. The Codex skill check fetches the official validator from the exact installed stable CLI release, verifies that the release documents it, and prints its source URL and hash. Both installation checks use temporary provider homes, then verify the installed version and enabled state; Claude explicitly uses user scope. Provider integration checks run here rather than duplicating installation inside unit tests. GitHub Actions are pinned to full commit SHAs and refreshed weekly by Dependabot.

On Windows, the discovery suite's POSIX assertions do not apply; run the Windows runtime checks instead, as CI does:

```bash
py -3 tests/windows_runtime_checks.py -v
```

The package uses portable root `plugin.json` with OpenAI presentation metadata in `extensions.com.openai.interface`, plus the native Claude manifest at `.claude-plugin/plugin.json`. Skills use the fixed `skills/` directory; no legacy Codex manifest is shipped. Codex verification covers strict package JSON, schema and version consistency, official skill validation, and isolated marketplace installation. Test representative requests in a fresh conversation after installation. The package includes a README, the MIT license, and SVG logo and composer icons. Both manifests declare MIT, Codex references both icons, and Claude references the listing logo. The sync check compares these packaged files with their canonical sources. OpenAI requires both icons for Codex distribution and lists the license field as optional; Anthropic's checklist requires a README and license declaration. Public-directory publication additionally requires the submission portal's automated checks; passing local validators does not establish directory acceptance. [OpenAI package formats](https://developers.openai.com/plugins/build/plugins), [OpenAI local plugin testing](https://developers.openai.com/plugins/deploy/connect-chatgpt), [OpenAI submission checks](https://developers.openai.com/plugins/deploy/submission), [Anthropic submission checks](https://claude.com/docs/plugins/pre-submission-checklist)

### Tagged releases

GitHub `main` is an installable marketplace source. Any push that changes `plugins/agent-collab/` must carry a new Semantic Versioning release; do not publish different package contents under the same manifest version. Use a major bump for incompatible settings or runtime contracts, a minor bump for backward-compatible functionality, and a patch bump for compatible fixes. The 3.0.0 source was on `main` without a tag or GitHub Release. Formal annotated tags start at `v4.0.0`; do not backfill `v3.0.0` without release evidence.

For each release, update `VERSION`, both plugin manifests, and the root `CHANGELOG.md`, then synchronize the package and run `scripts/release-check.sh`. Commit the verified release state on `main` before creating a new annotated `v<version>` tag on that exact commit. Confirm that the remote branch has not changed, push the branch and tag, and verify both remote refs point to the intended commit. Publish a GitHub Release from the existing tag, with notes drawn from the matching root changelog entry. Never move or reuse a release tag. A version tag identifies an exact package; marketplace sources that track `main` still follow the branch, while sources pinned to a tag remain fixed until users choose a newer tag. [Semantic Versioning](https://semver.org/spec/v2.0.0.html), [Codex Git refs](https://developers.openai.com/plugins/build/plugins), [Claude marketplace versions and refs](https://code.claude.com/docs/en/plugins/host-marketplace), [GitHub releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

Use the same version and commit for the tag, manifests, changelog entry, and GitHub Release. `gh release create` must use `--verify-tag` so it cannot silently create a tag at another commit. Check the release URL and tag target after publication. The root changelog is the maintained history; GitHub Release notes are a copy of that version's entry, not a separate source of truth. [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)

### Simplification measurements

Against `fa10efd`, the unreleased refactor reduces `host.py` from 4,590 to 2,716 lines, `start` from 360 to 24 lines, and `invoke_peer` from 443 to 63 lines. Synthesis authoring drops from nine fields to four judgment fields; the runtime fills the other five. The canonical and provider peer schemas retain distinct purposes, but the provider file is now generated and its parsed content is unchanged.

On this Linux host with Python 3.13.5, 20 alternating fresh-process `host.py --help` samples per revision measured medians of 72.06 ms before and 73.16 ms after (ranges 63.79–96.91 and 65.50–99.17 ms). These provider-free samples show no meaningful startup speed gain. No provider token, cost, latency, or review-quality improvement is claimed. Native macOS and Windows execution remains to be verified for this change.

The final local suite passes all 249 tests without skips on Python 3.11.18, 3.13.5, and 3.15.2, using Codex CLI 0.165.0 and Claude Code 2.1.312. Package synchronization and all 17 schema meta-validations pass. Registry concurrency is exercised with independent OS processes and lifecycle fixtures; this change did not run two live provider sessions. The native Windows suite was invoked on Linux and skipped all 18 checks, so neither it nor native macOS execution is counted as verified.

### Evaluation evidence

Repeatable behavioral cases and bounded, local-report commands are in [the evaluation guide](tools/agent-collab/evals/README.md). They cover skill selection and a synthetic verification task with a no-plugin arm; they do not establish real peer usefulness, end-to-end closure, or Codex behavioral coverage. Unit tests check the fixtures and graders separately from live model behavior.

The 2026-10-30 installed-plugin follow-up added regressions for exact run selection, zombie reports, normalization provenance, Linux PID namespaces, private storage parents, explicit host admission, request binding, filtered Git files, nested submodules, and review-schema conformance. All 272 tests pass without skips on Python 3.11.18, 3.13.5, and 3.15.2; package synchronization passes. The real Bubblewrap namespace test passes on the development Linux host; CI and native macOS/Windows execution remain separate validation requirements. Git subdirectories passed as `--repo-root` are normalized to the worktree root, including peer cwd, storage, and snapshot scope. `doctor` reports resolution failures as failed checks; commands that manage a run still require a valid canonical workspace identity.

The broader 2026-10-25 provider audit covered launch configuration, models and effort, helpers, permissions, hooks, memory, research, output, prompting, skills, caching, packaging, installation, and validation. It combined an independent host pass, an OpenAI-focused helper, an opposite-product Claude review, and fresh advisory adjudication. Adopted findings fixed Codex memory-alias precedence, remote message-board ingress, npm sandbox initialization, and explicit fresh-context helper selection. The [coverage record](tools/agent-collab/references/provider-audit.md) links the sources and separates product choices from defects.

The 2026-10-25 guidance refresh checked current Codex and Claude prompting, subagent, skill, and model-selection guidance, including Anthropic's main-loop article. Independent Claude `claude-opus-5-5`/`xhigh` reviews ran before and after the first implementation checkpoint. The follow-up review confirmed the V1/V2 source claims and preserved workflow, and identified a missing instruction to pass the delegation ban to fresh-context helpers; that instruction and its prompt-contract checks were added. The entry point now routes to required run-selection and artifact guidance, with detailed runtime/failure sections consulted when relevant. This is a maintenance change, not evidence of improved model performance.

After the comprehensive audit changes, all 228 tests passed without skips on Python 3.11.17 and 3.15.1. `scripts/release-check.sh` passed in release mode using Codex CLI 0.164.0 and Claude Code 2.1.310, including both official validators and isolated marketplace installations. The local Codex skill validator matched the file shipped in the 0.164.0 source. The live comprehensive-audit peer returned a valid schema 2.0 report; API usage contained the requested Opus model and allowed auxiliary Haiku, its Stop receipt reported `xhigh`, and the disposable workspace cleanup completed. Tests inside the peer had two environmental skips; the independent host checks above covered those checks without skips.

The 2026-10-22 review checked settings, instructions, and every documentation file against current Claude Code and Codex documentation, the Codex 0.163.0 source, and the implementation, through three independent lenses: Claude Code conformance, Codex conformance, and documentation against code. It found no wrong or deprecated provider setting, flag, hook field, or manifest field; the host re-verified each adopted finding, and 6.0.0 fixes them with regression tests. The Codex helper overrides, including `agents.max_depth`, were accepted by `codex exec --strict-config` against an unauthenticated `CODEX_HOME`, so no model ran. `scripts/release-check.sh` passed in release mode with Codex CLI 0.163.0 and Claude Code 2.1.306, and the unit suite passed on Python 3.11, 3.13, 3.14, and 3.15.

The 2026-10-21 audit reviewed the runtime, schemas, tests, packaging, and documentation against current Claude Code documentation, Codex documentation, and the Codex 0.163.0 source. It combined an independent Agent Collab Codex peer (`gpt-6-astra`/`xhigh`, isolated workspace), four host-local lenses, and advisory adjudication; every material claim was re-verified by the host. Confirmed findings were fixed in 5.0.0 with regression tests, including a real fuse-overlayfs and bwrap integration test of the isolated boundary. `scripts/release-check.sh` then passed in release mode: the full unit suite, package byte and mode parity, compilation, strict parsing of every tracked JSON file, schema meta-validation, version consistency, both official validators, and isolated installations on both hosts. Codex launch overrides were checked against `codex exec --strict-config` without starting a session.

The latest results are scoped to this Linux checkout with the versions recorded above. CI is configured for Linux, macOS, and Windows with Python 3.11 and 3.15; its first macOS run found the zombie EPERM defect fixed in 5.0.1. The 2026-10-25 work exercised Codex-hosted Claude review, but did not rerun macOS or native Windows CI, a live Windows peer, live safe-mode approval behavior, or comparative quality, latency, and cost measurements. Fresh availability probes remain mandatory for every run, since account and model access is not a durable guarantee. Earlier evaluations are recorded in [CHANGELOG.md](CHANGELOG.md).

The Codex and Claude manifests and root `VERSION` must always contain the same Semantic Versioning 2.0.0 version. Record user-visible and breaking changes in the root `CHANGELOG.md`; do not put changelogs inside skills. Version 2.0.0 intentionally rejects 1.x settings and run artifacts rather than translating them or probing compatibility fallbacks.

## License

Agent Collab is available under the [MIT License](LICENSE). Reuse, modification, and redistribution require preserving the copyright and license notices. The installable package includes the same license text.
