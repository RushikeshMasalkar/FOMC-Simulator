"""Load FRED daily observations and construct audited, lagged market features."""
from pathlib import Path
from urllib.request import urlopen
import io
import pandas as pd

SERIES = ('DFF', 'DTB3', 'DTB6', 'DGS1', 'DGS2', 'DGS10', 'VIXCLS')
CORE = ('s3m', 's6m', 'd3m_30', 'd2y_30')
EXTRA = ('curve', 'vix')


def load_fred(cache_dir='data/fred'):
    """Use local series caches or FRED CSV; fallback to pandas_datareader on failure."""
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for series in SERIES:
        path = directory / f'{series}.csv'
        if not path.exists():
            url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}'
            try:
                with urlopen(url, timeout=30) as response:
                    payload = response.read()
                df = pd.read_csv(io.BytesIO(payload), encoding='utf-8')
                if len(df.columns) != 2 or df.columns[1] != series or df.empty:
                    raise ValueError(f'unexpected FRED CSV for {series}')
                path.write_bytes(payload)
            except Exception as direct_error:
                try:
                    from pandas_datareader import data as web
                    df = web.DataReader(series, 'fred', start='1900-01-01')
                    if df.empty:
                        raise ValueError('empty result')
                    df.to_csv(path, index_label='observation_date', encoding='utf-8')
                except Exception as fallback_error:
                    raise RuntimeError(
                        f'Cannot obtain FRED {series}: CSV: {direct_error}; fallback: {fallback_error}. '
                        f'Provide data/fred/{series}.csv or enable access to FRED.'
                    ) from fallback_error
        frame = pd.read_csv(path, na_values=['.', ''], encoding='utf-8')
        if series not in frame or len(frame.columns) < 2:
            raise ValueError(f'{path}: expected a date column and {series}')
        datecol = frame.columns[0]
        values = pd.to_numeric(frame[series], errors='coerce')
        dates = pd.to_datetime(frame[datecol], errors='raise')
        s = pd.Series(values.to_numpy(), index=dates, name=series).sort_index()
        if s.index.has_duplicates:
            raise ValueError(f'{path}: duplicate observation dates')
        frames.append(s)
    return pd.concat(frames, axis=1).sort_index()


def market_features(df, meeting_date):
    """Return features and actual observation timestamps. As-of values forward-fill gaps.

    The 30-day changes compare the as-of cutoff with the as-of cutoff minus 30
    calendar days. All observation timestamps are retained for the leakage audit.
    """
    cutoff = pd.Timestamp(meeting_date).normalize() - pd.Timedelta(days=2)
    df = df.sort_index()
    if df.index.has_duplicates:
        raise ValueError('duplicate market observation dates')
    times = {}
    def observation(name, date, key):
        eligible = df.loc[df.index <= date, name].dropna()
        if eligible.empty:
            raise ValueError(f'No {name} observation by {date.date()} for meeting {meeting_date}')
        times[key] = eligible.index[-1]
        assert times[key] <= cutoff, 'market observation after cutoff'
        assert times[key] <= date, 'market observation after requested as-of date'
        return float(eligible.iloc[-1])
    current = {name: observation(name, cutoff, name) for name in SERIES}
    previous = {name: observation(name, cutoff - pd.Timedelta(days=30), name + '_30d')
                for name in ('DTB3', 'DGS2')}
    features = {'dff': current['DFF'], 's3m': current['DTB3'] - current['DFF'],
                's6m': current['DTB6'] - current['DFF'],
                's1y': current['DGS1'] - current['DFF'],
                'd3m_30': current['DTB3'] - previous['DTB3'],
                'd2y_30': current['DGS2'] - previous['DGS2'],
                'curve': current['DGS10'] - current['DGS2'], 'vix': current['VIXCLS']}
    return features, times
