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


class EnsembleCacheError(ValueError):
    """Invalid pre-existing cache with a safe per-invocation audit."""
    def __init__(self, message, audit):
        super().__init__(message)
        self.audit = audit


class EnsembleRunError(RuntimeError):
    """Failed provider invocation with a safe per-invocation audit."""
    def __init__(self, message, audit):
        super().__init__(message)
        self.audit = audit


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
                 dry_run=False, limit=None, approve_full_batch=False, retries=3, provider='openai',
                 flag_votes=None):
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
    valid_vote_keys = {(code, sample, role) for _, code, sample, role, _, _ in jobs}
    flagged = set()
    for flag in flag_votes or []:
        try:
            code, sample, role = flag.split(':')
            key = (code, int(sample), role)
        except (ValueError, AttributeError):
            raise ValueError('Invalid vote flag; expected meeting_id:sample_index:role') from None
        if key not in valid_vote_keys:
            raise ValueError('Vote flag does not match a selected meeting, sample and role')
        flagged.add(key)
    missing = [job for job in jobs if not job[-1].exists()]
    cached_keys = {(code, sample, role) for _, code, sample, role, _, path in jobs if path.exists()}
    if not flagged.issubset(cached_keys):
        raise ValueError('A flagged vote has no matching cached response; inspect the cache; no new calls made')
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
            return parse_response(record['text']), record
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f'{path}: invalid cached vote: {e}; inspect the response before retrying') from e
    # Invalid cached responses halt before any provider calls.
    invalid_cached = 0
    for job in jobs:
        if job[-1].exists():
            try:
                read_record(job)
            except (OSError, ValueError, KeyError, TypeError):
                invalid_cached += 1
    if invalid_cached:
        raise EnsembleCacheError(
            f'{invalid_cached} invalid cached vote or provenance record(s); inspect and retain them; no new provider calls made',
            {'completed': False, 'planned_role_votes': len(jobs), 'reused_cache_records': len(cached_keys),
             'invalid_cached_records': invalid_cached, 'new_responses': 0,
             'invalid_new_responses': 0, 'provider_attempts_this_invocation': 0,
             'failed_provider_attempts_this_invocation': 0, 'prior_provider_attempts_and_failures': 'unknown'})
    budget = CallBudget(max_calls)
    response_count = 0
    response_lock = threading.Lock()
    if missing:
        try:
            client = make_provider(provider)
        except Exception:
            raise EnsembleRunError('Could not initialize provider; no requests made; details redacted',
                {'completed': False, 'planned_role_votes': len(jobs), 'reused_cache_records': len(cached_keys),
                 'invalid_cached_records': 0, 'new_responses': 0, 'invalid_new_responses': 0,
                 'provider_attempts_this_invocation': 0, 'failed_provider_attempts_this_invocation': 0,
                 'prior_provider_attempts_and_failures': 'unknown'}) from None
        def invoke(job):
            nonlocal response_count
            date, code, sample, role, prompt, path = job
            text, usage = call_with_retry(client, budget, model, temp, prompt, retries)
            with response_lock:
                response_count += 1
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
                    errors.append(e)
        print(f'Provider requests attempted: {budget.used}/{max_calls}; cached responses retained.')
        if errors:
            written = [job for job in missing if job[-1].exists()]
            invalid_new = 0
            for job in written:
                try:
                    read_record(job)
                except (OSError, ValueError, KeyError, TypeError):
                    invalid_new += 1
            from .providers import GeminiRequestError, OpenAIRequestError
            diagnostic = (str(errors[0]) if isinstance(errors[0], (GeminiRequestError, OpenAIRequestError))
                          else 'details redacted')
            raise EnsembleRunError(
                f'{len(errors)} vote calls failed; {invalid_new} invalid cached vote(s); {diagnostic}',
                {'completed': False, 'planned_role_votes': len(jobs), 'reused_cache_records': len(cached_keys),
                 'invalid_cached_records': 0, 'new_responses': len(written), 'invalid_new_responses': invalid_new,
                 'failed_jobs': len(errors), 'provider_attempts_this_invocation': budget.used,
                 'provider_responses_this_invocation': response_count,
                 'failed_provider_attempts_this_invocation': max(0, budget.used - response_count),
                 'prior_provider_attempts_and_failures': 'unknown'})
    rows, vote_audit, meeting_audit = [], [], []
    usage_fields = {
        'input_tokens': ('prompt_token_count', 'prompt_tokens'),
        'output_tokens': ('candidates_token_count', 'completion_tokens'),
        'thinking_tokens': ('thoughts_token_count',),
        'total_tokens': ('total_token_count', 'total_tokens'),
    }
    for date, code, context in available:
        decisions, unresolved, reported_flags = [], 0, 0
        for sample in range(k):
            votes, records = [], []
            for role in ROLES:
                prompt = vote_prompt(role, date, context)
                path = cache_path(cache, code, sample, model, temp, role, prompt, provider)
                vote, record = read_record((date, code, sample, role, prompt, path))
                votes.append(vote)
                records.append((role, vote, record))
            try:
                decision = majority(votes)
            except ValueError:
                decision = None
                unresolved += 1
            else:
                decisions.append(decision)
            print(f"{code} sample {sample}: member votes={votes}, committee="
                  f"{decision if decision is not None else 'unresolved (no strict majority)'}")
            for role, vote, record in records:
                key = (code, sample, role)
                usage = record.get('usage') or {}
                if not isinstance(usage, dict):
                    usage = {}
                tokens = {}
                for name, candidates in usage_fields.items():
                    tokens[name] = next((usage[field] for field in candidates
                                         if type(usage.get(field)) is int and usage[field] >= 0), None)
                is_flagged = key in flagged
                reported_flags += int(is_flagged)
                vote_audit.append({'Date': str(date.date()), 'meeting_id': code, 'sample_idx': sample,
                    'role': role, 'vote': vote, 'committee_decision': decision,
                    'sample_status': 'unresolved' if decision is None else 'resolved',
                    'source': 'existing_cache' if key in cached_keys else 'new_response',
                    'review_status': 'reported_inconsistency' if is_flagged else 'not_reviewed',
                    **tokens})
        p = smooth_votes(decisions) if decisions else None
        winner = None
        if p is not None:
            # An exact top-probability tie is not an excuse for an arbitrary argmax.
            best = p.max()
            choices = [i for i, value in enumerate(p) if abs(value - best) <= 1e-12]
            winner = choices[0] if len(choices) == 1 else None
            print(f'{code}: conditional p_ens={p.tolist()}; resolved={len(decisions)}/{k}')
            rows.append({'Date': date, 'p_ens_cut': p[0], 'p_ens_hold': p[1], 'p_ens_hike': p[2],
                         'resolved_samples': len(decisions), 'unresolved_samples': unresolved})
        else:
            print(f'{code}: all samples unresolved; no ensemble probability calculated')
        meeting_audit.append({'Date': str(date.date()), 'meeting_id': code, 'requested_samples': k,
            'resolved_samples': len(decisions), 'unresolved_samples': unresolved,
            'reported_inconsistencies': reported_flags,
            'ensemble_decision': winner,
            'decision_status': 'no_resolved_samples' if p is None else
                               ('tied_probability' if winner is None else 'resolved')})
    result = pd.DataFrame(rows, columns=['Date', 'p_ens_cut', 'p_ens_hold', 'p_ens_hike',
                                         'resolved_samples', 'unresolved_samples'])
    result.attrs['vote_audit'] = vote_audit
    result.attrs['meeting_audit'] = meeting_audit
    result.attrs['run_audit'] = {
        'completed': True, 'provider': provider, 'model': model, 'temperature': temp, 'k': k,
        'planned_role_votes': len(jobs), 'reused_cache_records': len(cached_keys),
        'invalid_cached_records': 0, 'new_responses': len(missing), 'invalid_new_responses': 0,
        'failed_jobs': 0, 'provider_attempts_this_invocation': budget.used,
        'provider_responses_this_invocation': response_count,
        'failed_provider_attempts_this_invocation': budget.used - response_count,
        'prior_provider_attempts_and_failures': 'unknown',
        'policy': 'strict majority per sample; exclude unresolved samples from conditional probabilities',
        'warning': 'Resolved-only probabilities are conditional and may have selection bias; '
                   'reasoning consistency requires human review.'}
    return result
