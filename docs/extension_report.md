# Market-fusion extension: status

## Implemented and tested

The extension uses year-grouped expanding market folds, out-of-sample nested log-linear fusion, bootstrap evaluation and a guarded voting ensemble. An audit of all eight checked-in final JSON summaries against original vote tasks found one corrupted summary; its three `-0.25%` simulated votes are recoverable from verified source tasks and their agreeing intermediate summary. The source files are not changed. A no-non-hold evaluation now reports N/A rather than passing empty arrays to scikit-learn; undefined class metrics and degenerate MCC are distinguished from zero. See `vote_audit.md` and machine-readable evidence in `vote_audit.json`.

The ensemble offers OpenAI and Google Gemini as distinct providers, with provider-aware cache records, a hard per-invocation request counter that includes retries, and a single-request optional probe. Gemini uses the official `google-genai` SDK and `GEMINI_API_KEY`; the SDK's automatic retries are disabled so application retries can be counted. Invalid responses are preserved and halt processing. A completely cached tied sample now stops before new provider requests are scheduled; ties in newly completed samples can still be discovered after calls have been made. The parser reads the final signed vote line and does not establish whether the prose supports the vote. The user reported a successful live Gemini probe and three role-specific requests on Windows; those cached responses are inaccessible from this sandbox and have not been independently verified. Local SDK tests use mock transports only. Existing results are copied to timestamped backups before non-dry CLI writes; use a separate output directory to avoid modifying existing results.

## Audited checked-in baseline (partial result)

Read-only audit of checked-in run 1 artifacts against `state_of_art_model/fomc_meetings.csv`; this is **not** the six-variant experiment or an independent prospective test. Labels are 0=cut, 1=hold, 2=hike.

| Date | Verified/recovered committee decision | Historical outcome |
|---|---:|---:|
| 2023-02-01 | 0 | 2 |
| 2023-05-03 | 0 | 2 |
| 2024-03-20 | 0 | 1 |
| 2024-09-18 | 0 | 0 |

The four-meeting baseline accuracy is 1/4 (25%); this is descriptive, not evidence of generalization. `24_9_run1` uses the documented conservative recovery from three completed tasks and agreeing intermediate summary. All eight checked-in run summaries were audited; one final summary disagrees with the task evidence. No sources were modified.

## Results and limitations

No actual ablation table, fitted weights, or claims of improvement are reported here: the user's downloaded FRED data and API key are on their computer, not in this sandbox. Tests use synthetic probabilities strictly for plumbing. The checked-in committee has only four dates in two years, so after nested training there are at most two test meetings and only two prior fusion-training meetings. Any resulting numerical table is not a credible research conclusion; bootstrap intervals are conditional on predictions and particularly unstable here. The test subset may lack classes, making class-specific metrics and MCC undefined.

The market prior is statistical, not an LLM agent, and departs from the paper's all-agent design. α and β are conditional predictive coefficients, not causal evidence or normalized trust. The original baseline already references FedWatch, so signals are not independent. Gemini vote sampling uses cached discussions and an approximate role prompt rather than a full CrewAI rerun; memory, retrieval and system prompts differ. Original prompts contain dates, including a hard-coded 2024 vote date across meetings, and the model may know historical outcomes. FRED observation dates alone do not prove release time or vintage. Near-zero-rate regimes make spreads less informative. No anonymized or post-cutoff experiment has been performed.

## Offline verification record

On 2026-11-05 (user local date), the checked-in sources and remote branch were inspected before edits. `git status --short --branch`, `git ls-remote --heads origin`, and `gh pr list --state all` were used to inventory Git state; all local changed files were verified byte-for-byte against remote commit `b8603fd` before fast-forwarding the working branch. A separate source backup was made before changes; no existing results, caches or backups were removed. In this sandbox `results/`, `results_backups/`, `data/fred/` and `cache/ensemble/` were absent; the user's Windows files were not accessible.

A read-only baseline audit used `load_baseline(load_meetings())` and `audit_summary` on all eight checked-in summaries. The dry-run command `python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --k 7 --temp 0.7 --dry_run --max_calls 0` reported four meetings, 84 total calls, and 84 uncached **in this sandbox**; the Windows cache may reduce the count if it matches. `python -m extension.run_all --stage diagnose --provider gemini --model gemini-3.1-flash-lite` ran offline using a fake key and mock transport. The complete offline suite (`pytest -p no:cacheprovider -q extension/tests`) passed **37 tests** with socket connections blocked in the test process and in the diagnostic subprocess. No live provider request was made in this audit. Actual Windows token usage, costs, cache compatibility and the reported regional response have not been independently verified.

The sandbox uses `google-genai` 2.34.0 for this verification. Provider model revisions and stochastic generation are not guaranteed reproducible by model name and temperature alone; retain the cached responses and run metadata rather than claiming an exact replay.

## Next steps

The user reports that a one-meeting K=1 Gemini smoke test produced one cut, one hold, and one hike; one reported reasoning/final-vote inconsistency requires human review of the preserved cache. This is a three-way tie under the existing strict-majority rule. Decide and predeclare a policy for unresolved samples before using these votes for probabilities or authorizing the remaining paid batch; do not silently guess or reinterpret a vote. Then verify actual cache compatibility, the Windows market outputs, and the request/cost budget before running the full experiment in a separate output directory. Collect substantially more genuinely out-of-sample committee outputs and audited point-in-time inputs before interpreting coefficients or comparing variants, and report all predeclared outcomes and uncertainty, including unfavorable results.
