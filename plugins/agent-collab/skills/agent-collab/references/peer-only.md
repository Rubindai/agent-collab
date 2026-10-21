# Peer Prompt

The peer receives one compact, escaped XML prompt with six sections. Use them to express the parts current Codex and Claude guidance identifies as most useful without over-prescribing the investigation:

- `role`: independent challenge-first peer and mode-specific lens
- `outcome`: the result, expected output, and what done means
- `evidence`: available verification signals and the standard for material claims
- `boundaries`: scope, edit authorization, research availability, provider-specific helper limits, recursion instructions, and the peer deadline
- `task`: exact report identity, the user's target, relevant context, and constraints
- `stop`: completion, pending work, assumptions, blockers, and limits on further investigation

Start with the desired result. Include only context that can change the answer and only process constraints needed for independence, verification, or safety. Do not reveal host conclusions, suspected findings, or implementation defense. Do not duplicate `host-request.json` or the response schema in the prompt; provider output controls and runtime validation handle the structured report.

Instruct the peer to:

1. Produce a challenge-first second opinion and actively seek disconfirming evidence. In review mode, surface every in-scope issue you find, including uncertain or low-severity candidates, and label severity and confidence so host verification can filter them.
2. Inspect the relevant repository context before settling on a conclusion, using the available repository, command, test, and research tools when they can materially improve the evidence.
3. Ground material claims in file references, command output, tests, reproduction evidence, or current primary documentation.
4. Use available verification checks and report what was actually run or observed. Never assert that a check passed without evidence.
5. Use online research only when the resolved request enables it. Otherwise, label freshness-dependent claims unverified.
6. Respect the edit and local-helper instructions. Use the fewest helpers needed for sizeable independent workstreams. Give each a fresh context without parent conversation history, a neutral bounded task, required inputs, edit authorization, remaining deadline, and expected evidence; reuse helpers for follow-ups. Require the peer's exact model and effective effort and avoid custom agents or explicit spawn arguments that override either. Delegate only through ordinary native Agent/spawn_agent helpers; do not use Workflow, teams, remote agents, or external sessions. This is an instruction, not a universal interception of every orchestration surface. Claude total Agent calls and one-layer spawn depth are runtime-bounded; resumes through `SendMessage` do not consume Agent calls. Codex concurrent threads are provider-bounded; state its numeric total-spawn bound explicitly, including completed threads, and prohibit nesting as instructions. Full capability does not authorize durable edits. Temporary validation writes in isolated mode are discarded; direct-mode edits require explicit delegation. When parallel edits are authorized, assign disjoint writable paths and one integration owner, serialize overlaps, and preserve others' changes.
7. Never invoke Agent Collab, the host product, another cross-product peer, or ask a helper to do so. Keep the review independent of saved provider memory and earlier Agent Collab run artifacts; provider memory is also disabled at launch.
8. Complete authorized work within scope instead of ending with a plan or an offer to continue. Resolve nonblocking ambiguity with stated assumptions. Await started commands and helpers within the deadline. Finish with evidence or explicit uncertainty for material claims; when blocked, report the blocker and uncovered scope. Repeat checks only when new evidence or changes justify them. The deadline remains a hard runtime limit.
9. Return only the schema-constrained report.

This prompt is advisory instruction to the model. Do not describe it as a sandbox, a provider-system override, or a security boundary: the peer CLI may also load provider-managed instructions, repository guidance, configured tools, and policy. The runtime uses the values persisted in the v2 request for its launch arguments and must not independently re-resolve Agent Collab settings in the peer process.

Prompt review basis (2026-10-18): [GPT-6 Astra prompting](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#prompting-best-practices) and [Opus 5.5 prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5). Apply persistence within the resolved authorization and deadline; effort is configured by the CLI, not by requests to reveal reasoning. Provider-specific API continuation or progress-display features are not implemented by this CLI wrapper.

Helper guidance reviewed on 2026-10-18 against [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) and [Claude Code subagents](https://code.claude.com/docs/en/sub-agents). Codex helper defaults do not override custom agent definitions. Claude uses forced helper model selection, effective-effort environment configuration, and disabled conversation forks; provider policy still applies.

Fresh review contexts preserve independence at the cost of losing parent-conversation cache reuse on a Claude helper's first request. Reuse helpers for follow-ups; do not switch independent reviewers to forks or force longer cache lifetimes to claim unmeasured savings. Stable outcome and evidence text precedes the per-run identity. Cache hits, cost, latency, and review quality still require measurements. [Claude caching](https://code.claude.com/docs/en/prompt-caching), [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)
