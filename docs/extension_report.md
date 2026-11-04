# Market-fusion extension: status

## Implemented and tested

The extension uses year-grouped expanding market folds, out-of-sample nested log-linear fusion, bootstrap evaluation and a guarded voting ensemble. An audit of all eight checked-in final JSON summaries against original vote tasks found one corrupted summary; its three `-0.25%` simulated votes are recoverable from verified source tasks and their agreeing intermediate summary. The source files are not changed. A no-non-hold evaluation now reports N/A rather than passing empty arrays to scikit-learn; undefined class metrics and degenerate MCC are distinguished from zero. See `vote_audit.md` and machine-readable evidence in `vote_audit.json`.

The ensemble offers OpenAI and Google Gemini as distinct providers, with provider-aware cache records, a hard per-invocation request counter that includes retries, and a single-request optional probe. Gemini uses the official `google-genai` SDK and `GEMINI_API_KEY`; the SDK's automatic retries are disabled so application retries can be counted. Invalid responses are preserved and halt processing. Google integration was exercised using **mocked** provider responses; no live Gemini request was made, and account/model access is not verified. Existing results are copied to timestamped backups before non-dry CLI writes.

## Results and limitations

No actual ablation table, fitted weights, or claims of improvement are reported here: the user's downloaded FRED data and API key are on their computer, not in this sandbox. Tests use synthetic probabilities strictly for plumbing. The checked-in committee has only four dates in two years, so after nested training there are at most two test meetings and only two prior fusion-training meetings. Any resulting numerical table is not a credible research conclusion; bootstrap intervals are conditional on predictions and particularly unstable here. The test subset may lack classes, making class-specific metrics and MCC undefined.

The market prior is statistical, not an LLM agent, and departs from the paper's all-agent design. α and β are conditional predictive coefficients, not causal evidence or normalized trust. The original baseline already references FedWatch, so signals are not independent. Gemini vote sampling uses cached discussions and an approximate role prompt rather than a full CrewAI rerun; memory, retrieval and system prompts differ. Original prompts contain dates, including a hard-coded 2024 vote date across meetings, and the model may know historical outcomes. FRED observation dates alone do not prove release time or vintage. Near-zero-rate regimes make spreads less informative. No anonymized or post-cutoff experiment has been performed.

## Next steps

Run the provided Windows commands with the user's existing FRED outputs; verify the recovery provenance and inspect a small live Gemini smoke test before a full batch. Collect substantially more genuinely out-of-sample committee outputs and audited point-in-time inputs before interpreting coefficients or comparing variants, and report all predeclared outcomes and uncertainty, including unfavorable results.
