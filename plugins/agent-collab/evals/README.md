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

Run from the repository root after `scripts/sync-packages.sh`. The canonical directory is copied to `plugins/agent-collab/evals/`, outside the packaged skill. Do not execute the canonical directory as the plugin target: that would omit the package manifest and undermine the baseline. Set `EVAL_RESULTS_ROOT` to a persistent absolute directory outside this repository and plugin, such as `$HOME/.local/state/agent-collab-evals`; package synchronization replaces generated package trees, so results kept there can be lost.

Choose the host model and effort explicitly before running. For the repository's usual Claude selection these are `EVAL_HOST_MODEL=claude-opus-5-5` and `CLAUDE_CODE_EFFORT_LEVEL=xhigh`; retain any intentionally selected overrides. The cases specify neither model nor effort and change no user settings or peer permission choices.

```bash
: "${EVAL_HOST_MODEL:?Set the intended Claude host model}"
: "${CLAUDE_CODE_EFFORT_LEVEL:?Set the intended Claude host effort}"
: "${EVAL_RESULTS_ROOT:?Choose a persistent results directory outside the repository}"
export CLAUDE_CODE_EFFORT_LEVEL
mkdir -p "$EVAL_RESULTS_ROOT"
EVAL_OUTPUT_DIR="$(mktemp -d "$EVAL_RESULTS_ROOT/activation.XXXXXX")"
claude plugin eval ./plugins/agent-collab \
  --tag trigger --ablation none --runs 1 \
  --model "$EVAL_HOST_MODEL" --concurrency 1 \
  --disallow-tools Agent Task Workflow ToolSearch SendMessage TaskStop \
  --keep-temp --output-dir "$EVAL_OUTPUT_DIR" \
  --max-cost-usd 5 --no-publish --trust-plugin
```

This activation smoke test has five runs. The native exit status gates all selection and result graders because it runs a single arm. For the quality comparison, run fresh plugin and no-plugin arms:

```bash
EVAL_OUTPUT_DIR="$(mktemp -d "$EVAL_RESULTS_ROOT/comparison.XXXXXX")"
claude plugin eval ./plugins/agent-collab \
  --ablation with-without --runs 3 \
  --model "$EVAL_HOST_MODEL" --concurrency 1 \
  --disallow-tools Agent Task Workflow ToolSearch SendMessage TaskStop \
  --keep-temp --output-dir "$EVAL_OUTPUT_DIR" \
  --max-cost-usd 15 --no-publish --trust-plugin
```

The comparison has 36 host runs at most. Each case defaults to one run per arm, six turns and 120 seconds; the second command explicitly repeats each arm three times. The cost ceiling is checked between launches and can be exceeded by the one in-flight run. `--trust-plugin` is the operator's trust assertion for this reviewed local package.

The case's `allowed_tools: [Read, Skill]` is a request, **not an exact exposed-tool allowlist**. In Claude Code 2.1.314 a live trace also exposed `Task`, `Glob`, `Grep`, `TaskStop`, and `ToolSearch`. The documented case schema has no `execution.disallowed_tools`; use the CLI exclusions above to deny helper dispatch, Workflow orchestration, messaging, and tool discovery. Do not add an unsupported case field as a substitute. Check each retained trace's initialization tool list and tool calls: any exposed launch/helper/discovery tool invalidates the intended tool restriction and requires investigation. The offline tests cannot establish that the provider honored these exclusions.

No scaffold, real MCP server, shell, or write-tool grant is requested. Do not add `--allow-tools`, `--allow-real-servers`, or `--mocks off`: that would change the experiment and could enable actual work. Reports remain local with `--no-publish`; this does not make inference offline or free. `--output-dir` keeps aggregate results outside the generated package. `--keep-temp` retains the separate temporary trace files referenced by `tracePath` in `aggregate-result.json`. Archive only those `trace.jsonl` files beside the report, retaining their original-path mapping, before temporary-directory cleanup. Do not copy whole temporary directories or their homes/configuration, which can contain credentials. Retaining an aggregate report alone does not retain the response or tool trace. Default `evals/results/` directories are ignored, not durable evidence or package content.

## Interpret the result

All graders are deterministic native `tool_used` or `regex` checks; there are no judge-model calls. Positive selection checks look for the `Skill` tool with the exact `agent-collab` name, optionally namespaced. Negative checks require zero such calls. Output checks inspect only the final answer. The synthetic case also enforces the six-line output shape to reject contradictory duplicate answers.

