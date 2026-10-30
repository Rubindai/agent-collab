# Changelog

## Unreleased

- Reduce runtime duplication with shared module imports, strict field validation, and provider-result handling. Malformed request fields now produce structured failures, and executable entry points resolve sibling modules with Python safe-path mode enabled.
- Add a resumable `start`/`advance` host workflow with explicit next actions, resolved host policy, bounded 60-second guided waits, and successful waiting responses. Keep the normal instructions self-contained within 8 KB and make recovery diagnostics use the guided interface.
- Save readable, revision-bound Markdown evidence separately from compact control output, preserving all original reports while omitting duplicate matrix rows and admission hashes from the reading surface. Review receipts now bind rendered bytes; refresh older drafts through `advance` before synthesis.
- Count advisory reports within the host helper-report bound and route exhausted budgets to synthesis with the missing advisory visible.
- **Breaking:** require explicit CLI admission for host reports and revision-bound synthesis, preserve the first admitted host pass, and verify the saved request against its launch-time digest. Close active runs before upgrading; old process artifacts lack the required request digest and admission receipt.
- Keep filtered-file mutation evidence byte-based, support `=` in filter names, align generated review receipts with their schema, reject deleted admission receipts, and keep `doctor` diagnostics available when Git root resolution fails.
- Disable Git clean/process filters during snapshots, including initialized nested submodules, and track `.git/info/attributes`; create missing storage parents privately, reject malformed verdict types cleanly, and quote source text in Markdown review views.
- Fix exact run selection across run roots, normalize explicit Git subdirectories to their worktree root, treat unreaped wrappers as exited, and preserve normalization failures across repeated `finish` calls.
- **Breaking:** Linux process identities now require the observing PID namespace. Foreign or unreadable namespaces cannot authorize recovery or cleanup; finish active runs before upgrading. POSIX `ps` identities use UTC and the C locale, with an explicit format marker so older identities cannot be mistaken for dead processes. Older historical run artifacts are rejected by the new strict contracts.

- Accept compact first-pass, helper, and adjudicator judgments through `finish` flags. Generate revision-bound synthesis inputs and fill canonical metadata and evidence hashes through `complete --synthesis`; render pinned evidence with `result --format markdown --review`.

- Bind registry mutations to exact run instances, reserve lifecycle capacity before admission, and replace startup-wide recovery scans with targeted recovery. Add `status --active` and `recover RUN_ID`; `result` now requires an explicit run.
- Report malformed artifact filenames without a traceback and reject authored judgments disguised as the reserved adjudication placeholder.

- **Breaking:** allow independent Agent Collab runs in the same worktree, tracking each run in a shared guard registry. Finish active runs before upgrading; older and newer runtimes cannot share an active guard. Recursion controls are no longer a hard worktree-wide boundary.

## 6.0.1 — 2026-10-25

- License the project under MIT and include the license, package README, and SVG listing/composer icons in the synchronized dual-host package.
- Disable Codex message boards for peers so inherited remote-board configuration cannot inject cross-session messages into an ephemeral independent review. Ordinary local-helper messaging remains available.
- Name the required `fork_turns="none"` argument for independent Codex helpers and distinguish that instruction from Claude fork controls. Clarify retained hooks/MCP, Skill-triggered subagents, and explicit Fable 5.1 selection.
- Fix Linux Codex safe-mode preflight for npm installations by asking Codex to initialize its read-only sandbox instead of guessing bundled Bubblewrap paths.
- Pin Codex's canonical memory-use and memory-generation controls so an inherited provider feature alias cannot restore automatic saved-memory context or memory tools.
- Correct the public-directory publication guidance to distinguish OpenAI's icon requirement from Anthropic's published checklist.
- Shorten the skill entry point and move conditional launch, platform, and failure details into a dedicated reference while preserving the peer-first workflow and closure requirements.
- Select optional helpers by context separation and independent verification; keep tightly coupled work in the main loop. Assign launch choices to the spawner and explicitly forbid helpers from delegating again.
- Correct generated Codex prompts and documentation to distinguish V1 configured depth from V2 instructional depth, hidden spawn override fields from runtime rejection, and Claude model-list narrowing from exact-model checks.

## 6.0.0 — 2026-10-22

