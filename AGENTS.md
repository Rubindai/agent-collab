# Agent Collab Repository Guidance

## Source of truth

- Edit the canonical shared skill workflow, runtime code, references, and schemas only under `tools/agent-collab/`.
- Run `scripts/sync-packages.sh` after shared skill, runtime, reference, or schema changes. The packaged skill and shared resources must remain byte-for-byte synchronized.
- Codex and Claude Code share one dual-host package at `plugins/agent-collab/`. Both repository marketplaces must point to that path; do not reintroduce a separate `plugins/claude/` tree.
- Install through the repository marketplaces. Do not add direct installers, cache edits, path probes, shims, aliases, migrations, or compatibility fallbacks.
- Keep shared repository guidance in `AGENTS.md`. Keep `CLAUDE.md` as the single `@AGENTS.md` import; do not add a singular `AGENT.md`.

## Runtime contract

- Persisted settings, state, requests, helper reports, and synthesis artifacts use strict schema 2.0 contracts. Reject older, incomplete, noncanonical, or wrongly typed files instead of translating or coercing them.
- Default Codex peers to `gpt-5.6-sol` with effort `max` and Claude peers to `claude-opus-4-8` with effort `max`.
- Preserve user-requested per-run model and effort values exactly after resolving documented aliases. For Codex, refresh `codex debug models` and validate the requested effort against the exact live model entry instead of maintaining a documentation list; the current `gpt-5.6-sol` catalog advertises delegation-enabled `ultra`. For Claude peers, accept `low|medium|high|xhigh|max|ultracode`; `ultracode` is a CLI orchestration mode whose effective provider effort is `xhigh`.
- Require a fresh availability attestation before creating a run or launching a peer. Codex must advertise the exact pair in the refreshed catalog. In a live configuration-reduced Claude probe, API `modelUsage` must include the exact resolved model and the `Stop` hook must report the expected effective effort (`xhigh` for `ultracode`, otherwise the requested provider level); documented auxiliary Haiku usage is not a primary-model substitution. Treat `unavailable`, `unknown`, a downgraded effort, or another observed primary model as a hard prelaunch failure; never select or retry a fallback.
- Treat `claude-fable-5` as explicit-only. Accept Fable only from a user-requested per-run override, never from built-ins, environment, local settings, or global settings. Do not configure or retry an Opus fallback.
- Resolve Agent Collab launch settings in the host and persist them in `host-request.json`; the peer runtime must not independently reinterpret those values from Agent Collab environment variables. Do not describe the request as controlling provider policy, installed tools, MCP configuration, or other configuration the peer CLI may load.
- Keep the configured full-capability launch posture unless the user explicitly selects safe mode, but keep edit authorization separate: `edit_allowed` defaults to false and becomes true only after explicit user delegation. Full mode retains the provider's full/default tool surface and permissions, including Claude `bypassPermissions`. Document that this is powerful and that Anthropic recommends `bypassPermissions` only inside isolated containers, VMs, or development containers without internet access where host damage is impossible. Do not claim the full-mode no-edit prompt or mutation detector is preventive enforcement.
- Do not add helper-agent `tools` or `disallowedTools` frontmatter restrictions. In full mode helpers retain the host tool and permission context, subject to provider policy. Persist a finite per-run helper bound, default eight. Runtime-enforce Claude peer `Agent` calls with a command-line-priority hook, direct denial when the bound is zero, and a preflight that fails closed when managed policy suppresses the hook; enforce Codex peer fanout/depth/runtime and helper artifact admission. Host-native helper launch discipline remains a host instruction because the shared runtime cannot intercept every host tool call. Do not claim broader enforcement.
- Keep one atomic active-workflow guard per Git worktree from `start` through a validated `host-synthesis.json` and `complete`, or through `cancel`. Serialize every guard check, stale-start recovery, acquisition, update, and release with one stable per-worktree OS file lock and a bounded lock wait; never conditionally unlink a guard from an unlocked stale read. Use startup gates so neither wrapper nor provider can run before its exact process artifact is committed. Release the guard only after tracked groups are terminal and quiescent. This is the hard cross-product recursion boundary; the peer depth marker and host-CLI shadow are defense in depth. Separately require a hard finite peer timeout, default 2700 seconds, and terminate with a structured timeout result at the absolute deadline. Never support an indefinite peer timeout.
- In Linux safe mode, preflight `bwrap` before launching a Codex read-only peer and emit a structured sandbox-unavailable result if it cannot initialize. A Claude safe-mode peer uses provider-native `plan` permission mode; document that `plan` is not an OS container. Agent Collab itself must not request or preflight `bwrap` for Claude or in full-capability mode; separately loaded Claude settings can still enable Claude's sandbox.
- Describe the online-research setting as control of the provider research tools Agent Collab configures, not as a general network sandbox for shells, MCP, plugins, or other provider facilities.
- Describe mutation comparison as detective and bounded: it covers tracked, untracked, most ignored, and selected Git-control surfaces, but excludes known high-volume generated/dependency directories and cannot observe transient changes reverted before snapshots.
- Keep peer prompts compact, escaped, neutral, and non-leading. Follow current Codex and Claude guidance: state the outcome, relevant context, output or done condition, decisive boundaries, and available verification evidence. For review mode, ask for coverage across every in-scope issue and carry severity and confidence into the report so the host verification stage can filter; do not use vague importance filters that suppress recall. Do not embed request JSON or the response schema, reveal host conclusions, over-prescribe the peer's process, or call a user prompt a security boundary.

## Releases

- Use Semantic Versioning 2.0.0. `VERSION` and both plugin manifests must match exactly.
- Record user-visible and breaking changes in the root `CHANGELOG.md`; do not add changelogs inside skill directories.
- Do not tag, publish, or modify a user marketplace unless the user explicitly requests it.

## Verification

- Run `python -m unittest discover -s tests -v`.
- Run `scripts/sync-packages.sh --check`.
- Validate every JSON file, both plugin manifests, the Codex skill, and the Claude marketplace/plugin with their official validators.
- Test against Claude Code 2.1.214 or newer; Claude marketplace installation must pass `--scope user` explicitly.
- Treat `tools/agent-collab/runs/`, settings, caches, and generated plugin state as local artifacts.
