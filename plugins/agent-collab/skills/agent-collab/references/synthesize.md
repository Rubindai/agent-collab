# Host Synthesis Contract

Agent Collab is a challenge-first second opinion workflow. The host owns the final answer. Use peer, helper, and adjudicator reports as evidence, not authority. Do not treat agreement as proof; agreement only tells the host which shared evidence deserves closer inspection.

Canonical modes are `review`, `research`, `design`, `plan`, and `debug`. The runtime auto-selects the mode from the target and neutral brief unless the host supplies one explicitly. Official-doc research can happen in any mode; `research` means source-backed external facts are the primary deliverable.

Freshness rule: When a material claim depends on current or external information, including APIs, product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, or research, use the latest official documentation or primary sources. Do not rely on model memory for unstable facts. If online research is disabled or sources are unavailable, state that limitation explicitly and mark the claim as unverified.

Required flow:

1. Default `profile` to `ultra`, the configured full-capability launch posture, and `edit_allowed=false`. Full capability, including Claude `bypassPermissions`, has host-level consequences; Anthropic recommends that mode only inside isolated containers, VMs, or development containers without internet access where host damage is impossible. Safe mode is an explicit user choice. Classify the target and mode, and enable edits only when the user explicitly delegates changes. Launch capability and edit authorization are independent decisions.
2. Build a neutral peer brief before doing host analysis. Lead with the outcome, then supply relevant context, expected output or done condition, decisive boundaries, and available verification signals. Include only process constraints that matter to independence or safety; omit host conclusions, suspected findings, and implementation defense.
3. Start the peer run before host analysis. Use `scripts/host.py start` so the runtime first attests the exact model and effort pair, then creates the run directory, resolved request, workspace snapshot, recursion guard, and background peer process. For Codex, require the exact pair in freshly refreshed `codex debug models` output. In a bounded, configuration-reduced Claude probe outside the target repository, require API `modelUsage` to include the exact resolved model and the `Stop` hook to report the expected effective effort (`xhigh` for `ultracode`, otherwise the requested provider level). If the result is unavailable or unknown, or a Codex safe-mode sandbox prerequisite fails, report the structured failure instead of retrying another model, effort, permission posture, or isolation mode.
4. Do not read peer output until independent host work is complete and `host-first-pass.json` exists.
5. While the peer is running, do independent challenge-first host analysis. In `ultra`, use host-local helpers for independent lenses such as mapping, review, research, architecture, security, debugging, test strategy, and verification only when materially useful. Enforce the resolved finite helper count and depth, eight helpers by default. In full mode retain their native tools and permissions, subject to provider policy. Do not poll peer status during this independent phase.
6. Give host-local helpers only the neutral brief and their lens. Do not provide peer findings, host conclusions, suspected answers, or implementation defense. Explicitly tell helpers not to invoke Agent Collab, `$agent-collab`, `/agent-collab`, provider peer CLIs, or cross-product peer commands. The atomic worktree guard is the hard cross-product recursion boundary. The runtime enforces Claude peer `Agent` calls, Codex peer fanout, and helper artifact admission; host-native helper launch discipline remains a host instruction because the shared runtime cannot intercept every host tool call.
7. Ask all agents to use latest official documentation or primary sources for unstable external claims. Use online research only when the resolved run enables it; otherwise mark freshness-dependent claims unverified.
8. Use `finish` as the synchronization point after `host-first-pass.json`; status polling is not part of the normal independent-host phase. `finish` waits for peer artifacts, validates the current canonical output shape, and builds synthesis support artifacts. It intentionally keeps the hard worktree recursion guard active through synthesis.
9. Respect the hard finite peer deadline, 2700 seconds by default. Empty `peer-report.json` or stderr does not mean the process is stalled before that deadline. Do not replace Agent Collab with a direct provider peer-CLI fallback. At the deadline, terminate the peer and preserve the structured timeout result; never wait indefinitely.
10. After host first pass and peer output exist, run an advisory host-local adjudicator in `ultra` when one is available. The adjudicator receives the neutral brief, host first pass, peer report, helper reports, and claim matrix. It must not call the other product or invoke Agent Collab. If no adjudicator artifact exists, `finish` writes an `advisory_pending` marker rather than claiming adjudication happened. Run `finish` again after writing an adjudicator report so the claim matrix is rebuilt from it.
11. Verify important peer, helper, and adjudicator claims yourself before final synthesis.
12. Snapshot workspace state after the run and surface unexpected mutation diagnostics, especially when `edit_allowed=false`; do not discard otherwise valid peer claims solely because a diagnostic exists. In full-capability mode, no-edit prompting and mutation comparison are advisory and detective, not a preventive filesystem boundary.
13. Synthesize the final result in Markdown.
14. Immediately before returning the final answer, run `host.py complete RUN` to close the workflow and release the worktree guard. Helpers must never call `complete`. An explicitly cancelled run releases the guard through `cancel` instead.

Do not edit files, run formatters, or run commands likely to create repo-visible artifacts while the peer is running unless the user explicitly delegated implementation and the host can still distinguish host edits from peer edits.

Mode-specific challenge:

- `review`: decide whether the work should ship. Lead with concrete bugs, regressions, security risks, missing tests, compatibility breaks, and file/line evidence.
- `research`: decide whether claimed facts are true, current, and applicable. Prefer latest official documentation and source-backed evidence; label inference and stale or unavailable facts.
- `design`: decide whether the proposed approach is the right architecture. Compare alternatives, constraints, reversibility, operational risk, migration impact, and repo fit.
- `plan`: decide whether the execution sequence is actually ready. Check prerequisites, ordering, rollback, verification, test strategy, and human decisions.
- `debug`: decide whether the diagnosis is proven. Challenge reproduction, logs, code paths, environment assumptions, and the smallest checks that would falsify the theory.

Host first-pass claim shape:

```json
{
  "schema_version": "2.0",
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

When helper reports are saved, use only the strict v2 envelope below; arrays and unversioned helper output are unsupported:

```json
{
  "schema_version": "2.0",
  "run_id": "agent-collab-run-id",
  "reports": [
    {
      "name": "reviewer",
      "summary": "Independent helper summary.",
      "claims": [
        {
          "claim": "A verified helper claim.",
          "status": "confirmed",
          "evidence": "Concrete file, command, or source evidence"
        }
      ]
    }
  ]
}
```

After the host has performed final verification and prepared its synthesis, write this strict attestation before calling `complete`:

```json
{
  "schema_version": "2.0",
  "run_id": "agent-collab-run-id",
  "summary": "Concise synthesis that supports the final answer.",
  "verdict": "pass_with_concerns",
  "claims": [
    {
      "claim": "A host-verified synthesis claim.",
      "status": "confirmed",
      "evidence": "Concrete verification evidence"
    }
  ],
  "unresolved_risks": [],
  "workspace_mutation_sha256": "copy the exact digest from host-result.json",
  "final_answer_ready": true
}
```

Copy `workspace_mutation_sha256` exactly from the ready `host-result.json` after reviewing its
`workspace_mutation` value. `complete` validates this binding and refuses to release the worktree
guard when the synthesis is missing, malformed, belongs to another run, or predates changed
mutation evidence. If `complete` refreshes that evidence, inspect it and synthesize again with the
new digest before retrying.

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
