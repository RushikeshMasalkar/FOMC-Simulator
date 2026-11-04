"""Audit saved CrewAI vote records without modifying the original artifacts."""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

HEADER = re.compile(r'(?m)^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d: task_name="')
VOTE = re.compile(r'(?im)^\s*(?:\*\*)?INTEREST RATE VOTE(?:\*\*)?\s*:(?:\*\*)?\s*(?:\*\*)?\s*([+-]?\d+(?:\.\d+)?\s*%)')
ROLES = {'regional': 'Regional Pragmatist', 'academic': 'Academic Balancer', 'central': 'Central Policymaker'}


def vote_number(value):
    if not re.fullmatch(r'\s*[+-]?\d+(?:\.\d+)?\s*%\s*', str(value)):
        raise ValueError(f'Invalid percent: {value!r}')
    try:
        return Decimal(str(value).strip().removesuffix('%').strip())
    except InvalidOperation as e:
        raise ValueError(f'Invalid percent: {value!r}') from e


def named_votes(items):
    if not isinstance(items, list) or len(items) != 3:
        raise ValueError('Expected exactly three vote records')
    votes = {}
    for item in items:
        name = item['member'].strip()
        if name not in ROLES.values() or name in votes:
            raise ValueError(f'Unexpected or duplicate member: {name!r}')
        votes[name] = str(item['vote']).strip()
    if set(votes) != set(ROLES.values()):
        raise ValueError('Member set does not match voting roles')
    return votes


def equal_votes(a, b):
    return set(a) == set(b) and all(vote_number(a[n]) == vote_number(b[n]) for n in a)


def audit_summary(summary_path):
    """Compare the final JSON, three task outputs, and intermediate vote_summary.

    Reject duplicated task completions or ambiguous vote lines. The task output
    source includes line numbers and file hashes so recovery is independently auditable.
    """
    summary_path = Path(summary_path)
    log_path = summary_path.with_name('fomc_simulation.md')
    summary_bytes = summary_path.read_bytes()
    log_bytes = log_path.read_bytes()
    final = named_votes(json.loads(summary_bytes)['rate_votes'])
    text = log_bytes.decode('utf-8')
    starts = list(HEADER.finditer(text))
    # Log files may append interrupted or repeated runs. Select only the last run,
    # then verify its final task output is exactly the saved JSON.
    run_start = None
    for i, start in enumerate(starts):
        segment = text[start.start():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        if 'task_name="probabilities_comment"' in segment and 'status="started"' in segment:
            run_start = i
    if run_start is None:
        raise ValueError('missing run start in transcript')
    found = {}
    for i, start in enumerate(starts):
        if i < run_start:
            continue
        segment = text[start.start():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        if 'status="completed", output="' not in segment:
            continue
        task_match = re.search(r'task_name="([^"]+)"', segment)
        if not task_match:
            continue
        task = task_match.group(1)
        if task not in [f'{r}_vote' for r in ROLES] + ['vote_summary', 'summary_final']:
            continue
        if task in found:
            raise ValueError(f'duplicate completed task: {task}')
        output = segment.split('status="completed", output="', 1)[1].strip()
        if output.endswith('"'):
            output = output[:-1]
        line = text.count('\n', 0, start.start()) + 1
        if task == 'summary_final':
            if json.loads(output) != json.loads(summary_bytes):
                raise ValueError('saved summary does not match final output of last logged run')
            found[task] = {'line': line}
        elif task == 'vote_summary':
            found[task] = {'votes': named_votes(json.loads(output)['rate_votes']), 'line': line}
        else:
            matches = VOTE.findall(output)
            if len(matches) != 1:
                raise ValueError(f'{task}: expected exactly one vote line, found {len(matches)}')
            found[task] = {'vote': matches[0].replace(' ', ''), 'line': line}
    expected = [f'{r}_vote' for r in ROLES] + ['vote_summary', 'summary_final']
    missing = [task for task in expected if task not in found]
    if missing:
        raise ValueError(f'missing completed task outputs: {missing}')
    task_votes = {member: found[role + '_vote']['vote'] for role, member in ROLES.items()}
    intermediate = found['vote_summary']['votes']
    return {'summary_path': str(summary_path), 'summary_sha256': hashlib.sha256(summary_bytes).hexdigest(),
            'transcript_path': str(log_path), 'transcript_sha256': hashlib.sha256(log_bytes).hexdigest(),
            'final_votes': final, 'task_votes': task_votes, 'intermediate_votes': intermediate,
            'task_lines': {task: found[task]['line'] for task in expected},
            'intermediate_matches_tasks': equal_votes(intermediate, task_votes),
            'final_matches_tasks': equal_votes(final, task_votes)}
