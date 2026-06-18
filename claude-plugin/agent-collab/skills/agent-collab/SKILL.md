---
name: agent-collab
description: Run bidirectional repo-grounded challenge-first collaboration between Claude Code and Codex when an independent second opinion, judging, or synthesis support would improve confidence.
when_to_use: Use for strict code review, security-sensitive review, source-backed research, technical design critique, architecture debate, architecture tradeoffs, implementation planning, debugging, risky implementation verification, or when the user asks for a second opinion, cross-check, peer review, Claude+Codex collaboration, judge, adjudicator, or independent verifier. Avoid for casual ideation, naming, simple edits, routine Q&A, formatting-only changes, and low-risk questions where a second agent is unnecessary.
argument-hint: "[target or brief]"
model: opus
effort: max
allowed-tools: Bash Read Grep Glob WebSearch WebFetch Edit Write Agent Task
---

# Agent Collab

Use this skill when independent cross-product verification materially improves confidence. Agent Collab is a challenge-first second opinion workflow, not peer delegation.

## Host Role

When Claude invokes `/agent-collab:agent-collab` or this skill implicitly, Claude is the host, final judge, and synthesizer. Codex is the cross-agent peer.

Default to `profile=ultra`. Do not edit files unless the user explicitly asks to implement, fix, apply, modify, or update files. Set `edit_allowed=false` unless edits are explicit.

## Flow

1. Let the runtime auto-select `mode` from `target` and the neutral brief unless a canonical override is necessary. Classify `target`, `profile`, and `edit_allowed`. Default `profile` to `ultra`.
2. Resolve the workspace root with `repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)` so the skill works in git and non-git directories.
3. Resolve the bundled Agent Collab runtime from the first existing candidate:

```bash
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
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
  --target "$target" \
  --brief-file "$brief_file" \
  --profile "$profile"
```

Add `--mode review`, `--mode research`, `--mode design`, `--mode plan`, or `--mode debug` only when explicitly overriding auto-selection. Add `--edit-allowed` only when the user explicitly delegated edits.

8. While the peer runs, do independent challenge-first host analysis. In `ultra`, spawn Claude-local subagents for independent lenses when useful: mapper, reviewer, researcher, architect, security-auditor, debugger, test-strategist, verifier. Give each subagent only the neutral brief and its lens. Tell every helper: "Do not invoke Agent Collab, `$agent-collab`, `/agent-collab`, host/peer CLIs, or cross-product peer commands." Do not call `status --wait` during independent host analysis; repeated status polling is not part of the normal flow.
9. Do not read `peer-report.json` until independent host work is complete. Write `host-first-pass.json` first:

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

10. Finish the run. `finish` is the normal synchronization point after `host-first-pass.json`; it waits responsively for peer artifacts, normalizes the report, and returns without repeated status polling:

```bash
python "$runtime_root/scripts/host.py" finish "$run_dir"
```

11. Do not cancel a live peer before the 2700-second minimum wait. An empty `peer-report.json` or stderr does not mean the peer is stalled while the process is alive. Do not replace Agent Collab with a direct `claude --print` fallback before the minimum wait. If the user explicitly asks to stop a specific run before the floor, use `cancel RUN_ID --force-before-min-wait --reason USER_REQUESTED_STOP`.
12. In `ultra`, use a Claude-local advisory adjudicator after the host first pass, peer report, helper reports, and claim matrix exist. The adjudicator is advisory only and must not call Codex, cross-agent peer commands, or Agent Collab. If no adjudicator artifact is supplied, `finish` writes an `advisory_pending` marker.
13. Verify important claims yourself and synthesize the final answer using `"$runtime_root/references/synthesize.md"`. Do not treat peer agreement as proof.

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

The runtime auto-selects one of five canonical modes unless overridden:

- `review`: strict challenge of diffs, patches, risky changes, release readiness, security-sensitive code, and missing tests.
- `research`: source-backed current facts when external/API/platform/dependency/tooling truth is the main deliverable.
- `design`: architecture, alternatives, compatibility, migration approach, and tradeoff challenge.
- `plan`: implementation sequencing, rollout, test strategy, rollback, and readiness challenge.
- `debug`: root-cause challenge for bugs, crashes, failing tests, logs, and reproduction gaps.

Official-doc research can happen in any mode; choose `research` only when source-backed external facts are primary.

## Guardrails

Cross-agent depth is capped at 1. Local subagent depth is capped at 1 by default. Peer runs receive full repo, shell, tool, and network capability by default, and no-edit requests are enforced by prompt plus post-run workspace mutation detection. Git repos use git status/diff snapshots; non-git directories use deterministic filesystem snapshots. Use safe mode when technical read-only restrictions are required. The runtime prepends a PATH guard that blocks ordinary unqualified host CLI lookup, but it is not a sandbox and cannot block absolute host CLI paths or deliberate PATH rewriting.

Full capability is for investigation and validation. It is not blanket permission to mutate the repo.

Peer output is structured by schema first. The runtime preserves `peer.raw.json`, writes `peer-normalization.json`, and can recover a schema-valid JSON report from a Claude `result` string that contains prose before the report.
