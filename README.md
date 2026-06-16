# Agent Collab

Agent Collab is a Codex global skill plus a Claude Code repo-local skill that lets the two products cross-check each other on review, audit, design, debugging, migration, test strategy, and verification work.

This repository is intentionally skill-first, not plugin-first. Codex is installed globally under `$CODEX_HOME/skills` or `~/.codex/skills`; Claude Code is installed repo-locally under `.claude/skills`.

## What Is Included

```text
codex-skill/agent-collab/           Packaged Codex skill source for global install
.claude/skills/agent-collab/        Claude Code repo-local skill
.codex/agents/                      Codex host-local helper agents
.claude/agents/                     Claude host-local helper agents
tools/agent-collab/                 Shared peer runtime, schema, and contracts
scripts/                            Sync and install helpers
tests/test_agent_collab_runtime.py  Runtime and metadata tests
```

The shared runtime is [tools/agent-collab/agent-collab-peer.py](tools/agent-collab/agent-collab-peer.py). The global Codex skill bundles the same runtime under its skill directory so Codex does not need a repo-local `.agents/skills` install. The runtime reads a request JSON file, builds a strict peer prompt, calls the other product once, validates structured JSON output, and checks whether a no-edit peer run changed the working tree.

When you modify files in `tools/agent-collab/`, run [scripts/sync-codex-skill.sh](scripts/sync-codex-skill.sh) before testing or installing. The test suite checks that the packaged runtime stays in sync with the canonical root runtime.

## Requirements

- Git repository.
- Python 3.10 or newer.
- Codex CLI installed and authenticated for Codex-peer runs.
- Claude Code CLI installed and authenticated for Claude-peer runs.
- Start Codex or Claude from the repository root or a subdirectory inside the repository. The skill resolves the repository root with Git before invoking shared runtime files.

No Python package dependencies are required.

## Usage

From Codex, invoke the Codex skill explicitly:

```text
Use $agent-collab to review the current diff.
```

Codex can also invoke it implicitly for high-stakes prompts that match the skill description, such as code review, security audit, design debate, migration analysis, debugging, test strategy, or independent verification.

From Claude Code, invoke the Claude skill explicitly:

```text
/agent-collab review the current diff
```

Claude can also invoke it implicitly when the request matches the skill `description` and `when_to_use` metadata.

## How It Works

1. The host agent classifies the mode, target, and whether edits are allowed.
2. The host snapshots git state with `tools/agent-collab/git-snapshot.sh`.
3. The host writes a neutral request JSON file.
4. Start the peer run before host analysis by invoking the opposite product in the background and writing its JSON report to a temp file.
5. While the peer runs, the host performs read-only analysis and may use same-product local helper agents for bounded read-heavy work.
6. The host waits for the peer report.
7. The host verifies important peer claims.
8. The host snapshots git state again.
9. The host synthesizes the final answer, separating confirmed, unverified, rejected, product-decision, and human-input-needed claims.

Optional same-product helper agents are mapper, reviewer, and verifier. They are host-local helpers for repository inventory, focused review, and claim verification; they are instructed not to invoke Agent Collab, call the other product, recurse into more subagents, or edit files unless a bounded write scope is explicitly assigned.

There is no separate judge agent or synthesizer agent in the current implementation. The host remains the final synthesizer and uses peer/helper output as evidence to verify, reject, or qualify claims.

The peer is contractually forbidden from invoking Agent Collab again, calling the host product, or editing files when `edit_allowed=false`.
Peer prompts also ask for latest official documentation on external/API/platform/dependency/tooling claims and extensive online research when current external facts could affect the answer.
While the peer is running, host-side work also stays read-only so git-state mutation checks remain attributable.

## Install In This Repo

Clone the repository:

```bash
git clone git@github.com:Rubindai/agent-collab.git
cd agent-collab
```

Install the Codex skill globally:

```bash
scripts/install-codex-skill.sh
```

