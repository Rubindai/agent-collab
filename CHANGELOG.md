# Changelog

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
