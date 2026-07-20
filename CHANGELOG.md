# Changelog

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
