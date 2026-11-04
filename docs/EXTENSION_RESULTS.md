# FOMC Simulator Extension: Results and Evaluation

## Executive summary

This extension adds a Gemini-based voting ensemble to the original Fedsight AI FOMC simulator, together with a statistical market-prior/fusion pipeline, controlled API execution, reproducible artifacts, and committee-level auditing. The ensemble samples **three role-specific votes from saved discussions**; it does not repeat the entire original CrewAI deliberation. The work is a research and project demonstration, not financial advice.

The project owner reports completing a four-meeting, seven-sample Gemini ensemble and manually reviewing 42 agent responses. The reported experiment and audit counts below are useful project evidence, **but the Windows result directory, cached responses, and manual review records were not available in the documentation environment**. They are owner-reported, not independently verified against those artifacts here. Arithmetic and implementation details were checked against the reported numbers and repository code. Preserve the artifacts and verify the tables against them before treating this document as an independently audited record.

**No claim is made that the extension is more accurate than the original project.** The extension's resolution rate is not accuracy, and the reported manual contradiction rate is exploratory.

## Original project and reported benchmarks

The original project uses CrewAI agents to model the FOMC's decision process. An analyst and economist contribute analysis and policy options, while three clustered policymaker roles discuss and vote. Its base, in-context-learning and Chain of Draft (CoD) variants are described in the [root README](../README.md#high-level-overview). Inputs include economic indicators and narrative sources; the project also references FedWatch expectations, so the original system should not be characterized as market-blind.

The original README and the project-supplied Fedsight paper report the following; they are **original-project/benchmark reports**, not extension measurements:

| Reported result | Evaluation context | Attribution |
|---|---|---|
| 62.5% accuracy | 2023–2024 meetings | Original project's Ordinal Random Forest comparator |
| 75% accuracy | 2018 test set | MiniFed benchmark, as reported by the original project |
| 93.75% prediction accuracy | Original 2023–2024 evaluation: 16 meetings, 5 simulations per meeting | Fedsight AI + CoD, as reported by the original project |
| 100% accuracy | Same 2018 test set used in the original benchmark comparison | Fedsight AI + CoD, **not** MiniFed |

