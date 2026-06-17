---
name: agent-collab
description: Run bidirectional repo-grounded collaboration between Claude Code and Codex when independent peer verification, judging, or synthesis support would improve confidence.
when_to_use: Use for code review, security audit, plan critique, repo-grounded architecture brainstorming, technical design ideation, design critique, architecture debate, architecture tradeoffs, research, migration analysis, debugging, test strategy, implementation verification, or when the user asks for a second opinion, cross-check, peer review, audit, Claude+Codex collaboration, judge, adjudicator, or independent verifier. Avoid for casual brainstorming, naming, simple idea generation, simple edits, routine Q&A, formatting-only changes, and low-risk questions where a second agent is unnecessary.
argument-hint: "[mode] [target or brief]"
model: opus
effort: max
allowed-tools: Bash Read Grep Glob WebSearch WebFetch Edit Write Agent Task
---

# Agent Collab

Use this skill when independent cross-product verification materially improves confidence.

## Host Role

When Claude invokes `/agent-collab:agent-collab` or this skill implicitly, Claude is the host, final judge, and synthesizer. Codex is the cross-agent peer.

Default to `profile=ultra`. Do not edit files unless the user explicitly asks to implement, fix, apply, modify, or update files. Set `edit_allowed=false` unless edits are explicit.

## Flow

1. Classify `mode`, `target`, `profile`, and `edit_allowed`. Default `profile` to `ultra`.
2. Resolve the repository root with `repo_root=$(git rev-parse --show-toplevel)`.
3. Resolve the bundled Agent Collab runtime from the first existing candidate:

```bash
repo_root=$(git rev-parse --show-toplevel)
skill_dir="${CLAUDE_SKILL_DIR:-}"

for candidate in \
  "$skill_dir" \
  "$repo_root/claude-plugin/agent-collab/skills/agent-collab" \
  "$repo_root/tools/agent-collab"
do
  if [ -f "$candidate/scripts/host.py" ]; then
    runtime_root="$candidate"
    break
  fi
done
test -n "${runtime_root:-}"
```

4. Before doing host analysis, write a neutral brief. Decide what the peer needs, keep the prompt natural, minimal, and non-leading, and define the desired outcome, success criteria, scope, and hard constraints.
5. Ask the peer to use latest official documentation for external/API/platform/dependency/tooling claims and to research online extensively when current external facts could affect the answer. Prefer official sources and source-backed evidence.
6. Do not include host analysis, suspected findings, preferred conclusions, detailed reasoning, implementation defense, or another agent's findings.
7. Start the peer before host analysis:

```bash
python "$runtime_root/scripts/host.py" start \
  --host claude \
  --mode "$mode" \
  --target "$target" \
  --brief-file "$brief_file" \
  --profile "$profile"
```

Add `--edit-allowed` only when the user explicitly delegated edits.

8. While the peer runs, do independent host analysis. In `ultra`, spawn Claude-local subagents for independent lenses when useful: mapper, reviewer, researcher, architect, security-auditor, debugger, test-strategist, verifier. Give each subagent only the neutral brief and its lens. Tell every helper: "Do not invoke Agent Collab, `$agent-collab`, `/agent-collab`, host/peer CLIs, or cross-product peer commands." Do not call `status --wait` during independent host analysis; repeated status polling is not part of the normal flow.
9. Do not read `peer-report.json` until independent host work is complete. Write `host-first-pass.json` first:

```json
{
  "schema_version": "1.0",
  "run_id": "agent-collab-run-id",
  "summary": "Host first-pass summary written before reading peer output.",
  "claims": []
}
```

10. Finish the run. `finish` is the normal synchronization point after `host-first-pass.json`; it waits responsively for peer artifacts, normalizes the report, and returns without repeated status polling:

```bash
python "$runtime_root/scripts/host.py" finish "$run_dir"
```

11. In `ultra`, use a Claude-local advisory adjudicator after the host first pass, peer report, helper reports, and claim matrix exist. The adjudicator is advisory only and must not call Codex, cross-agent peer commands, or Agent Collab. If no adjudicator artifact is supplied, `finish` writes an `advisory_pending` marker.
12. Verify important claims yourself and synthesize the final answer using `"$runtime_root/references/synthesize.md"`.

Useful runtime helpers:

```bash
python "$runtime_root/scripts/host.py" setup
python "$runtime_root/scripts/host.py" setup --scope global
python "$runtime_root/scripts/host.py" setup --reset local
python "$runtime_root/scripts/host.py" status
python "$runtime_root/scripts/host.py" status "$run_id" --wait
python "$runtime_root/scripts/host.py" result "$run_id"
python "$runtime_root/scripts/host.py" clear-history --dry-run
python "$runtime_root/scripts/host.py" cancel RUN_ID
python "$runtime_root/scripts/host.py" doctor
```

Use `setup` to configure local or global Agent Collab peer defaults and reset them when needed, including web research capability, Codex config overrides, Claude tool access, safe mode, timeouts, and history retention. Use `status` and `status --wait` only for manual inspection and debugging, `result` to retrieve complete stored artifacts, `cancel RUN_ID` only when the user asks to stop a specific run, and `doctor` to check Agent Collab/Codex/Claude readiness without installing anything. Use `clear-history` to remove old terminal run artifacts; active runs are preserved by default.

In this source repo, run artifacts and local settings live under `tools/agent-collab/`. Installed packages write runtime state under the Agent Collab data root (`AGENT_COLLAB_STATE_HOME`, Claude plugin data when provided, Codex home for plugin-cache installs, or XDG state fallback), not into plugin code directories.

## Request Modes

Allowed modes: `review`, `audit`, `brainstorm`, `research`, `design`, `plan`, `plan-critique`, `debug`, `migration`, `test-strategy`, `verify`, `implement`.

## Guardrails

Cross-agent depth is capped at 1. Local subagent depth is capped at 1 by default. Peer runs receive full repo, shell, tool, and network capability by default, and no-edit requests are enforced by prompt plus post-run git mutation detection. Use safe mode when technical read-only restrictions are required. The runtime prepends a PATH guard that blocks ordinary unqualified host CLI lookup, but it is not a sandbox and cannot block absolute host CLI paths or deliberate PATH rewriting.

Full capability is for investigation and validation. It is not blanket permission to mutate the repo.

Peer output is structured by schema first. The runtime preserves `peer.raw.json`, writes `peer-normalization.json`, and can recover a schema-valid JSON report from a Claude `result` string that contains prose before the report.
