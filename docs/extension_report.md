# Market-fusion extension: implementation and current evidence

## Status

The implementation is available, but the requested real-data experiment is **not complete**. Direct FRED access and its pandas-datareader fallback both failed in the sandbox, and no external LLM key or provider access was available. No LLM calls were made. We therefore do not report an ablation table, confidence intervals, learned coefficients, or claims of improvement. The instructions for reproducing these outputs in a suitable environment are in `EXTENSION_README.md`.

## What was added and checked

The extension downloads or reads cached daily FRED data, computes lagged features and retains per-series observation timestamps. The statistical market prior fits standardized multinomial logistic regression in expanding calendar-year folds, mapping missing classes into a three-class probability vector. Nested fusion uses only previously out-of-sample market predictions and committee outputs; it fits the requested four-parameter regularized log-linear model. Evaluation computes the requested metrics, paired meeting-bootstrap intervals, calibration plots, confusion matrices and per-class diagnostics, with optional cutoff and calendar-ZLB splits.

For the candidate low-cost ensemble, three member vote tasks are approximated from saved discussion outputs rather than sampling the analyst's summary. This is **not** a faithful full CrewAI replay: system prompts, retrieval, knowledge and memory state are not preserved. Each role is a separate provider request, cached to disk; a per-invocation request cap includes retries. The default provider/model differs from the checked-in chain-of-draft Gemini setup unless explicitly configured. See the README before spending.

Unit tests cover feature timestamps and future-data mutation, strict fold chronology, out-of-sample nesting, missing classes, normalization, vote parsing, call budget and cache reuse. The full pipeline was also exercised using **synthetic data only** to check functionality; those numbers are not empirical results. The dry-run estimate is 84 uncached calls for seven samples on the four checked-in transcripts.

## Findings and limitations

- The checked-in paper describes five runs for 16 2023–2024 meetings; detailed files in this checkout cover only four meeting dates, two runs each. One fixed run-1 vote summary has a rate level where a rate change was requested, so it is rejected. The three vote prompts in the checked-in chain-of-draft code hard-code September 18, 2024, even for other meetings. The resulting data are insufficient for a credible nested fusion study.
- FedWatch is already mentioned in the paper and baseline prompts, so the committee and market signals are not independent. The market prior here is a statistical model, not another agent, and departs from the paper's all-agent architecture. α and β would be conditional predictive coefficients, not causal contributions or normalized trust shares. Their actual values are **unknown** until valid runs are completed.
- The sample is very small, especially for class-specific performance, per-year fitting and bootstrap inference. Near the zero lower bound, spreads may be uninformative; the code reports calendar-regime summaries when data permit. Treat bootstrap CIs as conditional on predictions, not uncertainty from refitting the models.
- Market observation timestamps alone do not establish publication time or vintage. Historical prompts and inputs contain dates, identities and possibly decision-revealing content; LLM training may include the actual outcomes. The exact model knowledge cutoff is not established and no anonymized comparison was run. No post-cutoff performance claim is justified.

## Next steps

Supply the seven FRED series with source/vintage provenance or run with FRED access; obtain substantially more historical, date-correct, audited committee outputs; validate cached-vote replay against full CrewAI runs and choose a defensible committee aggregation rule. Set a provider-side spending cap, perform and inspect the smoke test, then run nested folds on a predeclared sample and report all attempted configurations and CIs, including unfavorable results. Audit data releases and perform a genuinely anonymized/post-cutoff evaluation before making forecasting claims.
