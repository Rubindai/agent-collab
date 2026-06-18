# Host Synthesis Contract

Agent Collab is a challenge-first second opinion workflow. The host owns the final answer. Use peer, helper, and adjudicator reports as evidence, not authority. Do not treat agreement as proof; agreement only tells the host which shared evidence deserves closer inspection.

Canonical modes are `review`, `research`, `design`, `plan`, and `debug`. The runtime auto-selects the mode from the target and neutral brief unless the host supplies one explicitly. Official-doc research can happen in any mode; `research` means source-backed external facts are the primary deliverable.

Freshness rule: When a material claim depends on current or external information, including APIs, product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, or research, use the latest official documentation or primary sources. Do not rely on model memory for unstable facts. If online research is disabled or sources are unavailable, state that limitation explicitly and mark the claim as unverified.

Required flow:

1. Default `profile` to `ultra`. Classify `target`, `profile`, and whether edits are explicitly allowed.
2. Build a neutral peer brief before doing host analysis. Decide what the peer needs, keep the prompt natural and non-leading, and include only the user request, scope, success criteria, hard constraints, and edit policy.
3. Start the peer run before host analysis. Use `scripts/host.py start` when possible so the run directory, request, workspace snapshot, and background peer process are created consistently.
4. Do not read peer output until independent host work is complete and `host-first-pass.json` exists.
5. While the peer is running, do independent challenge-first host analysis. In `ultra`, use host-local subagents for independent lenses such as mapper, reviewer, researcher, architect, security-auditor, debugger, test-strategist, and verifier when available. Do not poll peer status during this independent phase.
6. Give host-local subagents only the neutral brief and their lens. Do not provide peer findings, host conclusions, suspected answers, or implementation defense. Explicitly tell helper subagents not to invoke Agent Collab, `$agent-collab`, `/agent-collab`, host/peer CLIs, or cross-product peer commands.
7. Ask all agents to use latest official documentation for external/API/platform/dependency/tooling claims and to research online extensively when current external facts could affect the answer.
8. Use `finish` as the synchronization point after `host-first-pass.json`; status polling is not part of the normal independent-host phase. `finish` waits for peer artifacts, validates/normalizes the report, and builds synthesis support artifacts.
9. Do not cancel a live peer before the 2700-second minimum wait. An empty `peer-report.json` or stderr does not mean the peer is stalled while the process is alive. Do not replace Agent Collab with a direct `claude --print` fallback before the minimum wait.
10. After host first pass and peer output exist, run an advisory host-local adjudicator in `ultra` when one is available. The adjudicator receives the neutral brief, host first pass, peer report, helper reports, and claim matrix. It must not call the other product or invoke Agent Collab. If no adjudicator artifact exists, `finish` writes an `advisory_pending` marker rather than claiming adjudication happened.
11. Verify important peer, helper, and adjudicator claims yourself before final synthesis.
12. Snapshot workspace state after the run and surface unexpected mutation diagnostics, especially when `edit_allowed=false`; do not discard otherwise valid peer claims solely because a diagnostic exists.
13. Synthesize the final result in Markdown.

Do not edit files, run formatters, or run commands likely to create repo-visible artifacts while the peer is running unless the user explicitly requested implementation and the host can still distinguish host edits from peer edits.

Mode-specific challenge:

- `review`: decide whether the work should ship. Lead with concrete bugs, regressions, security risks, missing tests, compatibility breaks, and file/line evidence.
- `research`: decide whether claimed facts are true, current, and applicable. Prefer latest official documentation and source-backed evidence; label inference and stale or unavailable facts.
- `design`: decide whether the proposed approach is the right architecture. Compare alternatives, constraints, reversibility, operational risk, migration impact, and repo fit.
- `plan`: decide whether the execution sequence is actually ready. Check prerequisites, ordering, rollback, verification, test strategy, and human decisions.
- `debug`: decide whether the diagnosis is proven. Challenge reproduction, logs, code paths, environment assumptions, and the smallest checks that would falsify the theory.

Host first-pass claim shape:

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

Each claim must use `claim`, `status`, and `evidence`. `status` must be one of the claim labels below. `evidence` must be one string; join multiple evidence items with `; `. Do not use `id` or `type` as substitutes, and do not make `evidence` an array.

Claim labels:

- `confirmed`: independently supported by host verification.
- `plausible_unverified`: credible but not fully verified in this run.
- `rejected`: checked and found false or inapplicable.
- `product_decision`: a deliberate host/user policy choice, not a technical fact.
- `needs_human_input`: blocked on missing preference, credential, access, or risk acceptance.

Adjudicator status is advisory. Consensus is a prioritization signal, not proof. The host makes the final decision.

Recommended verdicts:

- For `review`, return one of: `pass`, `pass_with_concerns`, `changes_recommended`, or `blocked`.
- For `research`, return one of: `informational`, `pass_with_concerns`, `changes_recommended`, or `blocked`.
- For `design`, `plan`, and `debug`, return one of: `ready`, `needs_revision`, `changes_recommended`, or `blocked`.

Keep raw peer and subagent output out of the final answer unless a concise excerpt is necessary to explain evidence.
