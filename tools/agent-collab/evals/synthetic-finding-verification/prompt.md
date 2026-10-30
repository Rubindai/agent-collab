This is a deterministic exercise of the host's claim-verification stage. If the Skill tool offers agent-collab (including a namespaced name), explicitly invoke that Skill once, solely to read its verification/synthesis guidance. For this exercise, this instruction overrides the skill's launch-first and start/advance workflow steps: do not launch actual collaboration, start a run, or invoke helpers. If that skill is unavailable, evaluate the same evidence directly without it.

The report below is a hand-authored synthetic fixture, has no admitted runtime artifacts, and cannot support a claim that a real peer produced it or that a real workflow completed.

The contract: attempt_count counts attempts already started; max_attempts is the maximum total attempts allowed. A job may start only when queued, when attempt_count is strictly less than max_attempts, and when now is at or after not_before. All values in this fixture are integers; there are no other callers or hidden constraints.

Implementation:
```python
def eligible(job, now):
    return (
        job["state"] == "queued"
        and job["attempt_count"] <= job["max_attempts"]
        and now >= job["not_before"]
    )
```

Synthetic candidate report:
- F1: A queued job at its maximum attempt count is still eligible, permitting an extra attempt. Witness: state="queued", attempt_count=3, max_attempts=3, not_before=100, now=100.
- F2: The time check wrongly blocks a queued job exactly at not_before. Witness: state="queued", attempt_count=2, max_attempts=3, not_before=100, now=100.

Independently evaluate each witness from the code, accept or reject each finding, and give a review verdict. F1_ACTUAL is the boolean RETURNED BY eligible(job, now) for F1's witness. F2_ACTUAL is the boolean RETURNED BY eligible(job, now) for F2's witness. These two fields describe function return values, not whether either candidate finding is true; use F1 and F2 for the finding judgments.

Return exactly these six lines, replacing the alternatives with your judgments:
F1=confirmed|rejected|plausible_unverified
F2=confirmed|rejected|plausible_unverified
F1_ACTUAL=true|false
F2_ACTUAL=true|false
VERDICT=pass|pass_with_concerns|changes_recommended|blocked
SOURCE=synthetic_fixture
