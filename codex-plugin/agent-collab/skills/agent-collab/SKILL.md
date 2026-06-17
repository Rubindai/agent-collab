---
name: agent-collab
description: Use for high-stakes repo-grounded collaboration where an independent Claude peer plus Codex host-local agents should verify, critique, research, review, audit, debug, judge, or challenge the current work. Trigger for code review, security audit, plan critique, repo-grounded architecture brainstorming, technical design ideation, design debate, architecture tradeoffs, migration analysis, debugging, test strategy, risky implementation verification, or requests for Claude/Codex cross-checking. Avoid for casual brainstorming, naming, simple idea generation, simple edits, routine explanations, formatting-only changes, and low-risk questions where a second agent adds little value.
---

# Agent Collab

Use this skill when independent cross-product verification materially improves confidence.

## Host Role

When Codex invokes `$agent-collab` or this skill implicitly, Codex is the host, final judge, and synthesizer. Claude is the cross-agent peer.

Default to `profile=ultra`. Do not edit files unless the user explicitly asks to implement, fix, apply, modify, or update files. Set `edit_allowed=false` unless edits are explicit.

## Flow

1. Classify `mode`, `target`, `profile`, and `edit_allowed`. Default `profile` to `ultra`.
2. Resolve the repository root with `repo_root=$(git rev-parse --show-toplevel)`.
3. Resolve this skill directory from the first existing candidate:

```bash
repo_root=$(git rev-parse --show-toplevel)
for candidate in \
  "$HOME/.agents/skills/agent-collab" \
  "${CODEX_HOME:-$HOME/.codex}/skills/agent-collab" \
  "$repo_root/codex-plugin/agent-collab/skills/agent-collab" \
  "$repo_root/tools/agent-collab"
do
  if [ -f "$candidate/scripts/host.py" ]; then
    skill_dir="$candidate"
    break
  fi
done
test -n "${skill_dir:-}"
```

4. Before doing host analysis, write a neutral brief. Decide what the peer needs, keep the prompt natural, minimal, and non-leading, and define the desired outcome, success criteria, scope, and hard constraints.
5. Ask the peer to use latest official documentation for external/API/platform/dependency/tooling claims and to research online extensively when current external facts could affect the answer. Prefer official sources and source-backed evidence.
6. Do not include host analysis, suspected findings, preferred conclusions, detailed reasoning, implementation defense, or another agent's findings.
7. Start the peer before host analysis:

```bash
python "$skill_dir/scripts/host.py" start \
  --host codex \
  --mode "$mode" \
  --target "$target" \
  --brief-file "$brief_file" \
  --profile "$profile"
```

Add `--edit-allowed` only when the user explicitly delegated edits.

8. While the peer runs, do independent host analysis. In `ultra`, use available host-local Codex subagents for independent lenses when useful; if named Agent Collab helper agents are not installed, use built-in Codex agents with lens-specific prompts for mapping, review, research, architecture, security, debugging, test strategy, and verification. Give each subagent only the neutral brief and its lens. Do not call `status --wait` during independent host analysis; repeated status polling is not part of the normal flow.
9. Do not read `peer-report.json` until independent host work is complete. Write `host-first-pass.json` first:

```json
{
  "schema_version": "1.0",
  "run_id": "agent-collab-run-id",
  "summary": "Host first-pass summary written before reading peer output.",
  "claims": []
}
```

10. Finish the run. `finish` is the normal synchronization point after `host-first-pass.json`; it waits responsively for peer artifacts, normalizes the report, and returns without repeated Codex-visible status polling:

```bash
python "$skill_dir/scripts/host.py" finish "$run_dir"
```

11. In `ultra`, use a host-local advisory adjudicator after the host first pass, peer report, helper reports, and claim matrix exist. The adjudicator is advisory only and must not call Claude, Codex peer commands, or Agent Collab.
12. Verify important claims yourself and synthesize the final answer using `"$skill_dir/references/synthesize.md"`.

Useful runtime helpers:

```bash
python "$skill_dir/scripts/host.py" setup
python "$skill_dir/scripts/host.py" setup --scope global
python "$skill_dir/scripts/host.py" setup --reset local
python "$skill_dir/scripts/host.py" status
python "$skill_dir/scripts/host.py" status "$run_id" --wait
python "$skill_dir/scripts/host.py" result "$run_id"
python "$skill_dir/scripts/host.py" clear-history --dry-run
python "$skill_dir/scripts/host.py" cancel RUN_ID
python "$skill_dir/scripts/host.py" doctor
```

Use `setup` to configure local or global Agent Collab peer defaults and reset them when needed, including web research capability, Codex config overrides, Claude tool access, safe mode, timeouts, and history retention. Use `status` and `status --wait` only for manual inspection and debugging, `result` to retrieve complete stored artifacts, `cancel RUN_ID` only when the user asks to stop a specific run, and `doctor` to check Agent Collab/Codex/Claude readiness without installing anything. Use `clear-history` to remove old terminal run artifacts; active runs are preserved by default.

## Request Modes

Allowed modes: `review`, `audit`, `brainstorm`, `research`, `design`, `plan`, `plan-critique`, `debug`, `migration`, `test-strategy`, `verify`, `implement`.

## Guardrails

Cross-agent depth is capped at 1. Local subagent depth is capped at 1 by default. Peer runs receive full repo, shell, tool, and network capability by default, but the runtime prepends a PATH guard that blocks the peer from calling the host CLI.

Full capability is for investigation and validation. It is not blanket permission to mutate the repo.

Peer output is structured by schema first. The runtime preserves `peer.raw.json`, writes `peer-normalization.json`, and can recover a schema-valid JSON report from a Claude `result` string that contains prose before the report.
