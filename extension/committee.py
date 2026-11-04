"""Load checked-in single-run committee outputs; reject ambiguous decisions."""
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
from .fusion import smooth_votes


def meeting_code(date):
    date = pd.Timestamp(date)
    return f'{date.year % 100:02d}_{date.month}'


def parse_vote(value):
    """Accept explicitly signed or unsigned rate changes, never a rate level."""
    match = re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*%\s*', str(value))
    if not match:
        raise ValueError(f'Unparseable vote {value!r}')
    number = float(match.group(1))
    if abs(number) > 2 or not np.isclose(number * 4, round(number * 4), atol=1e-7):
        raise ValueError(f'Vote is not a plausible quarter-point rate change: {value!r}')
    return 0 if number < 0 else 2 if number > 0 else 1


def majority(votes):
    counts = np.bincount(votes, minlength=3)
    if counts.max() <= len(votes) / 2:
        raise ValueError(f'No strict majority: {votes}')
    return int(np.argmax(counts))


def load_baseline(meetings, root='chain_of_draft/results'):
    """Use run1 only (fixed in advance); don't substitute a more favorable run."""
    rows, rejected = [], []
    for _, row in meetings.iterrows():
        code = meeting_code(row.Date)
        path = Path(root) / f'{code}_run1' / 'rate_summary.json'
        if not path.exists():
            continue
        if sum(meeting_code(d) == code for d in meetings.Date) != 1:
            rejected.append(f'{path}: ambiguous year/month meeting code; use an audited baseline CSV')
            continue
        try:
            data = json.loads(path.read_text())
            votes = data['rate_votes']
            if len(votes) != 3 or len({v['member'] for v in votes}) != 3:
                raise ValueError('expected three distinct members')
            decision = majority([parse_vote(v['vote']) for v in votes])
            p = smooth_votes([decision])
            rows.append({'Date': row.Date, 'meeting_id': code, 'comm_decision': decision,
                         'p_comm_cut': p[0], 'p_comm_hold': p[1], 'p_comm_hike': p[2]})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            rejected.append(f'{path}: {e}')
    return pd.DataFrame(rows, columns=['Date', 'meeting_id', 'comm_decision', 'p_comm_cut', 'p_comm_hold', 'p_comm_hike']), rejected
