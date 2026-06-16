# Agent Collab

Agent Collab is a repo-local skill pair that lets Codex and Claude Code cross-check each other on review, audit, design, debugging, migration, test strategy, and verification work.

This repository is intentionally skill-first, not plugin-first. It installs by placing product-specific skill files in the locations that Codex and Claude already scan, plus shared runtime files that this specific skill needs.

## What Is Included

```text
.agents/skills/agent-collab/        Codex repo-local skill
.claude/skills/agent-collab/        Claude Code repo-local skill
.codex/agents/                      Codex host-local helper agents
.claude/agents/                     Claude host-local helper agents
tools/agent-collab/                 Shared peer runtime, schema, and contracts
tests/test_agent_collab_runtime.py  Runtime and metadata tests
```

The shared runtime is [tools/agent-collab/agent-collab-peer.py](tools/agent-collab/agent-collab-peer.py). It reads a request JSON file, builds a strict peer prompt, calls the other product once, validates structured JSON output, and checks whether a no-edit peer run changed the working tree.

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
3. The host produces a first pass before reading peer output.
4. The host may use same-product local helper agents for bounded read-heavy work.
5. The host writes a request JSON file.
6. The shared runtime invokes the opposite product once.
7. The host verifies important peer claims.
8. The host snapshots git state again.
9. The host synthesizes the final answer, separating confirmed, unverified, rejected, product-decision, and human-input-needed claims.

The peer is contractually forbidden from invoking Agent Collab again, calling the host product, or editing files when `edit_allowed=false`.

## Install In This Repo

Clone the repository and start Codex or Claude Code in it:

```bash
git clone git@github.com:Rubindai/agent-collab.git
cd agent-collab
```

If Codex or Claude Code was already running before these directories existed, restart the session if the new skill or agent files do not appear automatically.

Verify the install:

```bash
repo_root=$(git rev-parse --show-toplevel)
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
bash -n "$repo_root/tools/agent-collab/git-snapshot.sh"
python -m json.tool "$repo_root/tools/agent-collab/peer-report.schema.json" >/dev/null
```

## Install Into Another Repo

For a plain Codex skill, the minimum repo-local install shape is `.agents/skills/<skill-name>/SKILL.md`. For a plain Claude Code skill, the minimum repo-local install shape is `.claude/skills/<skill-name>/SKILL.md`.

Agent Collab needs more than those two `SKILL.md` files because the skills call a shared runtime and use optional same-product helper agents. Install the full package as follows.

From a checkout of this repository, copy the implementation into the target repo:

```bash
target=/path/to/target-repo
rsync -a .agents .claude tools "$target"/
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

This README documents repo-local installation. User-level/global installation is intentionally not covered because Agent Collab's runtime currently assumes the shared `tools/agent-collab/` directory is present in the target repository.

## Upgrade

In this repository:

```bash
git pull --ff-only
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

In another repo where Agent Collab was copied in, pull this repository first, then repeat the copy steps from "Install Into Another Repo". Merge `.codex/config.toml`; do not blindly overwrite a target repo's existing Codex config.

After upgrading, restart active Codex or Claude Code sessions if they do not pick up changed skill or agent files.

## Uninstall

Remove the Agent Collab files:

```bash
rm -rf .agents/skills/agent-collab
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
- `CLAUDE_TOOLS`: Claude peer tool set. Defaults to `default`.
- `AGENT_COLLAB_SAFE_MODE=1`: Use read-only Codex sandbox and Claude `plan` permission mode.
- `AGENT_COLLAB_TIMEOUT_SECONDS`: Optional peer timeout. Unset or `0` means no hard timeout.
- `AGENT_COLLAB_PEER_ONLY=true` or `AGENT_COLLAB_DEPTH>=1`: Refuse nested invocation.

Default mode is full-capability because this repo was built for high-trust local collaboration. Use safe mode for untrusted repositories.

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
- The official Codex CLI reference documents `--ask-for-approval never`, but the local Codex CLI installed here is `codex-cli 0.139.0` and does not expose that flag. The runtime feature-detects support: newer Codex installs use `--ask-for-approval never`, while this local version falls back to `--dangerously-bypass-approvals-and-sandbox` for default full-permission Codex peer runs.
- Current Claude docs list `--max-turns`, but this runtime does not depend on it. Add feature detection before using it so older Claude installs do not break.
- `.codex/config.toml` can affect the whole target repo. Merge it carefully if the target repo already has Codex configuration.

## References

- Codex skills: https://developers.openai.com/codex/skills
- Codex subagents: https://developers.openai.com/codex/subagents
- Codex non-interactive mode: https://developers.openai.com/codex/noninteractive
- Claude skills: https://code.claude.com/docs/en/skills
- Claude subagents: https://code.claude.com/docs/en/sub-agents
- Claude CLI reference: https://code.claude.com/docs/en/cli-reference
