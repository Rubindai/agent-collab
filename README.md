# Agent Collab

Agent Collab is a repo-local challenge-first second opinion workflow for Codex and Claude Code. It starts the opposite product as an independent peer first, lets the host do its own unpolluted analysis in parallel, then uses host-owned judging and synthesis to produce the final answer.

The default profile is `ultra`: maximum capability, full repo/tool/network access, peer-first execution, host-local helper agents, and an advisory adjudicator.

## What It Does

Use Agent Collab for high-stakes work where a second independent AI pass is worth the cost:

- strict code review, security-sensitive review, and risky implementation verification
- repo-grounded architecture decisions, technical design critique, design debate, and architecture tradeoffs
- debugging, source-backed research, implementation planning, and test planning
- any task where the user asks for Claude+Codex cross-checking

Avoid using Agent Collab for casual brainstorming, naming, simple idea generation, routine Q&A, and low-risk questions where a second independent pass adds little value.

The host remains the final decision maker. Peer, helper, and adjudicator reports are evidence, not authority. Agreement is not proof; the host and peer should seek disconfirming evidence before accepting claims.

The runtime auto-selects one of five canonical modes from the target and neutral brief unless `--mode` is supplied explicitly:

- `review`: challenge whether the work should ship. Use for diffs, patches, risky changes, security-sensitive code, missing tests, and release readiness.
- `research`: challenge whether claimed facts are true, current, and applicable. Use when current external/API/platform/dependency/tooling facts are the primary deliverable.
- `design`: challenge whether the proposed approach is the right architecture. Use for architecture choices, tradeoffs, alternatives, compatibility, and migration approaches.
- `plan`: challenge whether the execution sequence is actually ready. Use for implementation plans, rollout plans, test strategy, sequencing, rollback, and readiness.
- `debug`: challenge the initial diagnosis. Use for bugs, crashes, failing tests, logs, reproduction gaps, and root-cause analysis.

official-doc research can happen in any mode. `research` is only selected when source-backed external facts are the main deliverable, not when docs are just supporting evidence for review, design, plan, or debug.

Freshness rule: When a material claim depends on current or external information, including APIs, product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, or research, use the latest official documentation or primary sources. Do not rely on model memory for unstable facts. If online research is disabled or sources are unavailable, state that limitation explicitly and mark the claim as unverified.

## Repo Layout

```text
codex-plugin/agent-collab/          Packaged Codex plugin source
  .codex-plugin/plugin.json         Codex plugin manifest
  skills/agent-collab/              Codex skill bundled by the plugin
claude-plugin/agent-collab/         Packaged Claude Code plugin source
  .claude-plugin/plugin.json        Claude plugin manifest
  skills/agent-collab/              Claude skill bundled by the plugin
  agents/                           Claude helper agents bundled by the plugin
tools/agent-collab/                 Shared runtime, prompts, schemas, and source-checkout run artifacts
scripts/                            Sync and install helpers
tests/test_agent_collab_runtime.py  Runtime and metadata tests
```

The shared runtime follows the standard skill resource shape:

```text
tools/agent-collab/
  scripts/      host and peer runtime entrypoints
  references/   peer-only, prompt-block, and synthesis contracts
  schemas/      structured request/report schemas
  runs/         local run artifacts and job state, ignored except .gitkeep
  settings.local.json  optional repo-local Agent Collab settings, ignored by Git
```

Runtime entrypoints:

- `tools/agent-collab/scripts/host.py`: creates run directories, snapshots workspace state, starts the peer first, and validates finish artifacts.
- `tools/agent-collab/scripts/peer.py`: invokes the opposite product once, preserves raw output, normalizes the report, and returns schema-validated JSON.
- `tools/agent-collab/scripts/snapshot.py`: records git snapshots in git repos and deterministic filesystem snapshots in non-git directories.
- `tools/agent-collab/scripts/state.py`: keeps a local ignored job ledger for status, result, cancellation, and cleanup commands. The ledger always keeps active jobs and prunes older terminal jobs toward a 50-entry retained history; `status` shows the newest 8 by default and `status --all` shows all retained jobs.

