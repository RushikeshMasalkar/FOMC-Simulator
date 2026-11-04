"""Resample three voting roles from saved discussions, not the summary task.

This deliberately does not claim byte-for-byte CrewAI equivalence: CrewAI memory,
retrieval and runtime prompts are not preserved by the logged discussions.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import re
import threading
import time
import pandas as pd
from .committee import majority, meeting_code, parse_vote
from .fusion import smooth_votes

ROLES = {'regional': 'Regional Pragmatists', 'academic': 'Academic Balancers', 'central': 'Central Policymakers'}
HEADER = re.compile(r'(?m)^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d: task_name="')


def discussions(path):
    text = Path(path).read_text(encoding='utf-8')
    starts = list(HEADER.finditer(text))
    found = {}
    for idx, start in enumerate(starts):
        segment = text[start.start():starts[idx + 1].start() if idx + 1 < len(starts) else len(text)]
        for role in ROLES:
            if f'task_name="{role}_discussion"' in segment and 'status="completed", output="' in segment:
                output = segment.split('status="completed", output="', 1)[1].strip()
                if output.endswith('"'):
                    output = output[:-1]
                found[role] = output
    if set(found) != set(ROLES):
        raise ValueError(f'{path}: missing completed discussions: {set(ROLES) - set(found)}')
    return found


def vote_prompt(role, date, context):
    transcript = '\n\n'.join(f'{ROLES[r]} discussion:\n{context[r]}' for r in ROLES)
    return (f'As {ROLES[role]}, cast your final vote for the {pd.Timestamp(date).date()} FOMC meeting. '
            'Base your vote on these three discussions. State the interest rate change, explain your reasoning, '
            'include relevant historical comparisons and economic indicators, and note any reservations. '
            'Use Chain of Draft: minimal steps, 30 words maximum per step. '
            'Finish with a separate line exactly in the form INTEREST RATE VOTE: +0.25% '
            '(or 0.00%, -0.25%, -0.50%, as appropriate).\n\n' + transcript)


def parse_response(text):
    matches = re.findall(r'(?im)^\s*(?:\*\*)?INTEREST RATE VOTE(?:\*\*)?\s*:(?:\*\*)?\s*(?:\*\*)?\s*([+-]?\d+(?:\.\d+)?\s*%)', text)
    if len(matches) != 1:
        raise ValueError('response needs exactly one INTEREST RATE VOTE line')
    return parse_vote(matches[0])


class CallBudget:
    def __init__(self, maximum):
        if maximum < 0:
            raise ValueError('max_calls must be non-negative')
        self.maximum, self.used, self.lock = maximum, 0, threading.Lock()
    def acquire(self):
        with self.lock:
            if self.used >= self.maximum:
                raise RuntimeError(f'LLM call budget exhausted ({self.maximum})')
            self.used += 1


def cache_path(directory, meeting, idx, model, temp, role, prompt):
    key = json.dumps([meeting, idx, model, temp, role, prompt], ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.sha256(key.encode()).hexdigest()
    return Path(directory) / meeting / f'{idx:03d}-{role}-{digest}.json'


def run_ensemble(meetings, root, cache, model, temp, k, max_calls, workers=3,
                 dry_run=False, limit=None, approve_full_batch=False, retries=3):
    if k < 1 or not 0 < temp <= 2 or workers < 1:
        raise ValueError('K >= 1, workers >= 1 and 0 < temperature <= 2 are required')
    if limit is not None and limit < 1:
        raise ValueError('limit_meetings must be positive')
    available = []
    for _, row in meetings.sort_values('Date').iterrows():
        code = meeting_code(row.Date)
        path = Path(root) / f'{code}_run1' / 'fomc_simulation.md'
        if path.exists():
            if sum(meeting_code(d) == code for d in meetings.Date) != 1:
                raise ValueError(f'{code}: ambiguous year/month meeting code; cannot associate transcript safely')
            available.append((row.Date, code, discussions(path)))
    if limit is not None:
        available = available[:limit]
    if not available:
        raise ValueError('No matching cached deliberation logs found')
    jobs = []
    for date, code, context in available:
        for sample in range(k):
            for role in ROLES:
                prompt = vote_prompt(role, date, context)
                path = cache_path(cache, code, sample, model, temp, role, prompt)
                jobs.append((date, code, sample, role, prompt, path))
    missing = [job for job in jobs if not job[-1].exists()]
    print(f'Ensemble: {len(available)} meetings, K={k}, {len(jobs)} total vote calls, '
          f'{len(missing)} uncached; retries count against max_calls={max_calls}.')
    if dry_run:
        return None
    if len(available) > 5 and not approve_full_batch:
        raise ValueError('Run and inspect a 3–5 meeting smoke test first; then pass --approve_full_batch')
    if len(missing) > max_calls:
        raise RuntimeError(f'At least {len(missing)} calls needed, exceeding max_calls={max_calls}; no calls made')
    budget = CallBudget(max_calls)
    if missing:
        from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError, InternalServerError
        client = OpenAI(max_retries=0)  # retries must be counted by our budget
        def invoke(job):
            date, code, sample, role, prompt, path = job
            for attempt in range(retries + 1):
                budget.acquire()
                try:
                    response = client.chat.completions.create(model=model, temperature=temp,
                        messages=[{'role': 'user', 'content': prompt}])
                    text = response.choices[0].message.content or ''
                    record = {'meeting_id': code, 'date': str(date.date()), 'sample_idx': sample,
                              'model': model, 'temperature': temp, 'role': role, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                              'text': text, 'usage': response.usage.model_dump() if response.usage else None}
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
                    tmp.replace(path)
                    return
                except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError):
                    if attempt == retries:
                        raise
                    time.sleep(min(2 ** attempt, 16))
        errors = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(invoke, job) for job in missing]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    errors.append(str(e))
        print(f'Provider requests attempted: {budget.used}/{max_calls}; cached responses retained.')
        if errors:
            raise RuntimeError(f'{len(errors)} vote calls failed; resume after fixing: {errors[0]}')
    rows = []
    for date, code, context in available:
        decisions = []
        for sample in range(k):
            votes = []
            for role in ROLES:
                path = cache_path(cache, code, sample, model, temp, role, vote_prompt(role, date, context))
                record = json.loads(path.read_text(encoding='utf-8'))
                try:
                    votes.append(parse_response(record['text']))
                except ValueError as e:
                    raise ValueError(f'{path}: invalid cached vote: {e}') from e
            decisions.append(majority(votes))
            print(f'{code} sample {sample}: member votes={votes}, committee={decisions[-1]}')
        p = smooth_votes(decisions)
        print(f'{code}: p_comm={p.tolist()}')
        rows.append({'Date': date, 'p_ens_cut': p[0], 'p_ens_hold': p[1], 'p_ens_hike': p[2]})
    return pd.DataFrame(rows)
