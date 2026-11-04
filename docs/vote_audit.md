# Saved vote audit

The eight checked-in run summaries were compared with the latest logged run's three completed member-vote tasks and the completed intermediate `vote_summary`. The audit also verifies that the last `summary_final` output equals the saved JSON, since `23_5_run2` contains an earlier interrupted run in its appended log. File SHA-256 hashes and source line numbers are in `vote_audit.json`. The run-time loader repeats these checks rather than trusting the audit file blindly.

| Meeting/run | Final summary versus three member voting tasks | Intermediate versus voting tasks |
|---|---|---|
| 23_2 run 1 | Match | Match |
| 23_2 run 2 | Match | Match |
| 23_5 run 1 | Match | Match |
| 23_5 run 2 | Match | Match |
| 24_3 run 1 | Match | Match |
| 24_3 run 2 | Match | Match |
| 24_9 run 1 | **Mismatch:** final lists `4.62%` for all; tasks each say `-0.25%` | Match: all `-0.25%` |
| 24_9 run 2 | Match | Match |

The malformed `4.62%` is plausibly a rate level, not a signed quarter-point change. The code recovers the **simulated** `-0.25%` changes from the actual vote tasks, with provenance; it does not replace them with the real FOMC outcome or modify `rate_summary.json`. If the task outputs, intermediate summary, or saved final transcript fail verification, the meeting is excluded. This audit checks saved-output consistency, not whether the original data or prompts were truly point-in-time.