The packaged Codex and Claude plugin skills both have the same `scripts/`, `references/`, and `schemas/` directories as `tools/agent-collab/`. They intentionally do not contain `runs/`, `settings.local.json`, a nested `tools/agent-collab/` compatibility copy, or duplicated runtime resources at the Claude plugin root.

When you modify `tools/agent-collab/`, run `scripts/sync-packages.sh` so the packaged Codex and Claude skill copies stay in sync.

## Usage

Implicit invocation is allowed by default.

From Codex:

```text
Review this migration with Agent Collab.
```

Explicit Codex invocation also works:

```text
Use $agent-collab to review the current diff.
```

From Claude Code:

```text
/agent-collab:agent-collab review the current diff
```

Claude may also invoke the skill implicitly when the request matches its description and `when_to_use` metadata. The Claude skill intentionally does not set `disable-model-invocation: true`.

## Workflow

1. The runtime auto-selects `mode` from `target` and the neutral brief unless the host supplies a canonical mode explicitly. The host classifies `target`, `profile`, and `edit_allowed`; `profile` defaults to `ultra`.
2. The host writes a neutral brief before host analysis begins.
3. The host starts the peer immediately with `scripts/host.py start`.
4. The peer receives only the neutral brief, target, constraints, edit policy, and output schema.
5. While the peer runs, the host performs independent challenge-first analysis and records its own first-pass claims before reading the peer.
6. All agents use latest official documentation for external/API/platform/dependency/tooling claims and research online extensively when current external facts could affect the answer.
7. Freshness rule: When a material claim depends on current or external information, including APIs, product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, or research, use the latest official documentation or primary sources. Do not rely on model memory for unstable facts. If online research is disabled or sources are unavailable, state that limitation explicitly and mark the claim as unverified.
8. In `ultra`, Claude hosts can use the helper agents packaged with the Claude plugin. Codex hosts use available host-local subagents or built-in Codex agents with independent lens prompts for mapping, review, research, architecture, security, debugging, test strategy, and verification.
9. Do not read peer output until independent host work is complete. The host writes `host-first-pass.json` before reading `peer-report.json`.
10. `finish` is the normal synchronization point after `host-first-pass.json`: it waits responsively for peer artifacts, validates `peer-report.json`, builds a claim matrix, and avoids repeated host-visible status polling.
11. The minimum wait is 2700 seconds for a live peer. An empty `peer-report.json` or stderr does not mean the peer is stalled, and the host must not cancel the run or replace it with a direct fallback before that floor unless the user explicitly asks to stop.
12. The host runs an advisory adjudicator when available; otherwise `finish` writes an `advisory_pending` marker.
13. The host verifies high-value claims and writes the final synthesis.

Independence rule:

```text
neutral brief -> launch peer -> independent host analysis -> host first pass -> read peer -> adjudicate -> synthesize
```

Peer prompts are intentionally natural and non-leading. The runtime wraps the neutral brief in role-specific XML sections such as `<role>`, `<task_brief>`, `<request_json>`, and `<response_schema>` so Codex and Claude get clear boundaries without receiving host conclusions or a list of suspected bugs.
The prompt contract uses compact reusable blocks for structured output, grounding, tool persistence, and action safety.

## Run Artifacts

Runs are written under the active runtime data directory. In this repository that is `tools/agent-collab/runs/<run-id>/`. Installed packages do not write state into plugin code directories: Claude uses `${CLAUDE_PLUGIN_DATA}` when Claude exposes it, Codex plugin-cache installs use `${CODEX_HOME:-$HOME/.codex}/agent-collab`, and other installs fall back to `${XDG_STATE_HOME:-$HOME/.local/state}/agent-collab`. Set `AGENT_COLLAB_STATE_HOME` to override the data root. The `start` output is the source of truth for the exact run path.

History is retained so interrupted runs can be recovered, peer failures can be debugged, and `result` can reconstruct complete host/peer/adjudicator artifacts after the visible terminal turn has moved on. Because those artifacts may include prompts, reports, stderr tails, and workspace snapshots, use `clear-history` to remove old terminal run artifacts when they are no longer useful. Active runs are preserved by default.