- **Breaking:** require Python 3.11 or newer. Python 3.10 reached end of life on 2026-10-01, and `doctor` now flags it. CI tests the oldest supported release, 3.11, and the latest stable, 3.15.
- **Breaking:** accept only ASCII decimal numbers for `AGENT_COLLAB_TIMEOUT_SECONDS` and every numeric command-line flag, as integer settings already required; Unicode digits, underscores, and exponents are rejected.
- `start` refuses a Git repository when `git` is not on `PATH` instead of moving the worktree guard out of the repository, where it would not exclude a run started with Git available.
- Pin Codex `agents.max_depth=1` for models on multi-agent V1, which still expose per-spawn model and effort overrides, and drop the legacy `features.memory_tool` alias; `features.memories=false` already disables memory.
- Raise Claude hook timeouts from 5 to 30 seconds, so a slow interpreter start on Windows or macOS neither blocks a legitimate `Agent` call nor loses Stop effort evidence.
- Name the actual deadline in timeout results instead of `AGENT_COLLAB_TIMEOUT_SECONDS`.
- Write skill commands with literal placeholders instead of shell variables, so they also run in PowerShell, where `$host` is reserved.
- Move CI to `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node.js 24, and require the latest stable release of every selected dependency in `AGENTS.md`.
- Correct the documentation against the implementation and current Claude Code and Codex documentation: the isolated workspace's fresh `/tmp`, minimal `/dev`, and which sockets stay reachable; credential paths; the startup gates' scope; macOS Codex safe mode; multi-agent V1 overrides; the effective Codex `ultra` effort; mods as a spawn channel outside the `Agent` counter; Claude Code's refusal of `bypassPermissions` as root; WinGet updates; and the workflow and agent-team environment pins.

## 5.0.1 — 2026-10-21

- Fix macOS process-group supervision. macOS answers EPERM to `killpg` while a group holds an unreaped zombie, so cleanup reported `permission_denied`, waited out its grace period, and left runs non-quiescent. Group liveness now comes from a `ps` scan that ignores zombies, and EPERM falls through to that check; Linux keeps its existing EPERM handling. First found by the macOS CI job, which had not run before.

## 5.0.0 — 2026-10-21

- **Breaking:** reject `codex_config` entries in, or parent tables of, every Codex namespace that selects the model, provider endpoint, auth, catalog, approval/reviewer, sandbox, tools, memory, or helpers (for example `openai_base_url`, `model_catalog_json`, `approvals_reviewer`, `tools.*`, `sandbox_workspace_write.*`). Settings that contain them must be edited before upgrading.
- **Breaking:** reject an empty boolean environment value (such as `AGENT_COLLAB_SAFE_MODE=`) instead of treating it as false, reject non-ASCII integer digits, and require the `claude_tools` sentinel to be lowercase `default`.
- **Breaking:** stop selecting the state root from an inherited `CLAUDE_PLUGIN_DATA`. Claude Code never exports it to Bash-tool commands, so the value always belonged to another plugin whose data directory is deleted on uninstall. Claude-host state now lives under `${XDG_STATE_HOME:-~/.local/state}/agent-collab`; archive any earlier history from the other plugin's directory.
- Pass `approval_policy="never"` and `approvals_reviewer="user"` to `codex exec`, which ignores the root `--ask-for-approval` flag and otherwise turns an `auto_review` user config into on-request approvals.
- Pin the Codex `features.multi_agent_v2` helper cap and disable per-spawn model overrides, so a user `config.toml` cannot widen the helper bound; disable it with helpers.
- Disable provider memory for peers (Codex `features.memories`/`memory_tool`, Claude `autoMemoryEnabled` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY`) and ask peers not to consult saved memory or earlier run artifacts.
- Detect Codex model reroutes from the `item.completed` error items `codex exec --json` actually emits.
- Stop binding the run directory writable into the isolated sandbox, clean up from in-memory mounts, re-verify workspace cleanup after cancel kills a live wrapper, make isolation state writes crash-safe, and bind the peer product's credential file through so rotated refresh tokens persist. Document that Docker, D-Bus/systemd, SSH-agent, and network access remain reachable and unobserved in isolated mode.
- Close direct-mode snapshot gaps: no false mutation from a read-only `git status`; detect assume-unchanged, skip-worktree, permission-only, textconv-hidden, and submodule content changes; ignore inherited `GIT_*`; disable fsmonitor; and handle non-UTF-8 filenames. The wrapper reuses the host baseline, and the attestation window covers the bounded baseline snapshot.
- Add peer findings to the claim matrix as unverified rows, bind peer-process deadline metadata to the request, resolve the active run through the worktree guard across host run roots, reject symlinked guards, bound Git probes, defer signals during provider cleanup, release the provider gate only after pending-signal checks, preserve another worktree's or a young startup's runs during `clear-history`, and reject lone surrogates and digit-limit integers in strict JSON.
- Give helper agents `model: inherit`, scope their descriptions to Agent Collab runs, specify that the host saves the adjudicator's JSON report, and probe the bwrap bundled with Codex for Linux safe mode.
- Add `scripts/release-check.sh` and a CI workflow; make `sync-packages.sh` use `python3`, parse strictly, compare file modes, and swap generated trees atomically; install only missing isolation packages; make tests hermetic and portable; and add schema/runtime parity tests.
- Support native Windows hosts. Each tracked process group is a named kill-on-close Job Object that forbids breakaway; roots start suspended until assigned, and an invisible job never counts as quiescent while its root lives. The detached wrapper breaks away from the caller's job and fails closed when that is forbidden. Startup gates pass an inheritable pipe handle to an isolated launcher that relays the provider's exit status; locks use byte-range locking; entry points use UTF-8 with LF on standard streams. Provider CLIs must resolve to native `claude.exe` and `codex.exe` (npm `.cmd` shims are rejected), fresh settings use direct mode, and Codex safe mode reports `sandbox_unavailable`. Host commands refuse to finish, complete, or cancel a run whose process identity another platform recorded, and unknown identity evidence never releases a guard. Claude hooks now use exec form on every platform, artifacts are written with LF endings, provider pipes use UTF-8, and CI adds Windows runtime checks.
- Run in folders outside any Git repository without Git installed, using filesystem snapshots; a Git repository still requires `git`, and `doctor` flags it only there.
- On Windows, run snapshot Git commands in a job nested in the caller's job, so a timeout also stops Git helpers holding their pipes; POSIX keeps Git in the caller's process group so cancellation still covers it. Report an expired snapshot deadline as `timeout` rather than `snapshot_unavailable`.
- Use `$agent-collab:agent-collab` for explicit Codex mentions, document storage locations, catalog caching, and probe limits, and refresh the README, references, and `AGENTS.md` against current documentation.
- Finish and complete runs started with 4.x before upgrading: peer findings now appear in the rebuilt claim matrix, so its digest changes and `complete` can refuse a synthesis written before the upgrade.

