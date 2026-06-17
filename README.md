# Agent Collab

Agent Collab is a repo-local collaboration workflow for Codex and Claude Code. It starts the opposite product as an independent peer first, lets the host do its own unpolluted analysis in parallel, then uses host-owned judging and synthesis to produce the final answer.

The default profile is `ultra`: maximum capability, full repo/tool/network access, peer-first execution, host-local helper agents, and an advisory adjudicator.

## What It Does

Use Agent Collab for high-stakes work where a second independent AI pass is worth the cost:

- code review, security audit, and risky implementation verification
- plan critique, design debate, and architecture tradeoffs
- debugging, migrations, research, and test strategy
- any task where the user asks for Claude+Codex cross-checking

The host remains the final decision maker. Peer, helper, and adjudicator reports are evidence, not authority.

## Repo Layout

```text
codex-skill/agent-collab/           Packaged Codex skill source for global install
.claude/skills/agent-collab/        Claude Code repo-local skill
.codex/config.toml                  Codex max-capability defaults
.codex/agents/                      Codex host-local helper and adjudicator agents
.claude/agents/                     Claude host-local helper and adjudicator agents
tools/agent-collab/                 Shared runtime, prompts, schemas, and run artifacts
scripts/                            Sync and install helpers
tests/test_agent_collab_runtime.py  Runtime and metadata tests
```

The shared runtime follows the standard skill resource shape:

```text
tools/agent-collab/
  scripts/      host and peer runtime entrypoints
  references/   peer-only and synthesis prompt contracts
  schemas/      structured request/report schemas
  runs/         local run artifacts, ignored except .gitkeep
```

Runtime entrypoints:

- `tools/agent-collab/scripts/host.py`: creates run directories, snapshots git state, starts the peer first, and validates finish artifacts.
- `tools/agent-collab/scripts/peer.py`: invokes the opposite product once and returns schema-validated JSON.

The packaged Codex skill has the same `scripts/`, `references/`, and `schemas/` directories at `codex-skill/agent-collab/`. It does not contain a nested `tools/agent-collab/` compatibility copy.

When you modify `tools/agent-collab/`, run `scripts/sync-codex-skill.sh` so the packaged Codex skill copy stays in sync.

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
/agent-collab review the current diff
```

Claude may also invoke the skill implicitly when the request matches its description and `when_to_use` metadata. The Claude skill intentionally does not set `disable-model-invocation: true`.

## Workflow

1. The host classifies `mode`, `target`, `profile`, and `edit_allowed`. `profile` defaults to `ultra`.
2. The host writes a neutral brief before host analysis begins.
3. The host starts the peer immediately with `scripts/host.py start`.
4. The peer receives only the neutral brief, target, constraints, edit policy, and output schema.
5. While the peer runs, the host performs independent analysis.
6. All agents use latest official documentation for external/API/platform/dependency/tooling claims and research online extensively when current external facts could affect the answer.
7. In `ultra`, the host uses local helper agents for independent lenses such as mapper, reviewer, researcher, architect, security-auditor, debugger, test-strategist, and verifier.
8. Do not read peer output until independent host work is complete. The host writes `host-first-pass.json` before reading `peer-report.json`.
9. The host validates `peer-report.json`, builds a claim matrix, and runs an advisory adjudicator.
10. The host verifies high-value claims and writes the final synthesis.

Independence rule:

```text
neutral brief -> launch peer -> independent host analysis -> host first pass -> read peer -> adjudicate -> synthesize
```

Peer prompts are intentionally natural and non-leading. For review and audit, the peer is asked for an independent review of the target, not a long list of host suspicions.

## Run Artifacts

Runs are written under `tools/agent-collab/runs/<run-id>/` and ignored by Git except `.gitkeep`.

Important files:

```text
host-request.json       Neutral request passed to the peer
before.snapshot         Git snapshot before peer work
peer-process.json       Peer PID and run metadata
host-first-pass.json    Host analysis written before reading peer output
peer.raw.json           Raw Claude/Codex CLI output envelope for debugging
peer-report.json        Schema-validated peer report or structured peer failure
claim-matrix.json       Host and peer claims grouped for verification
adjudicator-report.json Advisory judge output or pending marker
after.snapshot          Git snapshot after finish
```

## Permissions

Agent Collab defaults to full capability because it is designed for high-trust local repositories.

Codex defaults in `.codex/config.toml`:

```toml
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_verbosity = "high"
sandbox_mode = "danger-full-access"
approval_policy = "never"
web_search = "live"