The installer defaults to the active Codex-home style path, `${CODEX_HOME:-$HOME/.codex}/skills/agent-collab`, when that skills root exists. Current public Codex docs list `$HOME/.agents/skills` as the user-scope skills directory; use `scripts/install-codex-skill.sh --docs-path` if your Codex build has moved to that documented path. Do not install both paths at the same time unless you have verified your Codex build does not scan both, because duplicate skill names can appear separately.

The repository intentionally does not keep `.agents/skills/agent-collab`, so Codex will not see both a global and repo-local skill with the same name.

If Codex or Claude Code was already running before these directories existed, restart the session if the new skill or agent files do not appear automatically.

Verify the install:

```bash
repo_root=$(git rev-parse --show-toplevel)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
bash -n "$repo_root/tools/agent-collab/git-snapshot.sh"
python -m json.tool "$repo_root/tools/agent-collab/peer-report.schema.json" >/dev/null
```

## Install Into Another Repo

For a plain Codex global skill, the minimum install shape is `${CODEX_HOME:-$HOME/.codex}/skills/<skill-name>/SKILL.md`. For a plain Codex repo-local skill, the minimum install shape is `.agents/skills/<skill-name>/SKILL.md`; Agent Collab intentionally avoids that repo-local Codex path when installed globally to prevent duplicate discovery. For a plain Claude Code skill, the minimum repo-local install shape is `.claude/skills/<skill-name>/SKILL.md`.

Agent Collab needs more than `SKILL.md` because the skills call a runtime and use optional same-product helper agents. Install the Codex side globally once, then copy the Claude/runtime files into target repos that should support Claude-hosted runs.

From a checkout of this repository, install or refresh the global Codex skill:

```bash
scripts/install-codex-skill.sh
```

Then copy the Claude skill and root runtime into the target repo:

```bash
target=/path/to/target-repo
rsync -a .claude tools "$target"/
```

Then copy the optional test suite if you want install verification in the target repo:

```bash
rsync -a tests "$target"/
```

Then merge the Codex helper-agent files:

```bash
mkdir -p "$target/.codex/agents"
cp .codex/agents/agent-collab-*.toml "$target/.codex/agents/"
```

The Codex helper agents are optional, but recommended for host-local mapping, focused review, and peer-claim verification.

If the target repo does not already have `.codex/config.toml`, copy this one:

```bash
cp .codex/config.toml "$target/.codex/config.toml"
```

If the target repo already has `.codex/config.toml`, merge this setting instead of overwriting the file:

```toml
[agents]
max_depth = 1
```

This setting is optional because Codex currently defaults `agents.max_depth` to `1`, but keeping it makes Agent Collab's no-recursion intent explicit.

Restart Codex or Claude Code if the session was already open and the new files do not appear automatically.

## Upgrade

In this repository:

