# Probabilistic market-fusion extension

This extension is separate from the original CrewAI code. It adds a year-by-year expanding-window multinomial logistic market model, a small nested log-linear fusion, a cached-discussion voting ensemble, bootstrap evaluation and leakage checks. Class order everywhere is `cut=0`, `hold=1`, `hike=2`.

**Research status:** real-data results have not been generated here. FRED and LLM API hosts are unavailable in the development sandbox. Only four dates have checked-in committee runs; one run-1 summary contains a rate level (`4.62%`) instead of a signed change and is rejected. Consequently, the checked-in data do not support a meaningful six-variant nested walk-forward study. See `docs/extension_recon.md` and `docs/extension_report.md`. The paper and baseline already use market-implied FedWatch signals; the new prior is not the committee's first exposure to markets.

## Get the code and install

After the session branch is available on the remote:

```bash
git clone https://github.com/RushikeshMasalkar/FOMC-Simulator.git
cd FOMC-Simulator
git fetch origin arena/646b297b-fomc-simulator
git switch --track origin/arena/646b297b-fomc-simulator
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-extension.txt
pytest extension/tests
```

For an existing clone, instead run `git fetch origin` and `git switch --track origin/arena/646b297b-fomc-simulator` (or `git switch arena/646b297b-fomc-simulator` if you already have the local branch). This is a separate session branch, not `main`. Dependencies here do not modify the original project's pins.

## Data and first, zero-LLM-cost results

The market stage downloads public FRED CSVs for DFF, DTB3, DTB6, DGS1, DGS2, DGS10 and VIXCLS to `data/fred/`; if direct CSV fails it attempts `pandas_datareader`. No FRED key is required for these public series. If FRED is blocked, obtain those seven dated CSVs independently and place them at `data/fred/DFF.csv`, `data/fred/DTB3.csv`, etc. Each must have a date column first and a numeric column named exactly as the series, with no duplicate dates. A cached file is reused as-is; delete or refresh it explicitly to update. These local data are ignored by Git.

Run from the repository root:

```bash
python -m extension.run_all --stage market
python -m extension.run_all --stage fusion
```

Market features use the last nonmissing observation on or before two calendar days before each meeting and, for changes, the last observation on or before 30 days before that cutoff. Actual source observation timestamps are in `results/market_features.csv`. Training uses only earlier calendar years, with at least **5 distinct prior years AND 40 prior meetings** by default; each fold fits its own scaler and logit (C=0.5). The FRED CSV snapshot may contain revised data and an observation date is not necessarily its public release timestamp; source vintage and publication timing must be audited separately. DFF is EFFR, and T-bill discount yields are only proxies for expectations, not direct Fed funds futures probabilities.

The first four-row table, bootstrap CIs and diagnostic figures are saved under `results/phase2/`. The default checked-in run-1 loader uses a strict majority of the three signed changes, rejects ties and malformed/ambiguous values, and never switches to run 2 based on performance. This majority is a **new aggregation rule**, not the repository's original individual-vote evaluation. Fusion is trained only on earlier out-of-sample market predictions with matched committee outputs, and skips the first year with no earlier pairs. Its default minimum is only one prior pair to preserve the specified expanding design; **this is not statistically adequate**. Use a predeclared `--min_fusion` (e.g. 40) with a larger history for a more credible study. With the checked-in outputs, at most one 2024 meeting can be scored after fusion training; treat any resulting table as a plumbing demonstration, not a research finding.

To include additional audited single-run decisions, supply `--baseline_csv path.csv` to both `fusion` and `eval`, with unique columns `Date,comm_decision`, ISO dates and decision integers 0/1/2. These must be genuine predictions created before the corresponding outcomes, using a consistent, documented model and aggregation rule; ground-truth labels alone are not predictions. The matching labels still come from `state_of_art_model/fomc_meetings.csv`, or a supplied `--meetings` CSV with `Date,Outcome` and outcomes `CUT`, `NO CHANGE`, `HIKE`. Provide consistent historical predictions for many years before expecting meaningful nested fusion. Existing year/month-based transcript folders are rejected when the calendar contains multiple meetings with the same year/month code.

An optional predeclared market ablation adds curve and VIX: use `--extra_features` on the market run and use a **different `--outdir`** to retain both variants. Do not select a configuration based on the test results. Other predeclared settings: `--min_years`, `--min_meetings`, `--min_fusion`, `--c`, `--seed`, and `--bootstrap` (minimum 1000). Keep settings and data paths consistent across stages. Each invocation appends its configuration and dependency versions to `results/run_config.json`; paths do not contain credentials.

## Ensemble: inspect smoke results before full run

