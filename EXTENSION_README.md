# FOMC extension: Gemini ensemble and market fusion

This extension is separate from the original CrewAI code. It uses existing meeting transcripts, an expanding-window statistical market prior, nested fusion, and bootstrap diagnostics. Labels are `0=cut`, `1=hold`, `2=hike`. **It is research code, not financial advice.**

## Completed ensemble and documentation status

The project owner reports a completed Google Gemini `gemini-3.1-flash-lite` ensemble: **4 meetings × 7 samples × 3 roles = 84 individual votes**. The final resume invocation reportedly reused **68** cached responses, generated **16** responses and made **19** provider attempts, including **3** failed attempts; it completed with **0 failed jobs**. Across 28 committee samples, **16 resolved** and **12 were unresolved**. These are coverage figures, **not prediction accuracy**.

The owner also reports an exploratory manual review of 42 February/May 2023 responses: 27 consistent, 15 contradictory and 0 unclear (15/42 = 35.7%). These manual classifications are preliminary and limited to two meeting contexts. The completed result directory is reported as `results/gemini_full_k7_resume_20261105/`. It and the associated private cache/review records were not accessible in the documentation sandbox, so these results are **user-reported, not independently checked against artifacts here**. No new live experiment was performed for this documentation.

See [Extension results and comparison](docs/EXTENSION_RESULTS.md) for the meeting-by-meeting resolution table, smoothing formula, audit example, original-project benchmarks, limitations and future work. The extension samples votes from saved discussions; it does not repeat the original full CrewAI simulation.

### Offline tests

From the project root, with the extension dependencies installed:

```powershell
python -m pytest extension/tests -v
```

The owner reports **54 passed in 72.27 seconds** in an offline test run. This is a software-test result, not a new Gemini experiment or a prediction-accuracy measurement. Tests use mocks/synthetic fixtures and should not require live provider calls. See the detailed results document for the scope and provenance caveats.

## Research status and historical vote audit

`docs/vote_audit.json` records the audit of all eight checked-in summaries, with exact original task values, source paths, line numbers and SHA-256 hashes. Seven final summaries match the task outputs. In `24_9_run1`, the final JSON has `4.62%` (apparently a rate level), but the three original voting task outputs and intermediate vote summary each say `-0.25%`. The loader only recovers an invalid final summary when all three tasks and the intermediate summary agree and the log's final output matches the saved JSON. It does **not** change the source JSON. Ambiguous, missing, mismatching, or duplicated source records are rejected. Fusion/evaluation write the decisions and audit evidence to `results/baseline_provenance.json`.

The checked-in data cover only four meeting dates across two years. After nested fusion and recovery, **at most two meetings** can be evaluated before exclusions. For the single-run path, at most two earlier meeting predictions are available for fusion training; unresolved ensemble samples may reduce this further. This is a functionality demonstration, not a credible comparison. The transcript dates and model knowledge can leak historical outcomes; prompts in the original chain-of-draft code hard-code September 18, 2024. The paper and baseline already reference market expectations. FRED observation dates are not proof of release timing or a point-in-time vintage. The owner's completed Gemini ensemble and private result artifacts are on Windows and have not been independently examined in the documentation sandbox.

## Dependencies

Use Python 3.11 and `python -m pip install -r requirements-extension.txt`. The Google provider uses the official `google-genai` SDK and `GEMINI_API_KEY`; OpenAI remains available with `OPENAI_API_KEY`. You do not need CrewAI, Ollama, or a local AI model for this extension. The Gemini SDK installation can be added to an already-installed environment with:

```powershell
python -m pip install "google-genai>=2.0,<3.0"
```

All examples below run from the project root. Existing market data are read from `data/fred/`. `--stage market` downloads FRED public CSVs without a key; users who have already run it should not repeat it. Market settings and the meeting-file hash must match those recorded in `results/market_metadata.json`.

Before a CLI stage writes results, it refuses to overwrite existing stage outputs. Existing output directories are copied to timestamped sibling backup directories before writes, and existing run configurations are not overwritten. Downloaded FRED inputs and prior caches are not intentionally deleted or overwritten. Still make an independent backup before applying updates; a filesystem copy can fail if the disk is full. Dry runs, the local diagnostic, and the probe do not write results. The diagnostic makes no network call; the probe makes one paid or quota-limited request. Never run simultaneous processes sharing one cache.

## Fusion: no LLM cost

```powershell
python -m extension.run_all --stage fusion
```