Important files:

```text
host-request.json       Neutral request passed to the peer; run IDs are safe basenames only
before.snapshot         Workspace snapshot before peer work
peer-process.json       Peer PID and run metadata
host-first-pass.json    Host analysis written before reading peer output
peer.raw.json           Raw Claude/Codex CLI output envelope for debugging
peer-normalization.json How the runtime normalized or recovered the peer report
peer-report.json        Schema-validated peer report or structured peer failure
workspace-mutation.json Workspace mutation diagnostic when no-edit detection sees changes
claim-matrix.json       Host, peer, helper, and adjudicator claims grouped for verification when supplied
adjudicator-report.json Advisory judge output, optional advisory claims, or advisory_pending marker
host-result.json        Finish command summary
after.snapshot          Workspace snapshot after finish
state.json              Ignored recent-job ledger stored under runs/
```

## Permissions

Agent Collab defaults to full capability because it is designed for high-trust local repositories. This is a deliberate design choice: the peer and helper agents are given broad permission and full tool access by default so they can inspect, execute, research, and cross-check without artificial tool bottlenecks. That posture is intended to maximize review performance and finding quality in trusted workspaces. These are runtime peer-launch defaults, not committed project-level `.codex/` or `.claude/` host configuration. This repo intentionally ignores active `.codex/` and `.claude/` folders so local permission choices are not uploaded.

Codex peer defaults:

```text
--model gpt-5.5
--ephemeral
--cd <repo-root>
--config model_reasoning_effort="xhigh"
--config web_search="live"
--sandbox danger-full-access
--ask-for-approval never when supported
--dangerously-bypass-approvals-and-sandbox as fallback on older compatible Codex CLIs
```

Claude peer defaults:

```text
--model opus
--effort max
--permission-mode bypassPermissions
--dangerously-skip-permissions as fallback on older compatible Claude CLIs
--no-session-persistence
--json-schema ...
--output-format json
```

The runtime uses Claude Code's documented CLI flags rather than relying on `claude --help`, because Claude's own docs note that help output may omit flags. Custom `CLAUDE_AGENT_COLLAB_TOOLS` values are passed through `--tools`; `--allowedTools` / `--allowed-tools` are permission-preapproval flags and are not used as tool-availability substitutes.

Web research is a shared Agent Collab capability, not a shared tool-list abstraction. `web_research=live` and `web_research=cached` map to Codex as `-c web_search="..."` and keep Claude `WebSearch`/`WebFetch` available. `web_research=disabled` maps to Codex as `web_search="disabled"` and removes or disallows Claude `WebSearch`/`WebFetch` when the installed Claude CLI supports the relevant tool flags.

Full capability is for investigation and validation. It is not blanket permission to mutate. The default no-edit posture is prompt-level policy plus post-run workspace mutation detection, not a technical read-only sandbox. This is intentional because strict read-only modes can block ordinary investigation commands that write caches or temporary artifacts. In git repos the mutation check compares git status plus unstaged and staged diffs. In non-git directories it falls back to a deterministic filesystem snapshot so Agent Collab remains fully functional. Runtime artifacts, local Agent Collab settings, host config folders, VCS metadata, and common cache/build directories are excluded so the tool does not flag its own output. The check is still detective, not preventive, and it does not cover excluded paths or effects outside the target directory. When no-edit detection sees a workspace change, Agent Collab writes `workspace-mutation.json`; valid peer reports are preserved with a limitation warning, while already-failed peer reports keep their original failure and attach the mutation diagnostic. Peer and helper agents are still instructed not to edit unless `edit_allowed=true` and the user explicitly delegated edits.

Peer runs use structured output as the machine interface. Claude peers return a JSON envelope; the runtime preserves that raw envelope as `peer.raw.json`, normalizes `structured_output` into `peer-report.json`, and validates the normalized report against `schemas/peer-report.schema.json`.
If a CLI envelope contains prose before the JSON report, the runtime records the recovery in `peer-normalization.json` and extracts the first schema-valid report from the raw `result` text before declaring failure.