The ensemble uses OpenAI's chat API (`OPENAI_API_KEY` environment variable) with `gpt-4o-mini` by default; use `--model` for another compatible model. **It is not a full CrewAI replay.** The code freezes the three completed discussion outputs in each run-1 log, then separately asks each of the three member roles to vote K times, and aggregates each set of three by strict majority. It corrects the final vote's meeting date in the new prompt. It does not reproduce CrewAI's system prompts, retrieval or memory state; the original discussions may already contain leaked dates, decisions or erroneous inputs. A model/provider change also changes the baseline. Verify this approximation before interpreting results; a faithful crew implementation would require validating all that omitted context. Agent votes within a sample are not independent full committee deliberations.

Estimate calls without credentials or spend:

```bash
python -m extension.run_all --stage ensemble --dry_run --k 7 --temp 0.7 --max_calls 0
```

With four checked-in transcripts this prints 84 uncached voting requests (3 roles × 7 samples × 4 meetings), not 28. Then configure your provider key privately in your shell, not in a tracked file:

```bash
export OPENAI_API_KEY='your-key'  # PowerShell: $env:OPENAI_API_KEY = 'your-key'
# Smoke test: 3 meetings, 63 successful calls minimum; 70 allows some retries.
python -m extension.run_all --stage ensemble --limit_meetings 3 --k 7 --temp 0.7 --max_calls 70
```

Inspect every printed sample and JSON response under `cache/ensemble/` before running more. A bad or ambiguous response causes an explicit error; it stays cached for audit. Repair the parser if justified, or deliberately remove only that cached response after reviewing it before resampling; doing so costs another call. To continue, rerun the same command; completed role outputs are reused. After reviewing the smoke test, run the full batch, for example:

```bash
# On the four checked-in transcripts this needs 21 new successful calls after the smoke run.
python -m extension.run_all --stage ensemble --k 7 --temp 0.7 --max_calls 25 --approve_full_batch
```

`--max_calls` limits API requests **per process invocation**, including retries; default is zero. All cache misses are counted before spending, retries use exponential backoff, SDK automatic retries are disabled, and a bounded thread pool uses `--workers 3` by default. The limit is a request count, **not a dollar or token cap**. Review current model pricing and use a provider-side spending cap. Budget for retries, output lengths and input transcript tokens; model-specific price and runtime depend on the provider. Cache keys include meeting, sample, model, temperature, role and a prompt hash. Writes are atomic after each response; do not run simultaneous processes against the same cache. A network failure or crash after a provider processed a request but before the response was saved can still incur a charge on retry. Only checked-in/preexisting run-1 discussion logs are replayed; this tool does not create new deliberations or run a full crew. A full-batch override is required for more than five meetings. Downloaded caches may contain sensitive content and are Git-ignored.

If you have a larger, independently created and audited ensemble, provide `--ensemble_csv path.csv` at evaluation: unique `Date,p_ens_cut,p_ens_hold,p_ens_hike`, strictly positive rows summing to one. Document K, model, temperature and provenance separately. The implementation cannot establish that an external CSV was genuinely out-of-sample.

## Final evaluation

```bash
python -m extension.run_all --stage eval
# If known and independently verified, add --cutoff YYYY-MM-DD for descriptive pre/post-cutoff splits.
```

The six rows are scored on the identical intersection of dates for which both separately trained, nested fusion variants can predict. Outputs: `results/ablation.md`, `results/ablation.csv`, `results/evaluation_predictions.csv`, `results/all_fusion_theta.csv`, `results/confusion_and_classes.json`, and `results/figures/`. The table includes all requested metrics, 1000 meeting-bootstrap CIs and paired full-minus-replication CIs. Hard decisions use smoothed one-hot probabilities for probabilistic metrics. Brier is the sum of squared errors across classes; ECE is top-label confidence with 10 equal-width bins and the adaptive version uses up to 10 equal-count bins. True non-hold confusion matrices retain all predicted columns. Bootstrap meetings are resampled without re-fitting the models; CIs condition on the fitted predictions and are unreliable with very small N. Fold coefficients are joint log-linear weights, not causal contributions or normalized trust percentages.

`python -m extension.run_all --k 7 --temp 0.7 --max_calls N` runs market, Phase 2 and the ensemble in order, then **stops for manual review**, not final evaluation. Prefer the separate stages above: they enforce a visible Phase 2 checkpoint and smoke-test inspection. Running all stages needs actual FRED data and an LLM key and may incur spending. The output directory is reused, so preserve each completed run separately if comparing predeclared configurations.

The market stage is typically seconds to minutes after download; bootstrapping can take minutes depending on sample size and CPU. LLM time depends on provider latency and rate limits. No real-data runtime or cost was measured in this environment. Dates, actor descriptions, post-meeting information in historical inputs, and model memorization are material limitations; no anonymized or verified post-cutoff test has been performed.