## 4.0.0 — 2026-10-21

- Add a Linux system-package setup script for disposable workspaces, with a read-only dependency check and Debian/Ubuntu and Fedora installation paths.
- **Breaking:** default full-capability peer runs on Linux to a disposable copy-on-write repository and HOME workspace. Preflight FUSE OverlayFS and bubblewrap before run creation; fail closed if unavailable. Permit temporary validation writes and discard them after the run. Fresh macOS settings default to direct mode with bounded persisted snapshots, which detect included changes but do not prevent them; explicit settings remain authoritative. Durable delegated peer edits require direct mode. Isolated runs use a lightweight boundary receipt instead of hashing large ignored trees. Add strict `workspace_mode` settings and request fields; existing v2 settings must be recreated.
- Keep large workspace baselines below the artifact reader limit by streaming Git index and patch digests and retaining bounded path samples with complete manifest digests; report when changed paths fall outside the sample.

## 3.0.0 — Untagged GitHub candidate

- Launch Codex peers in non-Git folders by skipping Codex's Git-repository check only when the workspace snapshot identifies a filesystem workspace.
- Treat plain-language "use agent collab" as an explicit request for the real opposite-product peer workflow while retaining implicit discovery.
- Fix availability-probe interruption cleanup, launch-time attestation expiry, snapshot index self-mutation, strict artifact types, symlink rejection, and preservation of normalization warnings. Harden prefixed Fable admission, Agent counters, conflicting Claude evidence, and effort escaping.
- Reject every conflicting Codex model/effort observation, even when matching telemetry also appears. Require bounded actual-run Claude Stop-hook effort evidence; later matching observations cannot erase a mismatch.
- **Breaking:** deny Workflow orchestration after a live zero-helper probe demonstrated that Workflow agents bypass the Agent-call counter. Explicit `ultracode` now fails with `helper_policy_unavailable` before run creation; it is never silently downgraded. Provider availability checks still distinguish CLI support from this runtime-policy restriction.
- **Breaking:** require Claude Code 2.1.295+ for fail-closed helper hooks; reject Codex provider/profile overrides that would detach launch from availability. New strict synthesis/result/matrix digest fields bind the complete admitted evidence; close active runs before upgrading.
- Preserve workflow ownership when process identity cannot be inspected, reject oversized baselines before launch, and make interrupted cancellation resume from immutable snapshot evidence.
- Apply Claude helper pins through command-line settings, refuse inbound cross-session messages, and document the limits of process-group and helper accounting.
- Reassess the complete README and repository guidance on 2026-10-18; correct local Claude marketplace reload behavior, current sandbox evidence, fallback-setting semantics, and package verification guidance.
- Document current helper-cache tradeoffs and provider-managed cache lifetimes without claiming measured cost or quality gains. Move per-run identity after shared outcome/evidence text and retain bounded Codex stdout usage telemetry separately from the required final report.
- Enforce the documented Linux/macOS launch boundary for other POSIX platforms as well as Windows, and use the same platform predicate in diagnostics.
- Require Codex 0.163.0+ for current helper-selection controls; older versions fail before run creation.
- Pin Codex helper model and effort defaults to the selected peer pair, force Claude helper model selection, and pin Claude effective effort, with provider `ultracode` semantics documented separately from the execution-policy rejection. Document the remaining Codex custom-agent override boundary.
- Require fresh independent helper contexts, disable Claude conversation forks, state the numeric Codex total-spawn bound, and assign disjoint write scopes with one integration owner for authorized parallel edits.
- Remove the obsolete external Codex plugin-validator hook and its unsupported release requirement. Retain strict JSON/schema checks, official skill and Claude validation, and isolated installations on both hosts.

