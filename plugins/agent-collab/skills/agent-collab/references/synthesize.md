# Host Synthesis Contract

- [Workflow](#workflow)
- [Mode-specific review](#mode-specific-review)
- [Artifact shapes](#artifact-shapes)
- [Evidence boundaries](#evidence-boundaries)
- [Claim labels and verdicts](#claim-labels-and-verdicts)

Agent Collab is a challenge-first second opinion workflow. The host owns the final answer. Complete the authorized workflow through synthesis and closure without stopping at a plan or offer to continue; report any blocker and remaining scope. After required checks pass, repeat or broaden them only when new evidence, changes, or unresolved claims justify it. Use peer, helper, and adjudicator reports as evidence, not authority. Do not treat agreement as proof; agreement only tells the host which shared evidence deserves closer inspection.

Canonical modes are `review`, `research`, `design`, `plan`, and `debug`. The runtime auto-selects the mode from the target and neutral brief unless the host supplies one explicitly. Official-doc research can happen in any mode; `research` means source-backed external facts are the primary deliverable.

Freshness rule: When a material claim depends on current or external information, including APIs, product behavior, platform docs, dependency behavior, pricing, security advisories, laws, policies, or research, use the latest official documentation or primary sources. Do not rely on model memory for unstable facts. If online research is disabled or sources are unavailable, state that limitation explicitly and mark the claim as unverified.

## Workflow

1. Default to the configured full-capability launch posture, `adjudicator_enabled=true`, and `edit_allowed=false`. Full capability, including Claude `bypassPermissions`, has host-level consequences; Anthropic recommends that mode only inside isolated containers, VMs, or development containers without internet access where host damage is impossible. Safe mode is an explicit user choice. Classify the target and mode, and enable edits only when the user explicitly delegates changes. Launch capability and edit authorization are independent decisions.
2. Build a neutral peer brief before doing host analysis. Lead with the outcome, then supply relevant context, expected output or done condition, decisive boundaries, and available verification signals. Include only process constraints that matter to independence or safety; omit host conclusions, suspected findings, and implementation defense.
3. Start the peer run before host analysis. Use `scripts/host.py start` so the runtime first attests the exact model and effort pair, then creates the run directory, resolved request, workspace snapshot, run guard, and background peer process. For Codex, require the exact pair in the `codex debug models` catalog Codex resolves at launch (an online refresh when its five-minute cache is stale; Codex can silently fall back to its bundled catalog if that refresh fails). In a bounded, configuration-reduced Claude probe outside the target repository, require API `modelUsage` to include the exact resolved model and the `Stop` hook to report the expected effective effort (`xhigh` for `ultracode`, otherwise the requested provider level). Explicit `ultracode` execution fails before launch with `helper_policy_unavailable`, because Workflow orchestration bypasses the finite helper counter; do not substitute another mode. If the result is unavailable or unknown, or a Codex safe-mode sandbox prerequisite fails, report the structured failure instead of retrying another model, effort, permission posture, or isolation mode.
4. Do not read peer output until independent host work is complete and independent first-pass input has been saved; `finish --first-pass FILE` admits it before exposing results. This is a procedural independence rule; artifact ordering is evidence of the sequence but the runtime cannot prove the host's reading history.
5. While the peer is running, do independent challenge-first host analysis. Use host-local helpers only when separate context or independent verification materially helps. Keep tightly coupled planning, routing, and domain work in the main loop. Mapping, review, research, architecture, security, debugging, test strategy, and verification are optional lenses, not a mandatory pipeline. Enforce the resolved finite helper count and instructed depth, eight helpers by default. Claude peer total Agent calls are runtime-enforced. Codex peer concurrency is provider-enforced; total calls and V2 nesting depth are instructional, while V1 uses `agents.max_depth=1`. In full mode retain native tools and permissions, subject to provider policy. Do not poll peer status during this independent phase.
6. The spawner must start host-local helpers in fresh contexts, without parent conversation forks; an already-running helper cannot choose its initial context, model, or effort. For Codex `spawn_agent`, explicitly set `fork_turns="none"`; omission inherits parent history, and this rule is instructional. Supply a neutral bounded task and lens, required inputs, edit authorization, remaining deadline, and expected evidence; omit peer findings, host conclusions, suspected answers, and implementation defense. Preserve the spawning host's exact model and effective effort, avoid conflicting custom agent definitions, and reuse helpers for follow-ups. Count every new spawn against the resolved finite total, even after a helper completes. Explicitly forbid further helper delegation, Agent Collab, `$agent-collab:agent-collab`, `/agent-collab:agent-collab`, provider peer CLIs, and cross-product peer commands. Peer depth markers and CLI shadows deter nested runs but are not a hard recursion boundary. The runtime enforces Claude peer total `Agent` calls, Codex peer concurrency, and helper artifact admission; Claude resumes through `SendMessage` do not consume Agent calls. Host-native launch discipline, Codex total calls, and V2 depth remain instructions because the runtime cannot intercept them; V1 depth is configured separately.
7. Ask all agents to use latest official documentation or primary sources for unstable external claims. Use online research only when the resolved run enables it; otherwise mark freshness-dependent claims unverified.
8. Use `finish --first-pass FILE` as the synchronization point after saving independent analysis; status polling is not part of the normal independent-host phase. `finish` waits for peer artifacts, validates the current canonical output shape, and builds synthesis support artifacts. It intentionally keeps the run guard active through synthesis.
9. Respect the hard finite peer deadline, 2700 seconds by default. Empty `peer-report.json` or stderr does not mean the process is stalled before that deadline. Do not replace Agent Collab with a direct provider peer-CLI fallback. At the deadline, terminate the peer and preserve the structured timeout result; never wait indefinitely. Do not configure a Claude turn cap unless the user explicitly requests `--claude-max-turns N`; if reached, preserve `turn_limit_reached` separately from timeout.
10. After host first pass and peer output exist, run an advisory host-local adjudicator when `adjudicator_enabled` is true and one is available. The adjudicator receives the neutral brief, host first pass, peer report, helper reports, and claim matrix. It must not call the other product or invoke Agent Collab. This independence is also procedural; the runtime validates the artifact but cannot prove the adjudicator's process history. The adjudicator returns its report; the host admits its judgment fields with `finish --adjudicator FILE` (shape below), replacing any marker. If no adjudicator artifact exists, `finish` writes an `advisory_pending` marker, which means no adjudicator report was supplied rather than that adjudication happened. Run `finish` again after saving an adjudicator report so the claim matrix is rebuilt from it. When adjudication is disabled, do not create or accept `adjudicator-report.json`.
11. Verify important peer, helper, and adjudicator claims yourself before final synthesis.
12. In isolated mode, verify the disposable workspace has been cleaned before closing the guard; the host boundary receipt does not scan repository contents. In direct mode, compare before/after snapshots and surface unexpected mutation diagnostics, especially when `edit_allowed=false`; concurrent sessions can contribute changes and snapshots do not establish attribution; do not discard otherwise valid peer claims solely because a diagnostic exists.
13. Synthesize the final result in Markdown.
14. Immediately before returning the final answer, run `host.py complete RUN --synthesis SYNTHESIS_INPUT` to close the workflow and release the worktree guard. Helpers must never call `complete`. An explicitly cancelled run releases the guard through `cancel` after writing immutable cancellation-specific evidence without replacing peer or synthesis artifacts. If cancel reports `already_terminal`, keep the guard and run `finish` instead.

Do not edit files, run formatters, or run commands likely to create repo-visible artifacts while the peer is running unless the user explicitly delegated implementation and the host can still distinguish host edits from peer edits. Before authorized parallel writes, assign disjoint writable paths to each writer and one integration owner. Serialize overlapping edits and preserve other agents' changes.

## Mode-specific review

- `review`: decide whether the work should ship. Lead with concrete bugs, regressions, security risks, missing tests, compatibility breaks, and file/line evidence.
- `research`: decide whether claimed facts are true, current, and applicable. Prefer latest official documentation and source-backed evidence; label inference and stale or unavailable facts.
- `design`: decide whether the proposed approach is the right architecture. Compare alternatives, constraints, reversibility, operational risk, migration impact, and repo fit.
- `plan`: decide whether the execution sequence is actually ready. Check prerequisites, ordering, rollback, verification, test strategy, and human decisions.
- `debug`: decide whether the diagnosis is proven. Challenge reproduction, logs, code paths, environment assumptions, and the smallest checks that would falsify the theory.

## Artifact shapes

Use semantic JSON inputs for host judgments. The CLI fills schema versions, run IDs, readiness flags, and evidence hashes; it persists strict schema 2.0 artifacts and rejects unknown fields. Do not include mechanical metadata in semantic inputs.

Save independent host analysis before opening peer output:

```json
{"summary": "Independent host analysis.", "claims": [{"claim": "A candidate finding.", "status": "plausible_unverified", "evidence": "File, command, or source evidence."}]}
```

Admit it with `host.py finish RUN --first-pass first-pass.input.json`. Optional `--helpers helpers.input.json` accepts `{"reports": [{"name": "reviewer", "summary": "Independent summary.", "claims": []}]}` subject to the resolved helper bound. Existing canonical first-pass and helper files are also accepted through their strict artifact contracts.

For enabled adjudication, save the actual advisory report without schema or run metadata:

```json
{"status": "ok", "summary": "Advisory assessment.", "false_positives": [], "claims_needing_verification": [], "recommended_verdict": "pass_with_concerns", "claims": []}
```

Run `host.py finish RUN --adjudicator adjudicator.input.json`. `claims` is optional for this input; status is `ok`, `needs_human_input`, or `blocked`. The runtime reserves `advisory_pending` for its exact placeholder, which records that no advisory report was supplied. Adjudication-disabled runs reject advisory input.

Every successful `finish` returns `review_dir` and `synthesis_input`. Render the captured revision with `host.py result RUN --format markdown --review REVIEW_DIR`, verify the source claims, and edit only the four judgment fields in the generated `synthesis.json`:

```json
{"summary": "Host-verified synthesis.", "verdict": "pass_with_concerns", "claims": [], "unresolved_risks": []}
```

Close with `host.py complete RUN --synthesis SYNTHESIS_INPUT`. Completion fills and validates the canonical synthesis artifact before releasing the owned guard. Existing exact canonical synthesis artifacts remain accepted by `complete RUN`.

Each claim uses exactly `claim`, `status`, and `evidence`; evidence is one string. Use the claim labels below. Review every in-scope candidate; schema adherence does not prove a conclusion.

The revision receipt binds the exact run instance and all admitted source reports, claim matrix, normalization, and workspace evidence. It contains hashes of complete reports, so changes to findings, severity, summaries, or other evidence invalidate the revision. Every peer finding also appears as a `plausible_unverified` matrix row with severity, confidence, details, files, and recommendation.

`complete` rebuilds the matrix and checks current mutation evidence. Changed evidence keeps the guard active: rerun `finish`, inspect the new revision, and author a new synthesis. Repeated `finish` calls preserve an existing draft when evidence is unchanged; changed evidence gets another directory. Never copy old judgments into a new revision without reviewing it. The runtime checks artifact bindings, not the host's actual reading history.

## Evidence boundaries

In direct mode, before peer launch, the host admits only a canonical baseline snapshot that fits its finite
16 MiB JSON read limit; a larger baseline rolls startup back. Large Git index and patch output
is represented by streamed digests. Large status and file manifests retain complete digests and
counts with bounded path samples; changes beyond a sample remain detectable, but exact paths
may be unavailable. The scan itself still reads the included file contents and can hit its
finite deadline. After snapshots may be larger.
Isolated mode uses a small boundary receipt instead of reading file contents. Its writable
overlay is discarded after the provider and wrapper are quiescent. Provider outputs return
through a private temporary directory and only the provider credential file is bound through;
host-owned run artifacts are not writable from the sandbox. This does not observe writes made
through sockets (for example a Docker daemon or the systemd user manager), external services,
or the network.

Internal lifecycle artifacts without published schemas (`peer-process.json`,
`workspace-mutation.json`, `cancel-workspace-mutation.json`, `isolation-state.json`,
`startup-cleanup-failure.json`, the workspace snapshots `before.snapshot.json`,
`after.snapshot.json`, and `cancel-after.snapshot.json` with their text companions, and the
worktree guard registry `active-v2.json`) are validated strictly in code; hosts should not author them.
The bounded provider telemetry files `peer-stdout.txt` and `peer.stderr.log` are runtime-owned
too.
If cancellation is interrupted after its mutation diagnostic is committed, retrying validates
and reuses the recorded snapshot hashes without overwriting that evidence. The recorded snapshot
describes the cancellation capture point; later workspace edits are outside that capture.

## Claim labels and verdicts

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