The stage reads your existing `results/market_oos.csv`, prints any recovery, and writes `results/phase2/ablation.md`, `ablation.csv`, `predictions.csv` and figures. If there are no true non-hold meetings, its non-hold confusion matrix is N/A, and valid hold metrics are preserved. Undefined per-class precision/recall and degenerate MCC are N/A; macro-F1 still uses all three classes and sets missing class F1 to zero. The 1000 paired meeting-bootstrap intervals are conditional on already fitted predictions and are unreliable in tiny samples. MCC intervals omit undefined replicates; paired intervals indicate their count. Brier is summed over the classes; ECE is top-label confidence with 10 fixed or adaptive bins. Hard predictions use smoothed probabilities.

Model training uses only earlier calendar years and fold-local scaling. Default market initial window is 5 prior years AND 40 meetings. Fusion trains only on previous years' out-of-sample market and committee predictions, with a default minimum of one fusion pair; this is **not** statistically adequate. Keep settings consistent across stages and predeclare any changes. An independent audited baseline CSV (`Date,comm_decision`) is supported with `--baseline_csv`; extra audited historical committee outputs are necessary for credible inference.

## Gemini API and guarded vote sampling

Create a personal API key at <https://aistudio.google.com/apikey>. Verify model access, quotas and current pricing in your Google account. Free tier availability is account-dependent. The following PowerShell prompt sets a session-only key without normally displaying it; do not send your key to anyone or commit it.

```powershell
$env:GEMINI_API_KEY = ([System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString))).Password
python -c "import os; print('Gemini key is set' if os.getenv('GEMINI_API_KEY') else 'Gemini key is NOT set')"
```

First, check local Gemini configuration without a network request or secret output:

```powershell
python -m extension.run_all --stage diagnose --provider gemini --model gemini-3.1-flash-lite
```

It prints only whether the key is present, whether it contains whitespace, SDK version, configured endpoint/API version, and proxy environment variable names; it never prints the key or proxy values. System proxies may not appear in the environment variable list.

A single optional real API connectivity request can incur charges and is **not cached**:

```powershell
python -m extension.run_all --stage probe --provider gemini --model gemini-3.1-flash-lite --max_calls 1
```

Success prints provider, model, one request, the validated vote class and numeric token usage; raw response text is not printed. A failed Gemini request reports the HTTP status, a safely allowlisted endpoint and a sanitized classification of the response body; it never prints raw response bodies, query parameters, authentication headers, proxy addresses or key values. An HTML 400 page alone does not prove whether Google or an intermediary produced it. Authentication, model access, account quota, network/proxy configuration and request validity are possible causes. See `docs/gemini_probe_debug.md`. The provider logic was tested offline with mocks in the sandbox; the owner separately reports live Gemini execution on Windows.

### Approved tie and review policy

A sample has a committee decision only when at least two of the three role votes agree. A 1–1–1 cut/hold/hike split is **unresolved**, never a hold or an arbitrary tie-break. The ensemble distribution uses the existing half-count smoothing applied only to resolved sample decisions. It is therefore **conditional on resolution**; report the number unresolved and possible selection bias. A meeting with no resolved sample has no ensemble probability and cannot be scored. A tie in the highest ensemble or other model probabilities is also unresolved for hard classification; evaluation excludes that date from the common six-variant intersection and records it, rather than using `argmax` to choose the first class. Probabilistic training can still use an available conditional distribution with tied maxima.

The signed final line remains the recorded vote. The parser does **not** automatically establish whether prose is consistent with that line. Use `--flag_vote MEETING:SAMPLE:ROLE` for each human-reported inconsistency, for example `--flag_vote 23_2:0:regional`, after reviewing the cached response. A flag must identify a matching, already-cached record; otherwise execution stops before provider requests. Flagging does not rewrite, remove, reinterpret or resample the response. Other responses remain `not_reviewed`; this is not evidence that they are consistent. Preserve the flag on every invocation that writes an audit.

### Before paid calls

Run a no-cost cache estimate:

```powershell
python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --k 7 --temp 0.7 --dry_run --max_calls 0
```

The four checked-in transcripts require 84 successful requests if uncached. A matching K=1 sample-zero cache can be reused for K=7 because K is not part of the cache key. Each role record must match provider, model, temperature, prompt hash, meeting, sample and role. Invalid or inconsistent-provenance cached records halt before new calls, are retained, and are not silently retried. Inspect the cached transcripts; do not remove or modify them to force an output.

In an earlier smoke test, the owner reported a reasoning/final-vote inconsistency in `23_2:0:regional`; the raw response was not independently available here. The approved policy retains the signed vote and permits a human-reported flag without rewriting it. The completed ensemble and later manual audit are described above; do not rerun them merely to produce this documentation.