The original README is the repository source for these reported numbers. The project-supplied nine-page paper was also inspected: its backtesting setup is on page 3, results on page 4, and 2018 benchmark table on page 7. The attached paper is not distributed in this repository; its source audit is summarized in [extension_recon.md](extension_recon.md). The original README cites Seok et al.'s [MiniFed paper](https://arxiv.org/abs/2410.18012) and Xu et al.'s [Chain of Draft paper](https://arxiv.org/abs/2502.18600); neither cited paper's full text was independently available in this documentation environment. The MiniFed percentage above is attributed to the Fedsight project report, not asserted as a newly verified finding from the MiniFed paper. The original evaluation's reported performance has not been independently reproduced here.

## Completed Gemini ensemble: owner-reported results

| Item | Reported value |
|---|---|
| Provider and model | Google Gemini, `gemini-3.1-flash-lite` |
| Selected meetings | February 2023, May 2023, March 2024, September 2024 |
| Samples and roles | 7 samples per meeting; 3 agents per sample |
| Individual agent votes | 4 × 7 × 3 = **84** |
| Completed resume invocation | **68** cached responses reused; **16** new responses |
| Attempts in that invocation | **19** provider attempts, including **3** failed attempts |
| Final invocation status | **16** new responses and **0 failed jobs** |
| Output location reported by owner | `results/gemini_full_k7_resume_20261105/` |

The 19 attempts are for the final invocation only; the historical attempt, failure, token, and cost totals for the 68 earlier cache records cannot be reconstructed from that invocation alone. Failed *attempts* and failed *jobs* are different: a job can succeed after an attempt fails. No total expenditure is inferred.

The roles are **Regional Pragmatists**, **Academic Balancers**, and **Central Policymakers**. They independently receive a role-specific vote prompt with the saved discussion context. Their outputs are vote replays conditioned on those saved discussions, not independent full committee simulations. The repo's checked-in discussions cover dates 2023-02-01, 2023-05-03, 2024-03-20 and 2024-09-18; the specific completion artifacts remain unavailable here for independent matching.

### Committee resolution and probability interpretation

For each sample, a committee decision requires at least two of the three signed final votes to agree on cut, hold or hike. A cut/hold/hike split is **unresolved**. It is neither automatically a hold nor an arbitrary tie-break. The owner's reported counts are:

| Meeting | Resolved samples | Unresolved samples |
|---|---:|---:|
| February 2023 | 0 / 7 | 7 / 7 |
| May 2023 | 2 / 7 | 5 / 7 |
| March 2024 | 7 / 7 | 0 / 7 |
| September 2024 | 7 / 7 | 0 / 7 |
| **Total** | **16 / 28** | **12 / 28** |

**16/28 is a sample-resolution rate, not prediction accuracy.** February has no resolved samples, so the implementation emits no ensemble probability for that meeting. For a meeting with `N > 0` resolved sample decisions, the class estimate is:

```text
P(c) = (n_c + 0.5) / (N + 1.5)
```

Here `n_c` is the number of resolved committee decisions in class `c`, and `N` is the number of resolved decisions, not the seven requested samples. These are additively smoothed **conditional-on-resolution** estimates, not calibrated probabilities of the actual Federal Reserve decision. Excluding unresolved samples can introduce selection bias. Separately, a tie in the highest class probability is excluded from the common six-variant hard-decision evaluation rather than silently broken by class order.

### Exploratory reasoning-versus-vote audit

The owner reports manually examining **42** cached responses from the February and May 2023 meeting contexts:

| Manual classification | Responses |
|---|---:|
| Explanation and final vote consistent | 27 |
| Contradictory | 15 |
| Unclear | 0 |
| **Total reviewed** | **42** |

The apparent contradiction rate is **15/42 = 35.7%** (rounded). This is an exploratory, manually classified sample of only two contexts, without an independently available reviewed CSV, adjudication or inter-rater reliability. It is **not** a general estimate of Gemini behavior.

In a reported example from May 2023, sample 3, all three explanations supported a cut, while the extracted final votes produced a 2–1 majority for a hike. The source responses were not accessible here, and the manual review's sample-index convention could not be independently verified. The parser extracts the explicit final vote line; it does **not** semantically verify that the explanation supports the vote. The implementation retains that signed vote and can record a human-reported inconsistency flag; it does not rewrite the response or infer a replacement vote from prose.

## Original project versus extension

| Dimension | Original project | This extension | Interpretation |
|---|---|---|---|
| Architecture | CrewAI agents analyze, deliberate and vote; base, in-context and CoD variants | Three independent role-vote replays from frozen discussions; separate market prior and fusion pipeline | Extension reuses context and is **not** a full CrewAI replay. |
| Model / provider | Project paper reports GPT-4o agents; current checked-in code varies by variant, and the original result model provenance is not established by aggregate logs | Owner reports Google `gemini-3.1-flash-lite` for the ensemble | Different model/configuration; do not assume the checked-in CoD configuration generated the paper's reported results. |
| Meetings | Reported 16 meetings in 2023–2024 | 4 selected meetings | Different samples and coverage; not directly comparable. |
| Repeated samples | 5 full simulations per meeting, as reported | 7 vote-replay samples per meeting | Different procedures and sample counts. |
| Voting | Individual role votes and original reported simulation metrics | Strict majority of three signed votes; 1–1–1 unresolved | Extension adds an explicit committee rule and coverage accounting; it is not the original evaluation rule. |
| Reliability controls | Original scripts save per-run summaries and report repeatability metrics; do not assume equivalent API guards | Provider-aware cache, attempt budget, shared Gemini rate limiter, retry controls and audit files | Controls make request execution more controlled; they do not establish predictive quality. |
| Testing | Original README describes simulation/backtesting and metrics; an equivalent test-suite count was not established | Owner reports 54 offline tests passed in 72.27 seconds | Offline tests check software behavior, not live model performance. |
| Evaluation | Original project reports predictive accuracy, agent accuracy, stability and other metrics | Owner reports completion, committee resolution and exploratory manual reasoning-vote classifications; market/fusion evaluation code is implemented | No comparable extension accuracy result is independently verified in the available artifacts. |

## Reliability, reproducibility and output artifacts

The Gemini limiter is shared across threads in a process and allows at most **10 request starts per rolling 60 seconds**, including retries. It serializes synchronous Gemini requests, tracks completion times conservatively, honors valid `Retry-After` across workers and uses capped exponential backoff with jitter for retryable rate-limit errors. A server delay over 300 seconds stops further Gemini attempts in that process rather than being shortened. Non-retryable errors are not retried. `--max_calls` limits attempts per invocation, including retries; it is not a financial cap. These controls reduce rate-limit risk and avoid unnecessary calls, but cannot account for other processes or guarantee a provider will not return a 429.

The reported completed directory is `results/gemini_full_k7_resume_20261105/`. The owner identifies these artifacts:

| File | Purpose |
|---|---|
| `ensemble.csv` | Smoothed class estimates for meetings with at least one resolved sample, with coverage and configuration metadata |
| `ensemble_meetings.csv` | Per-meeting resolved/unresolved sample counts and decision status |
| `ensemble_votes.csv` | Role-level signed votes, sample status, source, review flag and available token counts |
| `ensemble_run.json` | Per-invocation request, cache, failure and recorded usage summary |
| `run_config.json` | Invocation arguments and environment/package-version provenance |

For exact invocation flags, versions, usage and cache provenance, inspect the saved `run_config.json` and `ensemble_run.json`; the exact completed command and environment cannot be independently reconstructed here. Retain the raw cache, original audit CSV and output directory unchanged. The [extension guide](../EXTENSION_README.md) describes dependencies, offline tests, output protection and safe configuration. A fresh output directory is required for any future execution; no new execution is needed to read existing reports.

## Offline testing

The owner reports running:

```powershell
python -m pytest extension/tests -v
```

Reported Windows result: **54 passed in 72.27 seconds**. Separately, during this documentation update, the sandbox suite passed **54 tests in 37.27 seconds** with socket connections blocked and UTF-8 mode disabled. These were **offline test runs**, not new live Gemini experiments. They are separate from the completed Gemini ensemble. A test count or passing test suite does not validate a statistical conclusion or semantic correctness of all generated reasoning.

## Limitations

- Four selected meetings and 28 samples do not provide a credible population-level accuracy comparison or statistically significant performance claim. The conditional resolution coverage varies sharply by meeting.
- The reasoning audit is owner-reported, manual, unverified here, confined to two contexts, and not an automatically validated semantic evaluation.
- Original discussions, prompts and model knowledge can expose dates or historical outcomes; the original CoD source has hard-coded 2024 dates in vote prompts. The extension replays saved discussions, which may already contain preliminary votes. This is not a prospective or leakage-free test.
- Model versions, prompts, temperatures and provider behavior affect reproducibility; a model name does not guarantee exact replay. Historical token/cost and failed-attempt records may be incomplete.
- The market prior and nested fusion are implemented, but no final six-variant comparative metric or fitted-weight result is independently verified here. With the checked-in four dates across two years, nested evaluation has at most two later-year test meetings before exclusions and very little prior fusion training. Bootstrap intervals would be unstable.
- Market expectations are already referenced in the original project, so a fusion coefficient is neither a causal effect nor evidence of independent information. FRED observation dates do not establish publication timing or point-in-time vintages.

## Future work / TODO — not implemented

- [ ] Populate the 42 manually reviewed reasoning-vote classifications into a **separate** reviewed audit CSV; preserve the original audit CSV and all raw cached JSON files.
- [ ] Build a semantic reasoning-versus-final-vote consistency evaluator, with a clear review and validation protocol.
- [ ] Evaluate more meetings and more samples before making general claims.
- [ ] Investigate why some roles produce explanations that contradict their final votes.
- [ ] Study unresolved samples, conditional coverage and potential selection bias.
- [ ] Run a larger evaluation comparable to the original project using the same meetings, sample counts and evaluation metrics, subject to available time and API budget.
