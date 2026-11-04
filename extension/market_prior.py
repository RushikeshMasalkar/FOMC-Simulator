"""Expanding, year-grouped market prior with fold-local scaling."""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from .market_data import CORE, EXTRA, market_features

LABELS = {'CUT': 0, 'NO CHANGE': 1, 'HIKE': 2}


def load_meetings(path='state_of_art_model/fomc_meetings.csv'):
    df = pd.read_csv(path, encoding='utf-8')
    df['Date'] = pd.to_datetime(df['Date'], errors='raise')
    if df.Date.duplicated().any() or not set(df.Outcome).issubset(LABELS):
        raise ValueError('meeting dates must be unique and labels recognized')
    df['y'] = df.Outcome.map(LABELS).astype(int)
    return df.sort_values('Date').reset_index(drop=True)


def build_features(meetings, market):
    rows = []
    for _, row in meetings.iterrows():
        features, timestamps = market_features(market, row.Date)
        assert all(t <= row.Date - pd.Timedelta(days=2) for t in timestamps.values())
        rows.append({**row.to_dict(), **features,
                     **{f'timestamp_{k}': v for k, v in timestamps.items()}})
    return pd.DataFrame(rows)


def walk_forward(frame, min_years=5, min_meetings=40, c=0.5, extra=False, floor=1e-3):
    if min_years < 1 or min_meetings < 1 or not 0 < floor < 1/3:
        raise ValueError('invalid training window or probability floor')
    columns = list(CORE) + (list(EXTRA) if extra else [])
    df = frame.copy().sort_values('Date')
    df['Date'] = pd.to_datetime(df['Date'])
    out = []
    for year in sorted(df.Date.dt.year.unique()):
        train = df[df.Date.dt.year < year]
        test = df[df.Date.dt.year == year]
        if len(train) < min_meetings or train.Date.dt.year.nunique() < min_years:
            continue
        assert (train.Date.dt.year < year).all()
        if not np.isfinite(train[columns].to_numpy(dtype=float)).all() or not np.isfinite(test[columns].to_numpy(dtype=float)).all():
            raise ValueError(f'Non-finite features in fold {year}')
        classes = np.sort(train.y.unique())
        probs = np.full((len(test), 3), floor, dtype=float)
        if len(classes) == 1:
            probs[:, classes[0]] = 1.0
        else:
            scale = StandardScaler().fit(train[columns])
            model = LogisticRegression(C=c, max_iter=2000, random_state=42)
            model.fit(scale.transform(train[columns]), train.y)
            probs[:, model.classes_.astype(int)] = model.predict_proba(scale.transform(test[columns]))
        probs = np.maximum(probs, floor)
        probs /= probs.sum(axis=1, keepdims=True)
        for (_, row), p in zip(test.iterrows(), probs):
            out.append({'Date': row.Date, 'y': int(row.y), 'p_mkt_cut': p[0],
                        'p_mkt_hold': p[1], 'p_mkt_hike': p[2], 'train_n': len(train),
                        'train_max_date': train.Date.max(), 'test_year': int(year)})
    if not out:
        raise ValueError('No market folds meet the initial training window')
    return pd.DataFrame(out)