In two-arm mode, positive skill checks are deliberately `with-only` indicators, excluded from the quality score. Inspect their verdicts: a successful aggregate exit alone does not prove activation. Negative selection checks use `arm: both`. Compare outcome scores and the per-case delta separately from selection, and inspect tool traces for unexpected behavior. Record the commit, CLI version, requested model/effort, observed model and any effective-effort evidence, settings, individual grader verdicts, cost, and duration alongside results. Exclude partial runs, API failures, timeouts, and observed model/effort mismatches from comparisons; never substitute another pair. One run is only a smoke check; even three repeats on six handcrafted prompts provide limited evidence.

Distinguish requested effort from observed effective effort. Verifying that the child received `CLAUDE_CODE_EFFORT_LEVEL=xhigh` establishes the requested setting, not provider execution at that effort. The inspected Claude Code 2.1.314 native eval trace/result identifies the model but supplies no observed Stop-hook effort evidence. Without independent evidence, report effective effort as **unverified**: results remain behavioral diagnostics, not a fully attested model-and-effort comparison. The native harness's isolated `dontAsk` permission mode is also a test configuration, not evidence about Agent Collab's full-capability peer permissions.

The Python unit test checks the requested tool configuration, documented command flags, and grading oracle without model calls. It does not verify the tools actually exposed or constitute a behavioral pass. Runtime tests cover lifecycle invariants separately. A real workflow outcome evaluation remains separate work requiring actual peer inference, verified evidence, complete closure, and a comparable no-plugin arm.

The initial five-case smoke on 2026-10-30 passed all five skill-selection checks but failed the informational explanation regex. A subsequent single-case rerun passed. The first response trace was deleted because `--keep-temp` was omitted, so the cause of that failure remains undiagnosed; the rerun does not erase it. These runs preceded the explicit tool exclusions above and do not validate the intended helper-free configuration.

A later retained run with the explicit exclusions again passed all five selection checks and exposed only `Glob`, `Grep`, `Read`, and `Skill`, with no helper calls. Its informational answer correctly described a separate Claude or Codex agent providing a second opinion, proving a grader false negative for that retained response. The grader now recognizes separate/second-opinion wording and accepts either word order, with regression examples for that response, other legitimate paraphrases, unrelated answers, and solo reviews. This evidence does not establish the cause of the earlier missing-trace failure.

The initial synthetic comparison with three runs per arm was failed calibration, not efficacy evidence: the plugin arm invoked the skill in zero of three runs. Its quality scores were 6/7, 5/7, and 1, while baseline scores were all 1. One response also treated F2_ACTUAL as the truth of the candidate claim despite correctly calculating the function result in prose. The prompt now explicitly requests Skill invocation solely for guidance, overrides launch steps for this exercise, and defines both ACTUAL fields as the function's boolean return values. The code, candidate findings, and objective oracle remain unchanged. A new comparison must verify actual guidance loading before interpreting its delta.

The corrected 2026-10-30 bounded runs passed all five activation cases and all six synthetic outcome runs (three per arm). Every plugin outcome run loaded the skill. Both outcome arms scored 1.0, with a native reported delta of zero: this fixture demonstrated no improvement over the no-plugin arm. Retained traces exposed only `Glob`, `Grep`, `Read`, and `Skill`, reported no helpers, and named only `claude-opus-5-5` in API model usage. The child environment requested `xhigh`; observed Stop effort remains unavailable. These are narrow behavioral diagnostics, not an attested quality, cost, or latency benchmark.

## Codex inputs

The `prompt.md` bodies and expected judgments are host-neutral and can be reused for a Codex evaluation, but this directory provides no native Codex execution harness. Claude results must not be reported as Codex activation evidence. Use fresh, equivalently configured Codex sessions with and without the plugin and supply the same prompt bodies; record actual reads of the canonical `SKILL.md` as the selection signal, because Codex does not expose Claude's `Skill` tool. Withhold all process launch, network, MCP, and helper tools in the evaluating environment before using the trigger prompts. A read-only shell is still launch-capable; an ordinary `codex exec --sandbox read-only` invocation is not an equivalent isolation setup. Preserve the selected Codex model and effort in both arms and apply the same objective outcome oracle. Until such a controlled runner is available and executed, report Codex behavioral coverage as outstanding.

## Maintenance

`case.yaml` uses JSON syntax, a YAML-compatible representation that the native harness reads and the standard-library tests can inspect without adding a parser dependency. Prompts stay readable in separate Markdown files. Keep evaluation data under this directory and synchronize it to the package root. Do not embed eval cases in skill instructions or permit the agent under test to read sibling cases or graders.