Use safe mode for untrusted repos:

```bash
AGENT_COLLAB_SAFE_MODE=1
```

Safe mode accepts `1`, `true`, `yes`, or `on`. It uses Codex read-only sandboxing and Claude plan permissions where supported. On current Claude CLIs without `--permission-mode`, the runtime avoids permission bypass and disallows edit tools when supported.

## Recursion Guards

Cross-agent depth is capped at 1:

```text
Codex host -> Claude peer
Claude host -> Codex peer
```

Forbidden:

```text
Codex host -> Claude peer -> Codex
Claude host -> Codex peer -> Claude
peer -> Agent Collab
peer local subagent -> host CLI
```

The runtime sets guard environment variables and prepends a run-local `bin/` directory to `PATH` so ordinary unqualified host CLI calls resolve to a blocking wrapper. This is a recursion guard, not a sandbox; it does not block absolute host CLI paths or deliberate PATH rewriting. Local subagent depth is capped by prompt and agent configuration; packaged Claude helper agents disallow nested `Task`, and Codex hosts should keep subagent depth at 1 in their user or project config when using custom helper agents.

## Install In This Repo

Install or validate the Codex plugin package:

```bash
scripts/install-codex-plugin.sh --dry-run
```

The Codex plugin installer copies `codex-plugin/agent-collab` to `$HOME/plugins/agent-collab`, creates or updates `$HOME/.agents/plugins/marketplace.json` with a local marketplace entry, and runs `codex plugin add agent-collab@personal --json` to refresh Codex's installed plugin cache when the Codex CLI is available. Use `--skip-codex-refresh` only when the CLI is unavailable or you want to refresh the cache manually later. Installers validate that packaged resources are already synced and strip runtime artifacts/caches from the temporary copy before replacing an installed package. Do not edit `${CODEX_HOME:-$HOME/.codex}/plugins/cache/...` directly; it is generated Codex state.

Install the Codex skill directly for sessions that still load user skills without plugin installation:

```bash
scripts/install-codex-skill.sh
```

The direct skill installer copies from `codex-plugin/agent-collab/skills/agent-collab` and defaults to the documented user skill path: `$HOME/.agents/skills/agent-collab`.
Use `scripts/install-codex-skill.sh --dry-run` to validate the source and destination without copying.

Install the Claude plugin as a skills-directory plugin:

```bash
scripts/install-claude-plugin.sh
```

The Claude installer defaults to `$HOME/.claude/skills/agent-collab`. It copies the packaged plugin source, including `.claude-plugin/plugin.json`, `skills/agent-collab/SKILL.md`, helper agents, and bundled runtime resources.

The repository intentionally does not keep `.agents/`, `.codex/`, `.claude/`, or `codex-skill/`, so host-local configuration and legacy duplicate package paths do not leak into source control.

Restart Codex or Claude Code if a running session does not pick up changed skill or agent files.

Verify:

```bash
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python -m py_compile \
  "$repo_root/tools/agent-collab/scripts/host.py" \
  "$repo_root/tools/agent-collab/scripts/peer.py" \
  "$repo_root/tools/agent-collab/scripts/snapshot.py"
for f in "$repo_root"/tools/agent-collab/schemas/*.schema.json; do python -m json.tool "$f" >/dev/null; done
```

## Install Into Another Repo

Install or refresh the Codex and Claude packages from this repository:

```bash
scripts/install-codex-plugin.sh --dry-run
scripts/install-codex-skill.sh
scripts/install-claude-plugin.sh
```

Do not copy `.codex/`, `.claude/`, `.agents/`, or `codex-skill/` from this repository into another repo. Those directories are active host configuration or legacy generated package paths, not Agent Collab package source.

## Runtime Configuration

Agent Collab can be configured with setup:

```bash
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
python "$repo_root/tools/agent-collab/scripts/host.py" setup
python "$repo_root/tools/agent-collab/scripts/host.py" setup --scope global
python "$repo_root/tools/agent-collab/scripts/host.py" setup --reset local
python "$repo_root/tools/agent-collab/scripts/host.py" setup --reset all
```

