# Market-fusion extension: historical sandbox audit

> **Current status:** The owner now reports a completed four-meeting Gemini ensemble and manual review. See [Extension results and evaluation](EXTENSION_RESULTS.md) for those owner-reported results, provenance and limitations. The sandbox did not have access to the Windows results or private cache. The audit below preserves earlier sandbox findings and does not independently verify the completed run.

## Implemented and tested

The extension uses year-grouped expanding market folds, out-of-sample nested log-linear fusion, bootstrap evaluation and a guarded voting ensemble. An audit of all eight checked-in final JSON summaries against original vote tasks found one corrupted summary; its three `-0.25%` simulated votes are recoverable from verified source tasks and their agreeing intermediate summary. The source files are not changed. A no-non-hold evaluation now reports N/A rather than passing empty arrays to scikit-learn; undefined class metrics and degenerate MCC are distinguished from zero. See `vote_audit.md` and machine-readable evidence in `vote_audit.json`.

The ensemble offers OpenAI and Google Gemini as distinct providers, with provider-aware cache records, a hard per-invocation request counter that includes retries, and a single-request optional probe. Gemini uses the official `google-genai` SDK and `GEMINI_API_KEY`; the SDK's automatic retries are disabled so application retries can be counted. Invalid responses are preserved and halt processing. The user delegated the tie decision and approved preserving the signed vote while flagging the reported inconsistency. Accordingly, each 1–1–1 sample remains unresolved, is reported, and is excluded from conditional ensemble probabilities; no vote is overwritten or reinterpreted. A meeting with no resolved sample has no ensemble probability. A tie in highest probability is excluded from the common hard-classification evaluation intersection rather than broken by class ordering. Resolved-only probabilities and evaluation exclusions can introduce selection bias. The parser reads the final signed vote line and does not establish whether the prose supports the vote. `--flag_vote MEETING:SAMPLE:ROLE` records a human-reported inconsistency without altering the vote; other responses remain unreviewed. The user reported a successful live Gemini probe and three role-specific requests on Windows; those cached responses are inaccessible from this sandbox and have not been independently verified. Local SDK tests use mock transports only. Existing output artifacts are not overwritten; use a separate output directory.

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

No actual ablation table, fitted weights, or claims of improvement are independently verified in this sandbox: the user's downloaded FRED data and API key are on their computer, not in this sandbox. Tests use synthetic probabilities strictly for plumbing. The checked-in committee has only four dates in two years, so after nested training there are at most two test meetings and only two prior fusion-training meetings. Any resulting numerical table is not a credible research conclusion; bootstrap intervals are conditional on predictions and particularly unstable here. The test subset may lack classes, making class-specific metrics and MCC undefined.

The market prior is statistical, not an LLM agent, and departs from the paper's all-agent design. α and β are conditional predictive coefficients, not causal evidence or normalized trust. The original baseline already references FedWatch, so signals are not independent. Gemini vote sampling uses cached discussions and an approximate role prompt rather than a full CrewAI rerun; memory, retrieval and system prompts differ. Original prompts contain dates, including a hard-coded 2024 vote date across meetings, and the model may know historical outcomes. FRED observation dates alone do not prove release time or vintage. Near-zero-rate regimes make spreads less informative. No anonymized or post-cutoff experiment has been performed.

## Offline verification record

On 2026-11-05 (user local date), the checked-in sources and remote branch were inspected before edits. `git status --short --branch`, `git ls-remote --heads origin`, and `gh pr list --state all` were used to inventory Git state; all local changed files were verified byte-for-byte against remote commit `b8603fd` before fast-forwarding the working branch. A separate source backup was made before changes; no existing results, caches or backups were removed. In this sandbox `results/`, `results_backups/`, `data/fred/` and `cache/ensemble/` were absent; the user's Windows files were not accessible.

A read-only baseline audit used `load_baseline(load_meetings())` and `audit_summary` on all eight checked-in summaries. The dry-run command `python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --k 7 --temp 0.7 --dry_run --max_calls 0` reported four meetings, 84 total calls, and 84 uncached **in this sandbox**; the Windows cache may reduce the count if it matches. `python -m extension.run_all --stage diagnose --provider gemini --model gemini-3.1-flash-lite` ran offline using a fake key and mock transport. The complete offline suite (`pytest -p no:cacheprovider -q extension/tests`) passed **37 tests** with socket connections blocked in the test process and in the diagnostic subprocess. No live provider request was made in this audit. Actual Windows token usage, costs, cache compatibility and the reported regional response have not been independently verified.

After the user delegated the unresolved-tie policy and chose to retain and flag the signed vote in a reported inconsistency, offline regression tests were rerun with network sockets blocked: **45 passed**. The sandbox uses `google-genai` 2.34.0. Provider model revisions and stochastic generation are not guaranteed reproducible by model name and temperature alone; retain the cached responses and run metadata rather than claiming an exact replay.

## Next steps

The earlier one-meeting smoke was superseded by the owner's reported completed K=7 ensemble. Its artifacts and the 42-response manual review are not available in this sandbox. Preserve all source responses and existing audit files, verify the reported counts against the artifacts when available, and see the [future-work checklist](EXTENSION_RESULTS.md#future-work--todo--not-implemented). Further paid experiments require a separately approved budget and are not part of this documentation update.
