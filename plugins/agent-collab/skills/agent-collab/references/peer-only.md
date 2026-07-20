# Peer Prompt

The peer receives one compact, escaped XML prompt with six sections. Use them to express the parts current Codex and Claude guidance identifies as most useful without over-prescribing the investigation:

- `role`: independent challenge-first peer and mode-specific lens
- `outcome`: the result, expected output, and what done means
- `evidence`: available verification signals and the standard for material claims
- `boundaries`: scope, edit authorization, research availability, finite helper count/depth, recursion instructions, and the peer deadline
- `task`: the user's target, relevant context, and constraints
- `stop`: the completion check and instruction to avoid unrelated expansion

Start with the desired result. Include only context that can change the answer and only process constraints needed for independence, verification, or safety. Do not reveal host conclusions, suspected findings, or implementation defense. Do not duplicate `host-request.json` or the response schema in the prompt; provider output controls and runtime validation handle the structured report.

Instruct the peer to:

1. Produce a challenge-first second opinion and actively seek disconfirming evidence. In review mode, surface every in-scope issue you find, including uncertain or low-severity candidates, and label severity and confidence so host verification can filter them.
2. Inspect the relevant repository context before settling on a conclusion.
3. Ground material claims in file references, command output, tests, reproduction evidence, or current primary documentation.
4. Use available verification checks and report what was actually run or observed. Never assert that a check passed without evidence.
5. Use online research only when the resolved request enables it. Otherwise, label freshness-dependent claims unverified.
6. Respect the edit and local-helper instructions. Never exceed the resolved helper count or depth. Full launch capability and inherited tools do not themselves authorize changes or relax those bounds.
7. Never invoke Agent Collab, the host product, another cross-product peer, or ask a helper to do so.
8. Stop when the requested outcome is satisfied and nearby decisive checks can no longer materially change it; do not assume the peer deadline can be extended indefinitely.
9. Return only the schema-constrained report.

This prompt is advisory instruction to the model. Do not describe it as a sandbox, a provider-system override, or a security boundary: the peer CLI may also load provider-managed instructions, repository guidance, configured tools, and policy. The runtime uses the values persisted in the v2 request for its launch arguments and must not independently re-resolve Agent Collab settings in the peer process.
