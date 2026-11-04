"""Load committee decisions and conservatively recover corrupted summaries."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .fusion import smooth_votes
from .vote_audit import ROLES, audit_summary, named_votes, vote_number


def meeting_code(date):
    date = pd.Timestamp(date)
    return f'{date.year % 100:02d}_{date.month}'


def parse_vote(value):
    """Accept quarter-point rate changes, not rate levels."""
    number = float(vote_number(value))
    if abs(number) > 2 or not np.isclose(number * 4, round(number * 4), atol=1e-7):
        raise ValueError(f'Vote is not a plausible quarter-point rate change: {value!r}')
    return 0 if number < 0 else 2 if number > 0 else 1


def majority(votes):
    counts = np.bincount(votes, minlength=3)
    if counts.max() <= len(votes) / 2:
        raise ValueError(f'No strict majority: {votes}')
    return int(np.argmax(counts))


def load_baseline(meetings, root='chain_of_draft/results'):
    """Use run 1; recover invalid JSON only if task and intermediate votes agree."""
    rows, rejected, provenance = [], [], []
    for _, row in meetings.iterrows():
        code = meeting_code(row.Date)
        path = Path(root) / f'{code}_run1' / 'rate_summary.json'
        if not path.exists():
            continue
        if sum(meeting_code(d) == code for d in meetings.Date) != 1:
            rejected.append(f'{path}: ambiguous year/month meeting code; use an audited baseline CSV')
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            votes = named_votes(raw['rate_votes'])
            audit = audit_summary(path)
            try:
                labels = [parse_vote(votes[name]) for name in ROLES.values()]
                valid_summary = True
            except ValueError:
                valid_summary = False
            if not audit['intermediate_matches_tasks']:
                raise ValueError('task votes and intermediate vote_summary disagree; cannot safely resolve')
            if valid_summary and not audit['final_matches_tasks']:
                raise ValueError('valid summary conflicts with task votes; cannot safely resolve')
            if not valid_summary:
                votes = audit['task_votes']
                labels = [parse_vote(votes[name]) for name in ROLES.values()]
                source = 'recovered_from_completed_voting_tasks'
            else:
                source = 'final_json_verified_against_tasks'
            decision = majority(labels)
            p = smooth_votes([decision])
            rows.append({'Date': row.Date, 'meeting_id': code, 'comm_decision': decision,
                         'p_comm_cut': p[0], 'p_comm_hold': p[1], 'p_comm_hike': p[2]})
            provenance.append({'date': str(pd.Timestamp(row.Date).date()), 'meeting_id': code,
                               'source': source, 'used_votes': votes, 'decision': decision, 'audit': audit})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            rejected.append(f'{path}: {e}')
    result = pd.DataFrame(rows, columns=['Date', 'meeting_id', 'comm_decision', 'p_comm_cut', 'p_comm_hold', 'p_comm_hike'])
    result.attrs['provenance'] = provenance
    return result, rejected