`setup` writes Agent Collab settings only. It does not rewrite normal Codex or Claude global configuration. In this source checkout, local settings are stored at `tools/agent-collab/settings.local.json` and ignored by Git. Installed package local settings are stored under the runtime data root described in Run Artifacts, not in plugin code directories. Global Agent Collab settings are stored at `$AGENT_COLLAB_HOME/settings.json` when `AGENT_COLLAB_HOME` is set; otherwise `$XDG_CONFIG_HOME/agent-collab/settings.json`, falling back to `~/.config/agent-collab/settings.json`.

Settings precedence:

```text
environment variables > local Agent Collab settings > global Agent Collab settings > built-in defaults
```

Automation examples:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" setup --scope local --no-input \
  --codex-model gpt-5.5 \
  --claude-model opus \
  --web-research live \
  --codex-config model_verbosity=\"high\" \
  --timeout-seconds 3600 \
  --history-retained-runs 25

python "$repo_root/tools/agent-collab/scripts/host.py" setup --scope global --no-input \
  --wait-until-response \
  --print-env

python "$repo_root/tools/agent-collab/scripts/host.py" setup --clear-history --dry-run --no-input
python "$repo_root/tools/agent-collab/scripts/host.py" setup --clear-history --yes --no-input
```

`history_retained_runs` defaults to `50` and controls how many terminal run artifact directories cleanup keeps. Set it with `setup --history-retained-runs N`; `0` means cleanup removes all terminal history while preserving active runs. `setup --clear-history` runs the same cleanup engine as `clear-history`, and requires `--yes` for deletion in non-interactive use.

Environment variables:

- `CODEX_AGENT_COLLAB_MODEL`: Codex peer model. Defaults to `gpt-5.5`.
- `CODEX_AGENT_COLLAB_EFFORT`: Codex peer reasoning effort: `minimal`, `low`, `medium`, `high`, or `xhigh`. Defaults to `xhigh`.
- `AGENT_COLLAB_WEB_RESEARCH`: Shared web research capability: `live`, `cached`, or `disabled`. Defaults to `live`.
- `CODEX_AGENT_COLLAB_CONFIG`: JSON list of additional Codex `key=value` config entries passed as repeated `codex exec -c key=value` flags before Agent Collab's required overrides.
- `CLAUDE_AGENT_COLLAB_MODEL`: Claude peer model. Defaults to `opus`.
- `CLAUDE_AGENT_COLLAB_EFFORT`: Claude peer effort. Defaults to `max`.
- `CLAUDE_AGENT_COLLAB_TOOLS`: Claude peer tool access. `default` uses Claude Code's default/full tool set; custom values are passed through Claude Code `--tools`. When web research is enabled, custom lists automatically include `WebSearch` and `WebFetch`.
- `CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD`: Deprecated compatibility variable. Agent Collab does not pass Claude Code's `--max-budget-usd` flag.
- `CLAUDE_AGENT_COLLAB_MAX_TURNS`: Optional Claude peer turn cap. Defaults to `50` when supported by the local CLI.
- `AGENT_COLLAB_CLAUDE_ASSUME_FLAGS`: Set to `1`, `true`, or `yes` to treat documented Claude flags as supported without probing `claude --help`.
- `AGENT_COLLAB_HISTORY_RETAINED_RUNS`: Number of terminal run artifact directories cleanup keeps. Defaults to `50`; use `0` to clear all terminal history when cleanup runs.
- `AGENT_COLLAB_TIMEOUT_SECONDS`: Peer subprocess timeout. Defaults to `2700` seconds, or 45 minutes. Positive values below `2700` seconds are raised to `2700` so every agent run gets at least 45 minutes. The general rule is to wait until the peer responds; use `0` only when you intentionally want no subprocess timeout. This does not change Codex's background terminal polling model.
- `AGENT_COLLAB_SAFE_MODE`: Opt into reduced-permission peer runs with `1`, `true`, `yes`, or `on`.
- `AGENT_COLLAB_CODEX_APPROVAL_FLAG`: Force Codex approval flag detection with `ask` or `bypass`.

## Manual Runtime

Start a run:

```bash
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
brief_file=$(mktemp)
printf '%s\n' "Review the current diff for correctness, security, and missing tests." > "$brief_file"
python "$repo_root/tools/agent-collab/scripts/host.py" start \
  --host codex \
  --target "current diff" \
  --brief-file "$brief_file"
