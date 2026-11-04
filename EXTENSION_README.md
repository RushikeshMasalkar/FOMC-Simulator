# Market fusion extension: Gemini and OpenAI

This extension is separate from the original CrewAI code. It uses existing meeting transcripts, an expanding-window statistical market prior, nested fusion, and bootstrap diagnostics. Labels are `0=cut`, `1=hold`, `2=hike`. **It is research code, not financial advice.**

## Research status and historical vote audit

`docs/vote_audit.json` records the audit of all eight checked-in summaries, with exact original task values, source paths, line numbers and SHA-256 hashes. Seven final summaries match the task outputs. In `24_9_run1`, the final JSON has `4.62%` (apparently a rate level), but the three original voting task outputs and intermediate vote summary each say `-0.25%`. The loader only recovers an invalid final summary when all three tasks and the intermediate summary agree and the log's final output matches the saved JSON. It does **not** change the source JSON. Ambiguous, missing, mismatching, or duplicated source records are rejected. Fusion/evaluation write the decisions and audit evidence to `results/baseline_provenance.json`.

The checked-in data cover only four meeting dates across two years. After nested fusion and recovery, **at most two meetings** can be evaluated. Fusion is trained on only two earlier meeting predictions. This is a functionality demonstration, not a credible comparison. The transcript dates and model knowledge can leak historical outcomes; prompts in the original chain-of-draft code hard-code September 18, 2024. The paper and baseline already reference market expectations. FRED observation dates are not proof of release timing or a point-in-time vintage. No real-data results or live Gemini request were made by the developer in the sandbox.

## Dependencies

Use Python 3.11 and `python -m pip install -r requirements-extension.txt`. The Google provider uses the official `google-genai` SDK and `GEMINI_API_KEY`; OpenAI remains available with `OPENAI_API_KEY`. You do not need CrewAI, Ollama, or a local AI model for this extension. The Gemini SDK installation can be added to an already-installed environment with:

```powershell
python -m pip install "google-genai>=2.0,<3.0"
```

All examples below run from the project root. Existing market data are read from `data/fred/`. `--stage market` downloads FRED public CSVs without a key; users who have already run it should not repeat it. Market settings and the meeting-file hash must match those recorded in `results/market_metadata.json`.

Before a CLI stage that writes results, the entire existing `results/` directory is copied to a timestamped `results_backups/` folder. Downloaded FRED inputs and previous caches are not deleted or overwritten. Still make your own independent backup of your project, results, data and cache before applying updates; a filesystem copy can fail if the disk is full. Dry runs, the local diagnostic, and the probe do not write results. The diagnostic makes no network call; the probe makes one paid or quota-limited request. Never run simultaneous processes sharing one cache.

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

Success prints provider, model, one request, the validated vote class and numeric token usage; raw response text is not printed. A failed Gemini request reports the HTTP status, a safely allowlisted endpoint and a sanitized classification of the response body; it never prints raw response bodies, query parameters, authentication headers, proxy addresses or key values. An HTML 400 page alone does not prove whether Google or an intermediary produced it. Authentication, model access, account quota, network/proxy configuration and request validity are possible causes. See `docs/gemini_probe_debug.md`. We have tested provider logic with mocks only, not a live Gemini request.

Dry-run estimate (free, no key required):

```powershell
python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --dry_run --k 7 --temp 0.7 --max_calls 0
```

For four checked-in transcripts and no Gemini cache, it shows 84 requests (four meetings × seven samples × three member votes). Cached responses reduce this number. The cache is provider/model/temperature/role/prompt-specific under `cache/ensemble/gemini/`; legacy caches without provider metadata are never silently reused. Provider output, usage and prompt hash are stored, including malformed outputs for review. Invalid cached responses halt the run and are never silently retried. Back up and inspect them before deciding whether to move a specific invalid cache file out of the cache and deliberately resample; that costs another request. Request retries use bounded exponential backoff for rate limits and transient errors; Google SDK automatic retries are disabled so each attempt is counted. A crash after the provider processes a request but before the cache is saved can still cause a charge on retry.

After reviewing price, quota and the probe result, run the three-meeting smoke test:

```powershell
python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --limit_meetings 3 --k 7 --temp 0.7 --max_calls 70 --workers 1
```

It requires 63 successful requests if uncached, with up to seven retries allowed by the invocation budget. `--max_calls` is a **per-process request cap**, not a token or monetary cap. `--workers 1` is slower but gentler on rate limits. Inspect all printed votes and cached JSON responses before completing the batch. The code samples the three member votes from a frozen discussion log and applies strict-majority aggregation; it is **not** a full CrewAI rerun and does not preserve all CrewAI retrieval, system prompts or memory state.

Then inspect actual remaining cache misses:

```powershell
python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --dry_run --k 7 --temp 0.7 --max_calls 0
```

If the smoke test completed and settings are unchanged, 21 successful calls remain. With room for four retries:

```powershell
python -m extension.run_all --stage ensemble --provider gemini --model gemini-3.1-flash-lite --k 7 --temp 0.7 --max_calls 25 --workers 1 --approve_full_batch
```

If more than 25 uncached requests remain, the command stops before spending; first inspect the dry-run count and failures, then choose a budget. Old un-namespaced OpenAI caches are preserved but are not automatically reused because their provider provenance is unclear. Existing `results/ensemble.csv` files without provider metadata are not silently assumed to be OpenAI; use a properly audited `--ensemble_csv` or regenerate.

## Final evaluation

```powershell
python -m extension.run_all --stage eval --provider gemini --model gemini-3.1-flash-lite --k 7 --temp 0.7
```

The model, provider, K and temperature must match the ensemble run. The six variants use the same intersection of scoreable meetings. Outputs include:

- `results/ablation.md`, `results/ablation.csv`: metrics and conditional 95% meeting-bootstrap intervals.
- `results/evaluation_predictions.csv`: meeting-level probabilities, labels and dates.
- `results/baseline_provenance.json`: task-source recovery evidence.
- `results/all_fusion_theta.csv`, `results/fusion_theta.csv`: fold parameters; weights are conditional associations, not causal contributions or normalized trust shares.
- `results/confusion_and_classes.json`: confusion matrices and class-specific precision/recall, with null for unavailable values.
- `results/figures/`: reliability diagram and parameter plot.

The paired full-minus-replication MCC and log-loss differences are in the Markdown report. The report can include a pre/post knowledge-cutoff split only when a validated `--cutoff YYYY-MM-DD` is supplied. `results/run_config.json` logs stage configuration and package versions, not secrets. Preserve each predeclared ablation in a separate `--outdir`; do not select models using the test results. The output directory is backed up before it is written.

OpenAI remains selectable with `--provider openai --model gpt-4o-mini` and `OPENAI_API_KEY`; the provider is restricted to the official OpenAI endpoint. The old OpenAI-only cache layout is preserved on disk but cannot be trusted as provider-identified; new calls use separate namespaces. `--stage probe` and paid ensemble runs are opt-in. The default provider remains OpenAI for backward CLI compatibility, so **always specify `--provider gemini`** for this workflow.