[agents]
max_threads = 8
max_depth = 1
# 45 minutes; Agent Collab agent timeouts must stay at or above 2700 seconds.
job_max_runtime_seconds = 2700
```

Claude peer defaults:

```text
--model opus
--effort max
--permission-mode bypassPermissions
--tools default
--no-session-persistence
```

Full capability is for investigation and validation. It is not blanket permission to mutate. Peer and helper agents are still instructed not to edit unless `edit_allowed=true` and the user explicitly delegated edits.

Peer runs use structured output as the machine interface. Claude peers return a JSON envelope; the runtime preserves that raw envelope as `peer.raw.json`, normalizes `structured_output` into `peer-report.json`, and validates the normalized report against `schemas/peer-report.schema.json`.

Use safe mode for untrusted repos:

```bash
AGENT_COLLAB_SAFE_MODE=1
```

Safe mode uses Codex read-only sandboxing and Claude plan permissions for peer runs.

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

The runtime sets guard environment variables and prepends a run-local `bin/` directory to `PATH` so a peer cannot call the host CLI. Local subagent depth is capped by prompt and agent configuration; Claude helper agents disallow nested `Task`, and Codex config keeps `agents.max_depth = 1`.

## Install In This Repo

Install the Codex skill globally:

```bash
scripts/install-codex-skill.sh
```

The installer defaults to the documented user skill path: `$HOME/.agents/skills/agent-collab`.

The repository intentionally does not keep `.agents/skills/agent-collab`, so Codex will not see both a global and repo-local skill with the same name.

Restart Codex or Claude Code if a running session does not pick up changed skill or agent files.

Verify:

```bash
repo_root=$(git rev-parse --show-toplevel)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python -m py_compile \
  "$repo_root/tools/agent-collab/scripts/host.py" \
  "$repo_root/tools/agent-collab/scripts/peer.py"
for f in "$repo_root"/tools/agent-collab/schemas/*.schema.json; do python -m json.tool "$f" >/dev/null; done
```

## Install Into Another Repo

Install or refresh the global Codex skill:

```bash
scripts/install-codex-skill.sh
```

Copy Claude/runtime files into the target repo:

```bash
target=/path/to/target-repo
rsync -a .claude tools "$target"/
```

Copy Codex helper agents and merge config:

```bash
mkdir -p "$target/.codex/agents"
cp .codex/agents/agent-collab-*.toml "$target/.codex/agents/"
```

If the target repo already has `.codex/config.toml`, merge the Agent Collab settings instead of overwriting it.

## Runtime Configuration

Environment variables:

- `CODEX_AGENT_COLLAB_MODEL`: Codex peer model. Defaults to `gpt-5.5`.
- `CODEX_AGENT_COLLAB_EFFORT`: Codex peer reasoning effort. Defaults to `xhigh`.
- `CODEX_AGENT_COLLAB_WEB_SEARCH`: Codex peer web search mode passed as `-c web_search=...`. Defaults to `live`.
- `CLAUDE_AGENT_COLLAB_MODEL`: Claude peer model. Defaults to `opus`.
- `CLAUDE_AGENT_COLLAB_EFFORT`: Claude peer effort. Defaults to `max`.
- `CLAUDE_AGENT_COLLAB_TOOLS`: Claude peer tool set. Defaults to `default`.
- `CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD`: Optional Claude peer budget cap. Defaults to `25.00` when supported by the local CLI.
- `CLAUDE_AGENT_COLLAB_MAX_TURNS`: Optional Claude peer turn cap. Defaults to `50` when supported by the local CLI.
- `AGENT_COLLAB_TIMEOUT_SECONDS`: Peer timeout. Defaults to `2700` seconds, or 45 minutes. Positive values below `2700` seconds are raised to `2700` so every agent run gets at least 45 minutes. The general rule is to wait until the peer responds; use `0` only when you intentionally want no subprocess timeout.
- `AGENT_COLLAB_SAFE_MODE=1`: Opt into reduced-permission peer runs.
- `AGENT_COLLAB_CODEX_APPROVAL_FLAG`: Force Codex approval flag detection with `ask` or `bypass`.

## Manual Runtime

Start a run:

```bash
repo_root=$(git rev-parse --show-toplevel)
brief_file=$(mktemp)
printf '%s\n' "Review the current diff for correctness, security, and missing tests." > "$brief_file"
python "$repo_root/tools/agent-collab/scripts/host.py" start \
  --host codex \
  --mode review \
  --target "current diff" \
  --brief-file "$brief_file"
```

Write `host-first-pass.json` in the returned run directory before reading `peer-report.json`, then finish:

```bash
python "$repo_root/tools/agent-collab/scripts/host.py" finish "$run_dir"
```

## Maintenance

Use this loop for changes:

```bash
scripts/sync-codex-skill.sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
python /home/rubin/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skill/agent-collab
scripts/install-codex-skill.sh --codex-home-path
```

After installing, start a fresh Codex process before live validation so it loads the refreshed skill instead of a stale in-memory copy.

## Caveats

- This is skill-first, not plugin-packaged yet.
- Default full-access modes are powerful and should be used only in trusted local environments.
- The automated tests mock peer CLI execution. They verify command construction, schemas, guards, metadata, and artifact behavior; they do not prove live authenticated Claude-to-Codex or Codex-to-Claude execution.
- The local Codex CLI observed during development was `codex-cli 0.140.0`, which does not expose `--ask-for-approval` on `codex exec`. The runtime feature-detects support and falls back to `--dangerously-bypass-approvals-and-sandbox` when needed.