Any **future** paid K=7 batch must be explicitly authorized after the cache count and actual cost implications are reviewed. For three meetings it requires at most 63 successful requests from an empty cache; the fourth meeting adds 21. `--max_calls` is a **per-process attempt cap**, including retries—not a token or monetary cap. Inspect the 3-meeting smoke output before authorizing/completing the rest. The code replays votes from frozen discussions and is **not** a full CrewAI rerun.

Gemini requests use a conservative, thread-safe per-process limiter: at most 10 request starts per rolling 60 seconds, including retries. Synchronous Gemini requests are serialized, and completion times are used conservatively to avoid exceeding the start limit. HTTP 429 / `RESOURCE_EXHAUSTED` uses capped exponential backoff with jitter; valid `Retry-After` delays are honored across all workers. An excessive delay over 300 seconds stops further Gemini attempts in that process instead of being shortened. OpenAI does not use the Gemini limiter. The limiter cannot see activity from a previous process, other applications or other machines sharing the quota. Wait at least 60 seconds after the last prior Gemini request before resuming, and avoid concurrent quota-sharing processes; other account limits can still cause 429 errors. Resume with the original cache, provider, model, temperature and prompts, a fresh output directory, and an approved attempt budget. Run the free `--dry_run` first to verify the actual miss count. Cached votes are reused; retries consume the same `--max_calls` budget.

### Outputs and preservation

Choose a fresh output directory for each smoke or full run. The CLI refuses to overwrite existing stage outputs and writes a new timestamped run configuration if one already exists. For final evaluation, copy (do not move) the existing `market_oos.csv` and `market_metadata.json` into the new full-run output directory; leave the original `results/` and `results_backups/` untouched. Run `--stage eval` with the same provider, model, K, temperature and `--outdir` as the completed full ensemble. Do not use `--stage all` to replace existing results.

- `ensemble_votes.csv`: each role's signed class, sample decision/status, source, human-reported review flag and available token counts.
- `ensemble_meetings.csv`: resolved/unresolved sample counts, inconsistency flags and hard-decision status, including meetings with no resolved samples.
- `ensemble.csv`: conditional probabilities for meetings with at least one resolved sample, coverage and provider/model/policy metadata.
- `ensemble_run.json`: this invocation's provider attempts, successful/new and cached records, failures and recorded usage totals. Previous unrecorded failures and costs remain unknown. Failed or invalid invocations also write a safe summary; use a new output directory when resuming.
- `evaluation_exclusions.csv`: top-probability ties and, when available, meetings with no resolved samples. All six compared variants use the same remaining intersection.
- `evaluation_predictions.csv`, `ablation.md`, `ablation.csv`, `confusion_and_classes.json`, `all_fusion_theta.csv`, `fusion_theta.csv` and `figures/`: final comparative outputs when sufficient common data exist.
- `run_config.json` or timestamped `run_config-*.json`: arguments, package versions and provenance, without keys.

Token sums cover only available metadata in cached/new responses; failed attempts, previous invocations and missing usage can be unrecorded. A provider attempt with no response returned to the application may still have incurred charges. A returned response that cannot be saved is counted separately from provider failures; its usage may be unavailable. Output token counts and thinking token counts are reported separately and should not automatically be added to a reported total. Never claim statistical significance from the four-meeting data; nested evaluation has at most two dates before exclusions. Macro-F1 includes all classes, undefined metrics are N/A, and bootstrap intervals are conditional and unstable.

OpenAI remains selectable with `--provider openai --model gpt-4o-mini` and `OPENAI_API_KEY`; the provider is restricted to the official OpenAI endpoint. The old OpenAI-only cache layout is preserved on disk but cannot be trusted as provider-identified; new calls use separate namespaces. `--stage probe` and paid ensemble runs are opt-in. The default provider remains OpenAI for backward CLI compatibility, so **always specify `--provider gemini`** for this workflow.

## Future work / TODO

- [ ] Store the 42 manual classifications in a separate reviewed audit CSV; preserve the original audit CSV and raw cached JSON files.
- [ ] Develop and validate a semantic reasoning-versus-final-vote consistency evaluator.
- [ ] Evaluate additional meetings and samples and investigate role-level contradictions.
- [ ] Study unresolved samples and the resulting selection bias.
- [ ] When time and budget permit, compare against the original project using matched meetings, sample counts and evaluation metrics.

These are future tasks, not part of the reported completed ensemble. See the [full checklist and limitations](docs/EXTENSION_RESULTS.md#future-work--todo--not-implemented).
