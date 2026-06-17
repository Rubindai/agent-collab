# Host Synthesis Contract

The host owns the final answer. Use peer, helper, and adjudicator reports as evidence, not authority.

Required flow:

1. Classify `mode`, `target`, `profile`, and whether edits are explicitly allowed. Default `profile` to `ultra`.
2. Build a neutral peer brief before doing host analysis. Decide what the peer needs, keep the prompt natural and non-leading, and include only the user request, scope, success criteria, hard constraints, and edit policy.
3. Start the peer run before host analysis. Use `scripts/host.py start` when possible so the run directory, request, git snapshot, and background peer process are created consistently.
4. Do not read peer output until independent host work is complete and `host-first-pass.json` exists.
5. While the peer is running, do host analysis independently. In `ultra`, use host-local subagents for independent lenses such as mapper, reviewer, researcher, architect, security-auditor, debugger, test-strategist, and verifier when available. Do not poll peer status during this independent phase.
6. Give host-local subagents only the neutral brief and their lens. Do not provide peer findings, host conclusions, suspected answers, or implementation defense.
7. Ask all agents to use latest official documentation for external/API/platform/dependency/tooling claims and to research online extensively when current external facts could affect the answer.
8. Use `finish` as the synchronization point after `host-first-pass.json`; status polling is not part of the normal independent-host phase. `finish` waits for peer artifacts, validates/normalizes the report, and builds synthesis support artifacts.
9. After host first pass and peer output exist, run an advisory host-local adjudicator in `ultra`. The adjudicator receives the neutral brief, host first pass, peer report, helper reports, and claim matrix. It must not call the other product or invoke Agent Collab.
10. Verify important peer, helper, and adjudicator claims yourself before final synthesis.
11. Snapshot git state after the run and report unexpected mutations, especially when `edit_allowed=false`.
12. Synthesize the final result in Markdown.

Do not edit files, run formatters, or run commands likely to create repo-visible artifacts while the peer is running unless the user explicitly requested implementation and the host can still distinguish host edits from peer edits.

Claim labels:

- `confirmed`: independently supported by host verification.
- `plausible_unverified`: credible but not fully verified in this run.
- `rejected`: checked and found false or inapplicable.
- `product_decision`: a deliberate host/user policy choice, not a technical fact.
- `needs_human_input`: blocked on missing preference, credential, access, or risk acceptance.

Adjudicator status is advisory. Consensus is a prioritization signal, not proof. The host makes the final decision.

For review/audit, return one of: `pass`, `pass_with_concerns`, `changes_recommended`, or `blocked`.

For plan critique, return one of: `ready`, `needs_revision`, or `blocked`.

Keep raw peer and subagent output out of the final answer unless a concise excerpt is necessary to explain evidence.
