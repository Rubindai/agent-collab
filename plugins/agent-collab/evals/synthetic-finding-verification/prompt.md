This is a deterministic exercise of the host's claim-verification stage, not a request to launch collaboration. Read Agent Collab's verification/synthesis guidance if it is available, then evaluate the synthetic evidence below. Do not start a run, invoke helpers, or claim a real peer produced this report. The report is a hand-authored fixture, has no admitted runtime artifacts, and cannot support a statement that a real workflow completed.

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

Independently evaluate each witness from the code, accept or reject each finding, and give a review verdict. Return exactly these six lines, replacing the alternatives with your judgments:
F1=confirmed|rejected|plausible_unverified
F2=confirmed|rejected|plausible_unverified
F1_ACTUAL=true|false
F2_ACTUAL=true|false
VERDICT=pass|pass_with_concerns|changes_recommended|blocked
SOURCE=synthetic_fixture