```

`--mode` is optional. Supply it only to override auto-selection with one of `review`, `research`, `design`, `plan`, or `debug`.

Write `host-first-pass.json` in the returned run directory before reading `peer-report.json`, then finish. `finish` is the normal synchronization point because it waits internally at a short cadence and returns as soon as peer artifacts are ready:

```json
{
  "schema_version": "1.0",
  "run_id": "agent-collab-run-id",
  "summary": "Host first-pass summary written before reading peer output.",
  "claims": [
    {
      "claim": "The host completed independent analysis before reading peer output.",
      "status": "confirmed",
      "evidence": "Host notes were written before opening peer-report.json"
    }
  ]
}
```

Each claim must use `claim`, `status`, and `evidence`. `status` must be one of `confirmed`, `plausible_unverified`, `rejected`, `product_decision`, or `needs_human_input`. `evidence` must be one string; join multiple evidence items with `; `. Do not use `id` or `type` as substitutes, and do not make `evidence` an array.

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" finish "$run_dir"
```

Inspect active and recent jobs:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" status
python "$repo_root/tools/agent-collab/scripts/host.py" status "$run_id" --wait
```

`status --wait` is for manual inspection and debugging. It is not the default Codex orchestration path because long external poll intervals can notice completion late, while short repeated status calls are noisy.

Show the complete stored output for a run:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" result "$run_id"
```

Clear old terminal run history:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" clear-history --dry-run
python "$repo_root/tools/agent-collab/scripts/host.py" clear-history --retain 25 --yes
python "$repo_root/tools/agent-collab/scripts/host.py" clear-history --run "$run_id" --yes
```

`clear-history` deletes only direct run artifact directories under the configured run root. It preserves active `running` and `starting` runs, removes matching jobs from `state.json`, and supports `--all` when you want to clear every terminal run.

Cancel a long-running peer:

```bash
run_id=$(python "$repo_root/tools/agent-collab/scripts/host.py" status | python -c 'import json,sys; print(json.load(sys.stdin)["jobs"][0]["id"])')
python "$repo_root/tools/agent-collab/scripts/host.py" cancel "$run_id"
```

For a live peer, early cancellation before the minimum wait is refused by default. If the user explicitly asks to stop before the floor, it requires `--force-before-min-wait --reason USER_REQUESTED_STOP`.

Check local prerequisites without installing anything:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" doctor
```

`doctor` checks Agent Collab settings, effective `web_research`, Claude web-tool support, Codex `web_search` config support, schemas, timeout floor, Codex and Claude availability, Codex `doctor --json`, Claude `auth status --json`, and required peer-launch flag support. Claude flag diagnostics report both effective documented support and whether each flag appears in `claude --help`.

## Maintenance

Use this loop for changes:

```bash
scripts/sync-packages.sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" codex-plugin/agent-collab/skills/agent-collab
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/validate_plugin.py" codex-plugin/agent-collab
scripts/install-codex-plugin.sh --dry-run
scripts/install-codex-skill.sh --dry-run
scripts/install-claude-plugin.sh --dry-run
```

After installing, start a fresh Codex process or a new thread before live validation so it loads the refreshed skill instead of a stale in-memory copy.

## Caveats

- Codex is distributed as a Codex plugin with a direct skill installer for compatibility; Claude is distributed as a Claude Code plugin package.
- Default full-access modes are powerful and should be used only in trusted local environments.
- The automated tests mock peer CLI execution. They verify command construction, schemas, guards, metadata, and artifact behavior; they do not prove live authenticated Claude-to-Codex or Codex-to-Claude execution.
- The local Codex CLI observed during development was `codex-cli 0.140.0`, which does not expose `--ask-for-approval` on `codex exec`. The runtime feature-detects support and falls back to `--dangerously-bypass-approvals-and-sandbox` when needed.