## 2.1.0 — 2026-10-13

- Default new Codex peer configurations to `gpt-6-astra` at `xhigh` and Claude peers to `claude-opus-5-5` at `xhigh`. Preserve persisted settings, explicit overrides, strict schema 2.0, and exact-pair availability checks without fallback.
- Refresh peer, host, and helper instructions against the GPT-6 Astra and Opus 5.5 guides: complete authorized work, await pending checks, state blockers and uncovered scope, and repeat verification only when justified.
- Tighten skill discovery and rewrite `AGENTS.md` into focused repository rules while preserving runtime, authorization, packaging, and release contracts.

## 2.0.0 — 2026-07-26

- Upgrade the default Claude peer from `claude-opus-4-8` to fixed model ID `claude-opus-5` at `max`, require Claude Code 2.1.219+, and keep Fable 5 explicit-only.
- Stop the entire Agent Collab attempt before run creation when exact model/effort availability is unavailable or unknown, with a user-facing structured attestation and no peer, helper, host-first pass, adjudicator, or fallback launch.
- Classify Claude quota, rate-limit, authentication, and access failures as explicit unavailability with a bounded provider explanation; retain `unknown` for indeterminate failures.
- Pin Claude's exact requested model in launch settings, keep overload fallback chains empty, disable automatic refusal model switching, and retain post-run `modelUsage` mismatch detection.
- Disable nested Claude helper spawning with the documented depth setting, while retaining the finite total `Agent`-call hook and exact inherited helper model.
- Tune the shared prompt for Opus 5 and GPT-5.6: concise deduplicated reports, explicit scope and stopping conditions, no generic repeated self-verification, and helpers only for sizeable independent workstreams.
- Remove the hidden default Claude `--max-turns 50` cap. Claude peers now have no turn cap by default and remain bounded by the absolute 2700-second peer deadline; `--claude-max-turns N` is an explicit per-run opt-in and a reached cap is reported as `turn_limit_reached`.
- Remove the vestigial `profile` setting and replace its only runtime effect with the independent `adjudicator_enabled` setting and per-run `--adjudicator` override.
- Persist `agent_timeout_seconds` as a strict JSON number, allow finite 60–86400 second deadlines, and retain 2700 seconds as the quality-oriented default.
- Replace legacy Codex fanout keys with current `agents.enabled` and `agents.max_concurrent_threads_per_session`. Record provider-specific helper semantics instead of claiming that Codex total calls, nesting depth, or per-helper runtime are runtime-enforced.
- Reject user `codex_config` entries that override Agent Collab-owned model, effort, sandbox, approval, web-research, or helper-control keys.
- Make cancellation append-only and terminal-aware: preserve peer, normalization, result, synthesis, and claim-matrix evidence; write strict `cancel-result.json` plus separate cancellation mutation evidence; and direct already-terminal runs through `finish` instead of converting them into cancellations.
- Exclude untracked and ignored `.next` build output from mutation snapshots while continuing to detect tracked `.next` changes through Git status, index, and diff evidence.
- Audit every persisted and provider launch setting against current OpenAI and Anthropic documentation and installed CLI help, documenting each product decision and use-case tradeoff.
- Raise the minimum tested Codex CLI to 0.145.0 and Claude Code to 2.1.219+.
- Make availability attestations short-lived and exact at peer launch, tolerate forward-compatible unrelated Codex catalog metadata while failing closed on a malformed requested row, and preserve the exact selected model and effort for native helpers.
- Split the portable provider output schema from the canonical peer-report and normalization contracts, then enforce run, origin, host, peer, status, and error relations after provider extraction.
- Harden lifecycle concurrency with a bounded per-run finalization lock, terminal-state rechecks, derived claim-matrix rebinding, and deterministic finish/complete/cancel race coverage.
- Write run directories and sensitive artifacts with owner-only POSIX permissions, fail closed on symlink destinations and helper-counter tampering, and reject unsupported non-POSIX peer launch before creating artifacts.
- Move Codex metadata and Claude helper definitions into canonical `tools/agent-collab/agents/` sources and synchronize them byte-for-byte into the dual-host package.
- Add explicit one-run safe-mode, timeout, and helper overrides; strengthen mode classification, canonical Codex config validation, Claude tool-sentinel validation, recovery guidance, and full schema validation in `doctor`.
- Document which provider controls are deliberately not enabled, the procedural limits of independence and adjudication, and the exact boundaries of retention, mutation evidence, provider structured outputs, and installed-package refresh behavior.

