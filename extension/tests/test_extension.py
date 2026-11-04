import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from extension.market_data import SERIES, market_features
from extension.market_prior import walk_forward
from extension.fusion import smooth_votes, fuse, fit_fusion, walk_forward_fusion
from extension.committee import parse_vote, majority, load_baseline
from extension.ensemble import discussions, run_ensemble, CallBudget
from extension.evaluate import metrics, ece


def market_frame():
    dates = pd.date_range('2010-01-01', '2010-04-01', freq='D')
    return pd.DataFrame({s: np.arange(len(dates), dtype=float) + i for i,s in enumerate(SERIES)}, index=dates)


def test_market_timestamps_and_future_mutation():
    data = market_frame()
    date = pd.Timestamp('2010-03-05')
    data.loc['2010-03-03', 'DTB3'] = np.nan
    f, times = market_features(data, date)
    assert all(t <= date - pd.Timedelta(days=2) for t in times.values())
    assert times['DTB3'] == pd.Timestamp('2010-03-02')
    assert times['DTB3_30d'] <= pd.Timestamp('2010-02-01')
    assert f['d3m_30'] == data.loc['2010-03-02', 'DTB3'] - data.loc['2010-02-01', 'DTB3']
    data.loc[data.index > date - pd.Timedelta(days=2), :] = 99999
    assert market_features(data, date)[0] == f


def synthetic():
    dates = pd.date_range('2000-01-01', periods=12, freq='YS')
    return pd.DataFrame({'Date': dates, 'y': [0,1,2]*4, 's3m':np.arange(12), 's6m':np.arange(12)*.1,
                         'd3m_30':np.arange(12)*.2, 'd2y_30':np.arange(12)*-.1})


def test_market_walk_forward_fold_is_strict_and_outcome_change_no_leak():
    df = synthetic(); result = walk_forward(df, min_years=3, min_meetings=3)
    assert (pd.to_datetime(result.train_max_date).dt.year < result.test_year).all()
    assert np.allclose(result[['p_mkt_cut','p_mkt_hold','p_mkt_hike']].sum(axis=1), 1)
    altered = df.copy(); altered.loc[altered.Date.dt.year >= 2006, 'y'] = 2
    r2 = walk_forward(altered, min_years=3, min_meetings=3)
    a = result[result.Date.dt.year == 2006].iloc[0]
    b = r2[r2.Date.dt.year == 2006].iloc[0]
    np.testing.assert_allclose(a[['p_mkt_cut','p_mkt_hold','p_mkt_hike']].to_numpy(float), b[['p_mkt_cut','p_mkt_hold','p_mkt_hike']].to_numpy(float))


def test_missing_classes_and_single_class_training():
    df = synthetic(); df.loc[df.Date.dt.year < 2003, 'y'] = 1
    r = walk_forward(df, min_years=3, min_meetings=3)
    p = r.iloc[0][['p_mkt_cut','p_mkt_hold','p_mkt_hike']].to_numpy(float)
    assert np.all(p > 0) and np.isclose(p.sum(), 1) and p.argmax() == 1


def test_nested_fusion_and_normalization():
    market = walk_forward(synthetic(), min_years=3, min_meetings=3)
    market[['p_comm_cut','p_comm_hold','p_comm_hike']] = np.array([smooth_votes([int(y)]) for y in market.y])
    result, folds = walk_forward_fusion(market)
    assert (pd.to_datetime(folds.train_max_date).dt.year < folds.year).all()
    assert np.allclose(result[['p_fused_cut','p_fused_hold','p_fused_hike']].sum(axis=1), 1)
    broken = market.copy(); broken.loc[0,'train_max_date'] = broken.loc[0,'Date']
    with pytest.raises(ValueError, match='not out of sample'):
        walk_forward_fusion(broken)
    p = smooth_votes([0,1,2,1]); assert np.isclose(p.sum(), 1)
    theta = fit_fusion(np.tile(p,(3,1)), np.tile(p,(3,1)), [0,1,2])
    assert np.isclose(fuse(p,p,theta).sum(), 1)


def test_vote_parsing_and_reject_level():
    assert parse_vote('-0.25%') == 0 and parse_vote('0.00%') == 1 and parse_vote('+0.50%') == 2
    with pytest.raises(ValueError): parse_vote('4.62%')
    with pytest.raises(ValueError): majority([0,1,2])


def test_reliability_metrics_and_always_hold():
    y = np.array([0,1,1,2]); p = np.tile(smooth_votes([1]), (4,1))
    m = metrics(y,p)
    assert m['accuracy'] == np.mean(y == 1)
    assert 0 <= m['ece'] <= 1 and 0 <= ece(y,p,adaptive=True) <= 1


def test_existing_logs_and_baseline():
    context = discussions('chain_of_draft/results/23_2_run1/fomc_simulation.md')
    assert set(context) == {'regional','academic','central'} and all(context.values())
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01','2024-09-18'])})
    data, rejected = load_baseline(meetings)
    assert len(data) == 1 and len(rejected) == 1 and '4.62%' in rejected[0]


def test_ensemble_cache_resume_and_guard(tmp_path, monkeypatch):
    import sys
    from extension import ensemble
    calls = []
    class Error(Exception): pass
    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='INTEREST RATE VOTE: 0.00%'))], usage=None)
    fake = SimpleNamespace(OpenAI=Client, RateLimitError=Error, APIConnectionError=Error, APITimeoutError=Error, InternalServerError=Error)
    monkeypatch.setitem(sys.modules, 'openai', fake)
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    args = (meetings, 'chain_of_draft/results', tmp_path, 'fake-model', .7, 2)
    with pytest.raises(RuntimeError, match='no calls made'):
        run_ensemble(*args, max_calls=5)
    assert not calls
    result = run_ensemble(*args, max_calls=6)
    assert len(calls) == 6 and len(list(tmp_path.rglob('*.json'))) == 6
    assert np.isclose(result.p_ens_hold.iloc[0], 2.5/3.5)
    again = run_ensemble(*args, max_calls=0)
    assert len(calls) == 6
    pd.testing.assert_frame_equal(result, again)
    budget = CallBudget(1); budget.acquire()
    with pytest.raises(RuntimeError): budget.acquire()
