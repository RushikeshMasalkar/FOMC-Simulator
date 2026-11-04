"""Small nested walk-forward log-linear fusion model."""
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp


def smooth_votes(votes):
    votes = np.asarray(votes, dtype=int)
    if votes.ndim != 1 or len(votes) == 0 or not np.isin(votes, [0, 1, 2]).all():
        raise ValueError('votes must be a nonempty vector of 0, 1, or 2')
    return (np.bincount(votes, minlength=3) + 0.5) / (len(votes) + 1.5)


def _logits(pc, pm, theta):
    pc, pm = np.asarray(pc, dtype=float), np.asarray(pm, dtype=float)
    if pc.shape != pm.shape or pc.shape[-1] != 3 or not np.isfinite(pc).all() or not np.isfinite(pm).all():
        raise ValueError('expected finite, matching probability arrays with three classes')
    if (pc < 0).any() or (pm < 0).any():
        raise ValueError('negative probabilities')
    a, b, bc, bh = theta
    return a * np.log(np.clip(pc, 1e-12, 1)) + b * np.log(np.clip(pm, 1e-12, 1)) + np.array([bc, 0, bh])


def fuse(pc, pm, theta):
    z = _logits(pc, pm, theta)
    return np.exp(z - logsumexp(z, axis=-1, keepdims=True))


def fit_fusion(pc, pm, y):
    y = np.asarray(y, dtype=int)
    if len(y) == 0 or not np.isin(y, [0, 1, 2]).all():
        raise ValueError('fusion needs nonempty labeled training data')
    pc, pm = np.asarray(pc), np.asarray(pm)
    if pc.shape != (len(y), 3) or pm.shape != (len(y), 3):
        raise ValueError('fusion arrays must be n by 3')
    def objective(theta):
        z = _logits(pc, pm, theta)
        return np.mean(logsumexp(z, axis=1) - z[np.arange(len(y)), y]) + .01 * np.dot(theta, theta)
    res = minimize(objective, [1., 1., 0., 0.], method='L-BFGS-B')
    if not res.success:
        raise RuntimeError(f'fusion optimization failed: {res.message}')
    return res.x


def walk_forward_fusion(df, comm_prefix='p_comm_', min_fusion=1):
    df = df.copy().sort_values('Date')
    df.Date = pd.to_datetime(df.Date)
    pc = [comm_prefix + c for c in ('cut', 'hold', 'hike')]
    pm = ['p_mkt_' + c for c in ('cut', 'hold', 'hike')]
    out, folds = [], []
    for year in sorted(df.Date.dt.year.unique()):
        train = df[df.Date.dt.year < year]
        test = df[df.Date.dt.year == year]
        if len(train) < min_fusion:
            continue
        assert (train.Date.dt.year < year).all()
        # Each market prediction must itself come from an earlier-year-only fit.
        for part in (train, test):
            maxdates = pd.to_datetime(part.train_max_date, errors='raise')
            if not (maxdates.dt.year < part.Date.dt.year).all():
                raise ValueError('market predictions are not out of sample by year')
        theta = fit_fusion(train[pc], train[pm], train.y)
        probs = fuse(test[pc], test[pm], theta)
        assert np.allclose(probs.sum(axis=1), 1)
        result = test.copy()
        result[['p_fused_cut', 'p_fused_hold', 'p_fused_hike']] = probs
        out.append(result)
        folds.append({'year': int(year), 'train_n': len(train), 'train_max_date': train.Date.max(),
                      'alpha': theta[0], 'beta': theta[1], 'b_cut': theta[2], 'b_hike': theta[3]})
    if not out:
        raise ValueError('No fusion folds: need earlier-year out-of-sample market and committee predictions')
    return pd.concat(out, ignore_index=True), pd.DataFrame(folds)