## 1.0.0 — 2026-07-20

- Replace the legacy runtime contract with strict settings and run schema 2.0.
- Default Codex peers to `gpt-5.6-sol` at `max` and Claude peers to `claude-opus-4-8` at `max`.
- Add exact per-run model, effort, and online-research overrides, including explicit-only `claude-fable-5` at `max` and explicit Claude Code `ultracode` orchestration with effective provider effort `xhigh`.
- Attest every model and effort pair before launch: use the refreshed `codex debug models` catalog for Codex and live Claude API `modelUsage` plus `Stop` effective-effort hook telemetry. Surface unavailable or unknown pairs without fallback.
- Fail with a structured unavailable result when a requested peer pair or safe-mode isolation backend cannot be used; never retry with a fallback model, effort, or unsandboxed safe-mode peer.
- Require Codex CLI 0.144.5+ and Claude Code 2.1.214+; remove old-flag and compatibility fallbacks.
- Replace duplicated prompts with one compact escaped XML peer brief based on outcome, relevant context, boundaries, done criteria, and verification evidence, plus a provider-compatible structured-output schema.
- Publish one dual-host package at `plugins/agent-collab/` through the native Codex and Claude repository marketplaces, and remove direct installers and legacy package paths.
- Preserve the configured full-capability launch posture and helper tool access while separating it from explicit edit authorization, enforcing bounded peer fanout, a hard finite peer timeout, and an atomic worktree recursion guard from `start` through `complete` or `cancel`.
- Document Anthropic's isolated, no-internet environment warning for `bypassPermissions`; runtime-enforce Claude peer `Agent` calls, Codex peer fanout, and helper artifact admission without claiming interception of every host-native helper launch.
- Investigate the reported pre-1.0 `bwrap user-namespace failure` continuation message, mark its historical provenance unverified, and remove any authorization meaning. Agent Collab itself requests `bwrap` only for the Linux Codex safe-mode preflight; a peer failure never authorizes direct host implementation.
- Tune review prompting for Opus 4.8 coverage: report every in-scope candidate with severity and confidence, then let host verification filter the findings.
- Remove noncanonical output recovery, persisted-setting coercion, old state resets, and configured Claude fallback chains.
- Use a provider-compatible peer output schema, configuration-reduce Claude availability probes, and disable probe MCP discovery.
- Fix strict JSON mutation diagnostics, scope Linux `bwrap` preflight to Codex read-only peers, and purge unexpected stale packaged-skill payload during synchronization.
- Add Git marketplace update guidance and require strict Claude validation plus explicit user-scope isolated installation in release tests.
- Share repository instructions through `AGENTS.md` and the exact `CLAUDE.md` import; use Semantic Versioning without skill-local changelogs.
- Gate wrapper and provider startup on committed process artifacts; terminate and prove tracked process-group quiescence before finish, cancel, or guard release; bound provider output capture during execution.
- Make host-side mutation comparison independent of cooperative peer completion, include ignored files and mutable Git refs/config/index surfaces, and document the remaining high-volume/transient blind spots.
- Bind final synthesis to the exact mutation diagnostic, preserve guarded runs during history cleanup, and persist terminal process proofs so stale or reused numeric process groups are never signaled after quiescence.
- Canonicalize the prompt's exact XML-entity target representation, isolate tests from ambient collaboration markers, require finite provider-command timeouts, and serialize all guard transitions with a bounded stable OS lock so stale-start recovery cannot delete a replacement run.
