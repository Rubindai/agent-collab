"""Offline checks of eval configuration and the objective grading oracle.

These do not verify the provider's actual exposed tools or skill activation.
"""
import json
import re
import shlex
import unittest
from pathlib import Path


EVAL_ROOT = Path(__file__).resolve().parents[1] / "tools" / "agent-collab" / "evals"


def cases():
    return {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in EVAL_ROOT.glob("*/case.yaml")
    }


def regex_passes(grader, message):
    # The suite intentionally uses the common Python/JavaScript regex subset.
    flags = sum({"i": re.I, "m": re.M}[flag] for flag in grader.get("flags", ""))
    return bool(re.search(grader["pattern"], message, flags))


class BehavioralEvalTests(unittest.TestCase):
    def test_cases_request_only_read_and_skill_with_finite_limits(self):
        suite = cases()
        self.assertEqual(len(suite), 6)
        for name, case in suite.items():
            with self.subTest(case=name):
                self.assertEqual(case["schema_version"], "1.1")
                self.assertEqual(case["name"], name)
                self.assertEqual(case["runs"], 1)
                self.assertNotIn("context", case)  # no shell scaffold or replay
                execution = case["execution"]
                self.assertEqual(set(execution["allowed_tools"]), {"Read", "Skill"})
                self.assertGreater(execution["max_turns"], 0)
                self.assertLessEqual(execution["max_turns"], 6)
                self.assertGreater(execution["timeout_seconds"], 0)
                self.assertLessEqual(execution["timeout_seconds"], 120)
                self.assertEqual(set(execution), {"allowed_tools", "max_turns", "timeout_seconds"})
                self.assertTrue((EVAL_ROOT / name / "prompt.md").read_text().strip())
                self.assertTrue(case["graders"])
                self.assertTrue(all(g["type"] in {"regex", "tool_used"} for g in case["graders"]))
        self.assertFalse(list(EVAL_ROOT.rglob("mocks")))

    def test_documented_commands_deny_helper_tools_and_preserve_evidence(self):
        readme = (EVAL_ROOT / "README.md").read_text(encoding="utf-8")
        commands = re.findall(r"^claude plugin eval .*?(?=\n```)", readme, re.M | re.S)
        self.assertEqual(len(commands), 2)
        denied = {"Agent", "Task", "Workflow", "ToolSearch", "SendMessage", "TaskStop"}
        for command in commands:
            args = shlex.split(command.replace("\\\n", " "))
            index = args.index("--disallow-tools") + 1
            end = next(i for i in range(index, len(args)) if args[i].startswith("--"))
            self.assertTrue(denied <= set(args[index:end]))
            self.assertIn("--keep-temp", args)
            self.assertIn("--no-publish", args)
            self.assertEqual(args[args.index("--output-dir") + 1], "$EVAL_OUTPUT_DIR")
            self.assertNotIn("--allow-tools", args)
            self.assertNotIn("--allow-real-servers", args)

    def test_selection_checks_do_not_inflate_baseline_quality(self):
        suite = cases()
        positives = {"explicit-request", "implicit-substantial-review", "synthetic-finding-verification"}
        for name, case in suite.items():
            selection = [g for g in case["graders"] if g["type"] == "tool_used"]
            self.assertEqual(len(selection), 1)
            grader = selection[0]
            self.assertEqual(grader["tool"], "Skill")
            for skill in ("agent-collab", "agent-collab:agent-collab"):
                self.assertRegex(json.dumps({"skill": skill}), grader["input_match"])
            self.assertNotRegex(json.dumps({"skill": "unrelated:other"}), grader["input_match"])
            if name in positives:
                self.assertEqual((grader["min"], grader["arm"]), (1, "with-only"))
            else:
                self.assertEqual((grader["min"], grader["max"], grader["arm"]), (0, 0, "both"))
            self.assertTrue(any(g["type"] == "regex" for g in case["graders"]))

    def test_informational_grader_accepts_peer_paraphrases_without_word_order(self):
        grader = next(g for g in cases()["informational-question"]["graders"] if g["type"] == "regex")
        valid = (
            '"Use agent collab" asks the assistant to run the agent-collab plugin, '
            'which starts a separate Claude or Codex agent to do the review on its '
            'own so you get a second opinion to check against the assistant’s.',
            "It asks for an independent peer to review the work.",
            "A Codex peer supplies an independent review.",
            "The assistant asks another agent for a second opinion.",
            "A separate assistant reviews the change.",
        )
        invalid = (
            "It asks the assistant to correct spelling mistakes.",
            "Separate documentation sections explain installation.",
            "The assistant reviews the change alone.",
            "The assistant conducts an independent review alone.",
            "",
        )
        for answer in valid:
            with self.subTest(answer=answer):
                self.assertTrue(regex_passes(grader, answer))
        for answer in invalid:
            with self.subTest(answer=answer):
                self.assertFalse(regex_passes(grader, answer))

    def test_objective_oracle_rejects_missed_and_invented_findings(self):
        case = cases()["synthetic-finding-verification"]
        prompt = (EVAL_ROOT / case["name"] / "prompt.md").read_text(encoding="utf-8")
        code = re.search(r"```python\n(.*?)\n```", prompt, re.S).group(1)
        namespace = {}
        exec(compile(code, "<synthetic-eval-fixture>", "exec"), {"__builtins__": {}}, namespace)
        # Confirm the actual fixture still produces the oracle's two witnesses.
        for attempts in (3, 2):
            job = {"state": "queued", "attempt_count": attempts, "max_attempts": 3, "not_before": 100}
            self.assertIs(namespace["eligible"](job, 100), True)
        # At the limit the contract prohibits another attempt; below it, at
        # the time boundary, the contract allows one. The candidate labels
        # below therefore retain F1 and reject F2.
        graders = [g for g in case["graders"] if g["type"] == "regex"]
        correct = (
            "F1=confirmed\nF2=rejected\nF1_ACTUAL=true\nF2_ACTUAL=true\n"
            "VERDICT=changes_recommended\nSOURCE=synthetic_fixture\n"
        )
        self.assertTrue(all(regex_passes(g, correct) for g in graders))
        mutations = (
            correct.replace("F1=confirmed", "F1=rejected"),
            correct.replace("F2=rejected", "F2=confirmed"),
            correct.replace("F1_ACTUAL=true", "F1_ACTUAL=false"),
            correct.replace("F2_ACTUAL=true", "F2_ACTUAL=false"),
            correct.replace("VERDICT=changes_recommended", "VERDICT=pass"),
            correct.replace("SOURCE=synthetic_fixture", "SOURCE=real_peer"),
            correct + "F2=confirmed\n",
            "",
        )
        for answer in mutations:
            with self.subTest(answer=answer):
                self.assertFalse(all(regex_passes(g, answer) for g in graders))


if __name__ == "__main__":
    unittest.main()
