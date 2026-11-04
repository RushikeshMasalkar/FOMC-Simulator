"""Paired, meeting-resampled evaluation and figures."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import accuracy_score, f1_score, matthews_corrcoef, confusion_matrix, precision_recall_fscore_support
from .fusion import smooth_votes

CLASSES = ('cut', 'hold', 'hike')
METRICS = ('accuracy', 'macro_f1', 'mcc', 'brier', 'log_loss', 'ece', 'ece_adaptive')


def ece(y, p, bins=10, adaptive=False):
    confidence = np.max(p, axis=1)
    correct = (np.argmax(p, axis=1) == y).astype(float)
    if adaptive:
        groups = np.array_split(np.argsort(confidence, kind='stable'), min(bins, len(y)))
    else:
        indices = np.minimum((confidence * bins).astype(int), bins - 1)
        groups = [np.flatnonzero(indices == b) for b in range(bins)]
    return float(sum(len(g) / len(y) * abs(correct[g].mean() - confidence[g].mean()) for g in groups if len(g)))


def metrics(y, p):
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    if len(y) == 0 or p.shape != (len(y), 3) or not np.isfinite(p).all() or (p < 0).any() or not np.allclose(p.sum(axis=1), 1):
        raise ValueError('invalid labels/probabilities')
    pred = p.argmax(axis=1)
    return {'accuracy': accuracy_score(y, pred),
            'macro_f1': f1_score(y, pred, labels=[0, 1, 2], average='macro', zero_division=0),
            'mcc': matthews_corrcoef(y, pred) if len(np.unique(y)) > 1 and len(np.unique(pred)) > 1 else 0.0,
            'brier': np.mean(np.sum((p - np.eye(3)[y]) ** 2, axis=1)),
            'log_loss': -np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1))),
            'ece': ece(y, p), 'ece_adaptive': ece(y, p, adaptive=True)}


def confidence_intervals(y, variants, n_boot=1000, seed=42):
    if n_boot < 1000:
        raise ValueError('at least 1000 bootstrap resamples required')
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(y), size=(n_boot, len(y)))
    scores, samples = {}, {}
    for name, p in variants.items():
        scores[name] = metrics(y, p)
        boot = [metrics(y[ix], p[ix]) for ix in indices]
        samples[name] = {m: np.array([x[m] for x in boot]) for m in METRICS}
        for m in METRICS:
            scores[name][m + '_lo'], scores[name][m + '_hi'] = np.quantile(samples[name][m], [.025, .975])
    return pd.DataFrame.from_dict(scores, orient='index'), samples


def evaluate(df, variants, outdir='results', n_boot=1000, seed=42, theta=None,
             cutoff=None, title='Full ablation'):
    out = Path(outdir); figdir = out / 'figures'
    figdir.mkdir(parents=True, exist_ok=True)
    df = df.sort_values('Date').reset_index(drop=True)
    y = df.y.to_numpy(dtype=int)
    if not len(y):
        raise ValueError('No common evaluation meetings')
    probs = {}
    for name, source in variants.items():
        probs[name] = np.tile(smooth_votes([1]), (len(y), 1)) if source == 'always' else df[[source + x for x in CLASSES]].to_numpy(dtype=float)
    hold_rate = np.mean(y == 1)
    assert np.isclose(metrics(y, probs['Always-hold'])['accuracy'], hold_rate)
    table, bootstrap = confidence_intervals(y, probs, n_boot, seed)
    table.index.name = 'variant'
    table.to_csv(out / 'ablation.csv')
    lines = [f'# {title}', '', f'N = {len(y)} common meetings; hold rate = {hold_rate:.3f}.',
             f'Percentile 95% CIs from {n_boot} paired meeting bootstrap resamples, seed {seed}.',
             'Brier is the sum across three classes; ECE is top-label confidence, 10 equal-width bins.',
             'Adaptive ECE uses up to 10 equal-count bins. Macro-F1 includes all three classes, with zero for missing classes.',
             'Always-hold and hard replication use smoothed one-hot probabilities (0.2, 0.6, 0.2); other variants use their predicted probabilities.', '',
             '| Variant | Accuracy | Macro-F1 | MCC | Brier | Log loss | ECE | Adaptive ECE |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, r in table.iterrows():
        fmt = [f'{r[m]:.3f} [{r[m+"_lo"]:.3f}, {r[m+"_hi"]:.3f}]' for m in METRICS]
        lines.append('| ' + name + ' | ' + ' | '.join(fmt) + ' |')
    if 'K-ensemble + fusion' in probs and 'Replication (single run)' in probs:
        lines.extend(['', '## Paired bootstrap: full − replication', ''])
        for m in ('mcc', 'log_loss'):
            diff = bootstrap['K-ensemble + fusion'][m] - bootstrap['Replication (single run)'][m]
            lo, hi = np.quantile(diff, [.025, .975])
            value = table.loc['K-ensemble + fusion', m] - table.loc['Replication (single run)', m]
            lines.append(f'- {m}: {value:.3f} [{lo:.3f}, {hi:.3f}] (negative log-loss difference is better).')
    if table.loc['Market-only logit', 'log_loss'] >= table.loc['Always-hold', 'log_loss']:
        lines.extend(['', '**Sanity check:** market-only did not beat smoothed always-hold on log loss. '
                      'Inspect fold sizes, class balance, regime shifts, source availability and feature timing; no test-driven retuning was applied.'])
    lines.extend(['', '## Confusion matrices and per-class precision/recall', '',
                  'Matrices have true labels as rows and predicted labels as columns, ordered cut / hold / hike. Non-hold matrices omit true-hold rows only.', ''])
    diagnostics = {}
    for name, p in probs.items():
        pred = p.argmax(axis=1)
        cm = confusion_matrix(y, pred, labels=[0,1,2])
        nonhold = confusion_matrix(y[y != 1], pred[y != 1], labels=[0,1,2])[[0,2], :]
        prec, rec, _, _ = precision_recall_fscore_support(y, pred, labels=[0,1,2], zero_division=0)
        diagnostics[name] = {'all': cm.tolist(), 'non_hold': nonhold.tolist(), 'precision': prec.tolist(), 'recall': rec.tolist()}
        lines.extend([f'**{name}**  ', f'All: `{cm.tolist()}`  ', f'Non-hold: `{nonhold.tolist()}`  ',
                      f'Precision (cut, hold, hike): `{np.round(prec, 3).tolist()}`; recall: `{np.round(rec, 3).tolist()}`.', ''])
    (out / 'confusion_and_classes.json').write_text(json.dumps(diagnostics, indent=2))
    lines.extend(['## Near-zero-rate calendar regimes', '',
                  'Calendar proxy: 2009–2015 and 2020–2021 (not an observation-level rate threshold).'])
    years = df.Date.dt.year
    for label, mask in [('ZLB proxy', years.between(2009, 2015) | years.between(2020, 2021)),
                        ('Other years', ~(years.between(2009, 2015) | years.between(2020, 2021)))]:
        ix = mask.to_numpy()
        lines.append(f'- {label}: n={ix.sum()}')
        if ix.any():
            for name, p in probs.items():
                m = metrics(y[ix], p[ix]); lines.append(f'  - {name}: accuracy {m["accuracy"]:.3f}, log loss {m["log_loss"]:.3f}')
    if cutoff:
        split_date = pd.Timestamp(cutoff)
        lines.extend(['## User-supplied model knowledge cutoff', '', f'Cutoff: {split_date.date()}; metrics are descriptive, not a leakage test.', ''])
        for label, mask in [('On/before cutoff', df.Date <= split_date), ('After cutoff', df.Date > split_date)]:
            ix = mask.to_numpy()
            lines.append(f'- {label}: n={ix.sum()}')
            if ix.any():
                for name, p in probs.items():
                    m = metrics(y[ix], p[ix]); lines.append(f'  - {name}: accuracy {m["accuracy"]:.3f}, MCC {m["mcc"]:.3f}, log loss {m["log_loss"]:.3f}')
    else:
        lines.extend(['## Model knowledge cutoff', '', 'Unknown; no pre/post-cutoff comparison was made.'])
    # Reliability: top-label confidence; empty bins omitted.
    plt.figure(figsize=(5, 5)); plt.plot([0,1], [0,1], 'k--', label='Ideal')
    graph = [('K-ensemble only', 'K-ensemble + fusion'), ('Replication (single run)', 'Replication + fusion')]
    selected = next(((a,b) for a,b in graph if a in probs and b in probs), None)
    if selected:
        for name in selected:
            p = probs[name]; conf = p.max(axis=1); correct = (p.argmax(axis=1) == y)
            bucket = np.minimum((conf * 10).astype(int), 9)
            xs, ys = [], []
            for b in range(10):
                mask = bucket == b
                if mask.any(): xs.append(conf[mask].mean()); ys.append(correct[mask].mean())
            plt.plot(xs, ys, 'o-', label=name)
    plt.xlabel('Mean confidence'); plt.ylabel('Accuracy'); plt.xlim(0,1); plt.ylim(0,1); plt.legend(); plt.tight_layout()
    plt.savefig(figdir / 'reliability.png', dpi=150); plt.close()
    lines.extend(['', '## Figures', '', '![Reliability](figures/reliability.png)'])
    if theta is not None and not theta.empty:
        theta.to_csv(out / 'fusion_theta.csv', index=False)
        if (theta.train_n < 40).any():
            lines.extend(['', '**Warning:** at least one fusion fold has fewer than 40 training meetings; coefficients and metrics are highly unstable.'])
        plt.figure(figsize=(7,4))
        for col in ('alpha', 'beta', 'b_cut', 'b_hike'):
            plt.plot(theta.year, theta[col], marker='o', label=col)
        plt.xlabel('Test year'); plt.ylabel('Parameter'); plt.legend(); plt.tight_layout()
        plt.savefig(figdir / 'fusion_theta.png', dpi=150); plt.close()
        lines.extend(['', '![Fold parameters](figures/fusion_theta.png)', '',
                      'Coefficients are joint log-linear weights, not normalized shares or causal attribution.'])
    (out / 'ablation.md').write_text('\n'.join(lines) + '\n')
    return table