```bash
git pull --ff-only
scripts/install-codex-skill.sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

In another repo where Agent Collab was copied in, pull this repository first, then repeat the copy steps from "Install Into Another Repo". Merge `.codex/config.toml`; do not blindly overwrite a target repo's existing Codex config.

After upgrading, restart active Codex or Claude Code sessions if they do not pick up changed skill or agent files.

## Uninstall

Remove the Agent Collab files:

```bash
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/agent-collab"
rm -rf .claude/skills/agent-collab
rm -rf tools/agent-collab
rm -f .codex/agents/agent-collab-*.toml
rm -f .claude/agents/agent-collab-*.md
rm -f tests/test_agent_collab_runtime.py
```

If `.codex/config.toml` only contains Agent Collab's `[agents] max_depth = 1`, remove that file too:

```bash
rm -f .codex/config.toml
```

If `.codex/config.toml` has other project settings, only remove the Agent Collab-specific setting if you no longer want it.

Restart active Codex or Claude Code sessions after uninstalling.

## Runtime Configuration

Environment variables:

- `CODEX_MODEL`: Codex peer model. Defaults to `gpt-5.5`.
- `CODEX_EFFORT`: Codex peer reasoning effort. Defaults to `xhigh`.
- `CLAUDE_MODEL`: Claude peer model. Defaults to `opus`.
- `CLAUDE_EFFORT`: Claude peer reasoning effort. Defaults to `max`.
- `CLAUDE_TOOLS`: Claude peer tool set. Defaults to `Bash,Read,Grep,Glob,WebSearch,WebFetch`.
- `AGENT_COLLAB_SAFE_MODE=1`: Use read-only Codex sandbox and Claude `plan` permission mode.
- `AGENT_COLLAB_TIMEOUT_SECONDS`: Optional peer timeout. Unset or `0` means no hard timeout.
- `AGENT_COLLAB_PEER_ONLY=true` or `AGENT_COLLAB_DEPTH>=1`: Refuse nested invocation.

Default mode is full-capability because this repo was built for high-trust local collaboration. Use safe mode for untrusted repositories.

### Access Defaults

- Codex peer runs use `--search` and default to `--sandbox danger-full-access` unless `AGENT_COLLAB_SAFE_MODE=1`.
- Claude peer runs default to `Bash,Read,Grep,Glob,WebSearch,WebFetch` through `CLAUDE_TOOLS`.
- Codex host-local helper agents inherit the parent Codex session's sandbox, approval, and live runtime overrides unless explicitly overridden in their agent files.
- Claude host-local helper agents include `Read`, `Glob`, `Grep`, `Bash`, `WebSearch`, and `WebFetch`.

Helper agents are still instructed to avoid edits and recursion. Broader tool access is for better investigation, not permission to mutate the repo.
Peer and helper prompts prefer latest official documentation and source-backed web research for current external claims.

## Maintenance

Use this loop for changes:

```bash
scripts/sync-codex-skill.sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
python /home/rubin/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skill/agent-collab
scripts/install-codex-skill.sh
```

Keep `tools/agent-collab/` as the canonical runtime. The packaged copy under `codex-skill/agent-collab/tools/agent-collab/` exists so the global Codex skill can run outside this repository.

## Manual Peer Runtime

Create a request file:

```json
{
  "origin": "codex",
  "host": "codex",
  "peer": "claude",
  "mode": "review",
  "target": "current diff",
  "brief": "Review the current diff for correctness, security, and missing tests.",
  "edit_allowed": false,
  "run_id": "agent-collab-manual-test"
}
```

Run:

```bash
repo_root=$(git rev-parse --show-toplevel)
python "$repo_root/tools/agent-collab/agent-collab-peer.py" /path/to/request.json --repo-root "$repo_root"
```

The command prints a JSON peer report or structured peer-failure JSON.

## Known Caveats

- This is not packaged as a Codex or Claude plugin yet. Install, uninstall, and upgrade are file-copy or git operations.
- The automated tests mock peer CLI execution. They verify command construction, schema handling, failure handling, no-recursion checks, metadata, and git snapshot behavior. They do not prove a live Claude-to-Codex or Codex-to-Claude run succeeds in your authenticated environment.
- The official Codex CLI reference documents `--ask-for-approval never`, but the local Codex CLI installed here is `codex-cli 0.140.0` and does not expose that flag. The runtime feature-detects support: newer Codex installs use `--ask-for-approval never`, while this local version falls back to `--dangerously-bypass-approvals-and-sandbox` for default full-permission Codex peer runs.
- Current Claude docs list `--max-turns`, but this runtime does not depend on it. Add feature detection before using it so older Claude installs do not break.
- `.codex/config.toml` can affect the whole target repo. Merge it carefully if the target repo already has Codex configuration.

## References

- Codex skills: https://developers.openai.com/codex/skills
- Codex subagents: https://developers.openai.com/codex/subagents
- Codex non-interactive mode: https://developers.openai.com/codex/noninteractive
- Claude skills: https://code.claude.com/docs/en/skills
- Claude subagents: https://code.claude.com/docs/en/sub-agents
- Claude CLI reference: https://code.claude.com/docs/en/cli-reference
