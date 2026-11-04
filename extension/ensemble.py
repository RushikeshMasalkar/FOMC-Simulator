"""Sample member votes from saved discussions, not a full CrewAI replay."""
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
from .providers import make_provider

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


def cache_path(directory, meeting, idx, model, temp, role, prompt, provider='openai'):
    key = json.dumps([provider, meeting, idx, model, temp, role, prompt], ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.sha256(key.encode()).hexdigest()
    return Path(directory) / provider / meeting / f'{idx:03d}-{role}-{digest}.json'


def call_with_retry(client, budget, model, temp, prompt, retries=3):
    for attempt in range(retries + 1):
        budget.acquire()
        try:
            return client.request(model, temp, prompt)
        except Exception as error:
            if not client.retryable(error) or attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 16))


def probe(provider, model, temp, max_calls):
    """One user-authorized request, not cached; costs money if the API charges."""
    if max_calls < 1:
        raise ValueError('probe needs --max_calls >= 1; no calls made')
    client = make_provider(provider)
    budget = CallBudget(max_calls)
    prompt = 'For a connectivity test, output exactly: INTEREST RATE VOTE: 0.00%'
    try:
        text, usage = call_with_retry(client, budget, model, temp, prompt, retries=0)
        vote = parse_response(text)
    except Exception as error:
        raise RuntimeError(f'Provider probe failed ({type(error).__name__}): {error}') from error
    print(f'Probe succeeded: provider={provider}, model={model}, requests={budget.used}.')
    print(f'Validated vote class: {vote} (0=cut, 1=hold, 2=hike).')
    # Only known numeric token counts are printed, never arbitrary provider data.
    allowed = ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count',
               'total_token_count', 'prompt_tokens', 'completion_tokens', 'total_tokens')
    safe_usage = {k: usage[k] for k in allowed if isinstance(usage, dict) and type(usage.get(k)) is int}
    print(f'Usage (numeric token counts): {safe_usage}')


def run_ensemble(meetings, root, cache, model, temp, k, max_calls, workers=3,
                 dry_run=False, limit=None, approve_full_batch=False, retries=3, provider='openai'):
    if provider not in ('openai', 'gemini'):
        raise ValueError('provider must be openai or gemini')
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
                path = cache_path(cache, code, sample, model, temp, role, prompt, provider)
                jobs.append((date, code, sample, role, prompt, path))
    missing = [job for job in jobs if not job[-1].exists()]
    print(f'Ensemble: provider={provider}, model={model}; {len(available)} meetings, K={k}, '
          f'{len(jobs)} total vote calls, {len(missing)} uncached; retries count against max_calls={max_calls}.')
    if dry_run:
        return None
    if len(available) > 5 and not approve_full_batch:
        raise ValueError('Run and inspect a 3–5 meeting smoke test first; then pass --approve_full_batch')
    if len(missing) > max_calls:
        raise RuntimeError(f'At least {len(missing)} calls needed, exceeding max_calls={max_calls}; no calls made')
    # Fail before spending if any prior cached response is malformed or has wrong provenance.
    def read_record(job):
        date, code, sample, role, prompt, path = job
        record = json.loads(path.read_text(encoding='utf-8'))
        expected = {'provider': provider, 'meeting_id': code, 'sample_idx': sample, 'model': model,
                    'temperature': temp, 'role': role, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        if any(record.get(k) != v for k, v in expected.items()):
            raise ValueError(f'{path}: cache metadata mismatch; inspect and retain the file')
        try:
            return parse_response(record['text'])
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f'{path}: invalid cached vote: {e}; inspect the response before retrying') from e
    cached_votes = {}
    for job in jobs:
        if job[-1].exists():
            _, code, sample, role, _, _ = job
            cached_votes[(code, sample, role)] = read_record(job)
    # A previously completed sample can already be unresolved under the
    # existing strict-majority rule. Detect it before scheduling any of the
    # remaining paid calls; never discard, reinterpret, or resample its votes.
    for _, code, _ in available:
        for sample in range(k):
            keys = [(code, sample, role) for role in ROLES]
            if all(key in cached_votes for key in keys):
                votes = [cached_votes[key] for key in keys]
                try:
                    majority(votes)
                except ValueError:
                    raise ValueError(
                        f'{code} sample {sample}: No strict majority: {votes}. '
                        'Cached votes retained; no new provider calls made. '
                        'Decide how to handle unresolved votes before resuming.'
                    ) from None
    budget = CallBudget(max_calls)
    if missing:
        client = make_provider(provider)
        def invoke(job):
            date, code, sample, role, prompt, path = job
            text, usage = call_with_retry(client, budget, model, temp, prompt, retries)
            record = {'provider': provider, 'meeting_id': code, 'date': str(date.date()), 'sample_idx': sample,
                      'model': model, 'temperature': temp, 'role': role,
                      'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'text': text, 'usage': usage}
            # Every response, including an invalid one, is saved for audit before parsing.
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
            tmp.replace(path)
            return read_record(job)
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
                prompt = vote_prompt(role, date, context)
                path = cache_path(cache, code, sample, model, temp, role, prompt, provider)
                votes.append(read_record((date, code, sample, role, prompt, path)))
            decisions.append(majority(votes))
            print(f'{code} sample {sample}: member votes={votes}, committee={decisions[-1]}')
        p = smooth_votes(decisions)
        print(f'{code}: p_comm={p.tolist()}')
        rows.append({'Date': date, 'p_ens_cut': p[0], 'p_ens_hold': p[1], 'p_ens_hike': p[2]})
    return pd.DataFrame(rows)
