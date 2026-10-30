# Behavioral evaluations

These six cases use the native [Claude Code plugin-eval format](https://code.claude.com/docs/en/plugin-evals), checked against `claude plugin eval --help` in Claude Code 2.1.314. They measure skill selection and a narrow verification task. They do **not** measure a real opposite-product peer, independence, runtime closure, provider availability, permissions enforcement, or overall product usefulness.

| Case | Expected selection | Result checked |
| --- | --- | --- |
| `explicit-request` | Agent Collab | A requested workflow is reported as unstarted when launch tools are absent |
| `implicit-substantial-review` | Agent Collab | An independent architectural review request selects the skill and reports the launch limitation |
| `quoted-mention` | None | Correct the typo without treating quoted text as an instruction |
| `informational-question` | None | Explain independent peer review without invoking the skill |
| `routine-edit` | None | Return the requested one-line rename |
| `synthetic-finding-verification` | Agent Collab guidance | Confirm a real boundary defect, reject a false finding, evaluate both witnesses, and disclose synthetic provenance |

The outcome case is a hand-authored post-peer exercise. It has no real peer report, run, or admission record; it exercises verification guidance only. Both arms receive identical code and candidate findings. Its ground truth is objective: `3 <= 3` and `100 >= 100` are true, so F1 violates the attempt limit and F2's claimed time defect is false. A zero improvement over baseline is a valid result, not evidence that the grader needs loosening.

## Run

Run from the repository root after `scripts/sync-packages.sh`. The canonical directory is copied to `plugins/agent-collab/evals/`, outside the packaged skill. Do not execute the canonical directory as the plugin target: that would omit the package manifest and undermine the baseline.

Choose the host model and effort explicitly before running. For the repository's usual Claude selection these are `EVAL_HOST_MODEL=claude-opus-5-5` and `CLAUDE_CODE_EFFORT_LEVEL=xhigh`; retain any intentionally selected overrides. The cases specify neither model nor effort and change no user settings or peer permission choices.

```bash
: "${EVAL_HOST_MODEL:?Set the intended Claude host model}"
: "${CLAUDE_CODE_EFFORT_LEVEL:?Set the intended Claude host effort}"
export CLAUDE_CODE_EFFORT_LEVEL
claude plugin eval ./plugins/agent-collab \
  --tag trigger --ablation none --runs 1 \
  --model "$EVAL_HOST_MODEL" --concurrency 1 \
  --max-cost-usd 5 --no-publish --trust-plugin
```

This activation smoke test has five runs. The native exit status gates all selection and result graders because it runs a single arm. For the quality comparison, run fresh plugin and no-plugin arms:

```bash
claude plugin eval ./plugins/agent-collab \
  --ablation with-without --runs 3 \
  --model "$EVAL_HOST_MODEL" --concurrency 1 \
  --max-cost-usd 15 --no-publish --trust-plugin
```

The comparison has 36 host runs at most. Each case defaults to one run per arm, six turns and 120 seconds; the second command explicitly repeats each arm three times. The cost ceiling is checked between launches and can be exceeded by the one in-flight run. `--trust-plugin` is the operator's trust assertion for this reviewed local package. No scaffold, real MCP server, shell, write tool, or helper tool is granted. Do not add `--allow-tools`, `--allow-real-servers`, or `--mocks off` to these commands: that would change the experiment and could enable actual work. Reports remain local with `--no-publish`; this does not make model inference offline or free. Results under `evals/results/` are ignored, not package content.

## Interpret the result

All graders are deterministic native `tool_used` or `regex` checks; there are no judge-model calls. Positive selection checks look for the `Skill` tool with the exact `agent-collab` name, optionally namespaced. Negative checks require zero such calls. Output checks inspect only the final answer. The synthetic case also enforces the six-line output shape to reject contradictory duplicate answers.

In two-arm mode, positive skill checks are deliberately `with-only` indicators, excluded from the quality score. Inspect their verdicts: a successful aggregate exit alone does not prove activation. Negative selection checks use `arm: both`. Compare outcome scores and the per-case delta separately from selection, and inspect tool traces for unexpected behavior. Record the commit, CLI version, requested and observed model/effort, settings, individual grader verdicts, cost, and duration alongside results. Exclude partial runs, API failures, timeouts, and missing or mismatched model/effort evidence from comparisons; never substitute another pair. One run is only a smoke check; even three repeats on six handcrafted prompts provide limited evidence.

The Python unit test checks the suite's launch restrictions and grading oracle without model calls. It is not a behavioral pass. Runtime tests cover lifecycle invariants separately. A real workflow outcome evaluation remains separate work requiring actual peer inference, verified evidence, complete closure, and a comparable no-plugin arm.

## Codex inputs

The `prompt.md` bodies and expected judgments are host-neutral and can be reused for a Codex evaluation, but this directory provides no native Codex execution harness. Claude results must not be reported as Codex activation evidence. Use fresh, equivalently configured Codex sessions with and without the plugin and supply the same prompt bodies; record actual reads of the canonical `SKILL.md` as the selection signal, because Codex does not expose Claude's `Skill` tool. Withhold all process launch, network, MCP, and helper tools in the evaluating environment before using the trigger prompts. A read-only shell is still launch-capable; an ordinary `codex exec --sandbox read-only` invocation is not an equivalent isolation setup. Preserve the selected Codex model and effort in both arms and apply the same objective outcome oracle. Until such a controlled runner is available and executed, report Codex behavioral coverage as outstanding.

## Maintenance

`case.yaml` uses JSON syntax, a YAML-compatible representation that the native harness reads and the standard-library tests can inspect without adding a parser dependency. Prompts stay readable in separate Markdown files. Keep evaluation data under this directory and synchronize it to the package root. Do not embed eval cases in skill instructions or permit the agent under test to read sibling cases or graders.
