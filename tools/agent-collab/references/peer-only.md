# Peer Prompt

This is a maintenance reference for peer prompt construction. Ordinary hosts use the compact briefing guidance in `SKILL.md`; the runtime supplies the provider-specific prompt and constraints below.

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
6. Respect the edit and local-helper instructions. Use the fewest helpers needed for work with independently useful context or verification. Keep tightly coupled work and quick lookups in the main loop; the role catalog does not require a pipeline. The spawner must give each helper a fresh context without parent conversation history, a neutral bounded task, required inputs, edit authorization, remaining deadline, expected evidence, and an explicit prohibition on further helper or cross-product delegation; reuse helpers for follow-ups. Codex spawners must explicitly set `fork_turns="none"`; omission inherits the parent history, and this rule is instructional. Require the peer's exact model and effective effort and avoid custom agents or explicit spawn arguments that override either. Delegate only through ordinary native Agent/spawn_agent helpers; do not use Workflow, teams, remote agents, or external sessions. This is an instruction, not a universal interception of every orchestration surface. Claude total Agent calls and one-layer spawn depth are runtime-bounded; resumes through `SendMessage` do not consume Agent calls. Codex concurrent threads are provider-bounded; state its instructional numeric total-spawn bound explicitly, including completed threads. Prohibit nesting: V1 configures `agents.max_depth=1`, while V2 depth remains instructional. Full capability does not authorize durable edits. Temporary validation writes in isolated mode are discarded; direct-mode edits require explicit delegation. When parallel edits are authorized, assign disjoint writable paths and one integration owner, serialize overlaps, and preserve others' changes.
7. Never invoke Agent Collab, the host product, another cross-product peer, or ask a helper to do so. Keep the review independent of saved provider memory and earlier Agent Collab run artifacts; provider memory is also disabled at launch.
8. Complete authorized work within scope instead of ending with a plan or an offer to continue. Resolve nonblocking ambiguity with stated assumptions. Await started commands and helpers within the deadline. Finish with evidence or explicit uncertainty for material claims; when blocked, report the blocker and uncovered scope. Repeat checks only when new evidence or changes justify them. The deadline remains a hard runtime limit.
9. Return only the schema-constrained report.

This prompt is advisory instruction to the model. Do not describe it as a sandbox, a provider-system override, or a security boundary: the peer CLI may also load provider-managed instructions, repository guidance, configured tools, and policy. The runtime uses the values persisted in the v3 request for its launch arguments and must not independently re-resolve Agent Collab settings in the peer process.

Prompt review basis (2026-10-25): [GPT-6 Astra prompting](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#prompting-best-practices) and [Opus 5.5 prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5). Apply persistence within the resolved authorization and deadline; effort is configured by the CLI, not by requests to reveal reasoning. Provider-specific API continuation or progress-display features are not implemented by this CLI wrapper. The compact skill router and conditional references follow [OpenAI's skill and prompt guidance](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra).

Helper guidance reviewed on 2026-10-25 against [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) and [Claude Code subagents](https://code.claude.com/docs/en/sub-agents). Codex helper defaults do not override custom agent definitions. Claude uses forced helper model selection, effective-effort environment configuration, and disabled conversation forks; provider policy still applies.

Fresh review contexts preserve independence at the cost of losing parent-conversation cache reuse on a Claude helper's first request. Reuse helpers for follow-ups; do not switch independent reviewers to forks or force longer cache lifetimes to claim unmeasured savings. Stable outcome and evidence text precedes the per-run identity. Cache hits, cost, latency, and review quality still require measurements. [Claude caching](https://code.claude.com/docs/en/prompt-caching), [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)

Delegation choice follows [Anthropic's main-loop guidance](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop): retain independent verification or separable parallel work; use skills and tools in the main loop when work needs the same evolving context. A role name alone is not a reason to spawn. Compare end-to-end completion before claiming quality or latency gains from architectural changes.
