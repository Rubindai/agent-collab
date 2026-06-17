# Peer Prompt Blocks

Use these blocks as the compact contract for peer runs.

```xml
<structured_output_contract>
Return exactly one JSON object matching the schema. Do not add prose before or after it.
</structured_output_contract>

<grounding_rules>
Ground material claims in repo evidence, command output, tests, or current primary documentation.
Label hypotheses and uncertainty clearly.
</grounding_rules>

<tool_persistence_rules>
Use tools until the request has enough evidence for a useful answer.
Do not stop after the first plausible issue if nearby checks could change the result.
</tool_persistence_rules>

<action_safety>
Full capability is for investigation. Do not mutate project state unless edit_allowed is exactly true and the request delegated edits.
</action_safety>
```
