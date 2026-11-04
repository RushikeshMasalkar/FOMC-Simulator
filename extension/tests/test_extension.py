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
from extension.ensemble import discussions, run_ensemble, CallBudget, cache_path, call_with_retry, probe
from extension.vote_audit import audit_summary
from extension.evaluate import evaluate
from extension.providers import GeminiProvider
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
    assert len(data) == 2 and not rejected
    recovered = data.attrs['provenance'][1]
    assert recovered['source'] == 'recovered_from_completed_voting_tasks'
    assert set(recovered['used_votes'].values()) == {'-0.25%'}
    assert data.iloc[1].comm_decision == 0
    assert not recovered['audit']['final_matches_tasks']
    assert recovered['audit']['intermediate_matches_tasks']


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


def test_all_eight_saved_summaries_audited():
    paths = sorted(Path('chain_of_draft/results').glob('*_run*/rate_summary.json'))
    assert len(paths) == 8
    records = [audit_summary(p) for p in paths]
    assert sum(not r['final_matches_tasks'] for r in records) == 1
    assert all(r['intermediate_matches_tasks'] for r in records)
    anomalous = next(r for r in records if not r['final_matches_tasks'])
    assert '24_9_run1' in anomalous['summary_path']
    assert set(anomalous['final_votes'].values()) == {'4.62%'}
    assert set(anomalous['task_votes'].values()) == {'-0.25%'}
    assert all(anomalous['task_lines'].values())
    # This file is an append-only log with a previous interrupted run.
    assert audit_summary('chain_of_draft/results/23_5_run2/rate_summary.json')['final_matches_tasks']


def test_recovery_requires_consistent_task_and_intermediate_sources(tmp_path):
    import shutil
    source = Path('chain_of_draft/results/24_9_run1')
    dest = tmp_path / '24_9_run1'
    shutil.copytree(source, dest)
    log = dest / 'fomc_simulation.md'
    text = log.read_text(encoding='utf-8')
    needle = 'status="completed", output="Regional Pragmatist:\nPOLICY VOTE: Decrease by 25 basis points\nINTEREST RATE VOTE: -0.25%'
    assert needle in text
    log.write_text(text.replace(needle, needle.replace('INTEREST RATE VOTE: -0.25%', 'INTEREST RATE VOTE: +0.25%'), 1), encoding='utf-8')
    frame = pd.DataFrame({'Date': pd.to_datetime(['2024-09-18'])})
    result, rejected = load_baseline(frame, tmp_path)
    assert result.empty and len(rejected) == 1 and 'disagree' in rejected[0]
    assert json.loads((dest / 'rate_summary.json').read_text(encoding='utf-8'))['rate_votes'][0]['vote'] == '4.62%'


def test_no_nonhold_evaluation_reports_na(tmp_path):
    df = pd.DataFrame({'Date': pd.to_datetime(['2024-03-20']), 'y': [1],
        'p_mkt_cut': [.2], 'p_mkt_hold': [.6], 'p_mkt_hike': [.2]})
    table = evaluate(df, {'Always-hold': 'always', 'Market-only logit': 'p_mkt_'}, tmp_path)
    assert table.loc['Always-hold', 'accuracy'] == 1
    assert np.isnan(table.loc['Always-hold', 'mcc'])
    assert 'Non-hold: N/A (no non-hold meetings)' in (tmp_path / 'ablation.md').read_text(encoding='utf-8')
    assert 'N/A' in (tmp_path / 'ablation.md').read_text(encoding='utf-8')
    report = json.loads((tmp_path / 'confusion_and_classes.json').read_text(encoding='utf-8'))
    assert report['Always-hold']['non_hold'] is None
    assert report['Always-hold']['recall'] == [None, 1.0, None]


def test_google_sdk_provider_and_no_automatic_retries(monkeypatch):
    from google import genai
    from google.genai import errors
    seen = {}
    class FakeClient:
        def __init__(self, **kwargs):
            seen['kwargs'] = kwargs
            self.models = SimpleNamespace(generate_content=self.generate_content)
        def generate_content(self, **kwargs):
            seen['request'] = kwargs
            return SimpleNamespace(text='INTEREST RATE VOTE: -0.25%', usage_metadata=None)
    monkeypatch.setattr(genai, 'Client', FakeClient)
    monkeypatch.setenv('GEMINI_API_KEY', 'test-not-a-real-key')
    client = GeminiProvider()
    assert seen['kwargs']['http_options'].retry_options.attempts == 1
    text, usage = client.request('gemini-3.1-flash-lite', .7, 'prompt')
    assert text == 'INTEREST RATE VOTE: -0.25%' and usage is None
    assert seen['request']['model'] == 'gemini-3.1-flash-lite'
    assert seen['request']['config'].temperature == .7
    assert client.retryable(errors.APIError(429, {}))
    assert client.retryable(errors.APIError(503, {}))
    assert not client.retryable(errors.APIError(400, {}))


def test_provider_cache_isolated_gemini_resume_and_retry(tmp_path, monkeypatch):
    from extension import ensemble
    calls = []
    class Retryable(Exception): pass
    class Fake:
        def request(self, model, temp, prompt):
            calls.append((model, temp, prompt))
            if len(calls) == 1: raise Retryable('rate limited')
            return 'INTEREST RATE VOTE: -0.25%', {'test': True}
        def retryable(self, error): return isinstance(error, Retryable)
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    monkeypatch.setattr(ensemble.time, 'sleep', lambda seconds: None)
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    args = (meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7, 1)
    result = run_ensemble(*args, max_calls=4, provider='gemini', workers=1)
    assert len(calls) == 4 and result.p_ens_cut.iloc[0] == pytest.approx(.6)
    assert len(list((tmp_path / 'gemini').rglob('*.json'))) == 3
    run_ensemble(*args, max_calls=0, provider='gemini')
    assert len(calls) == 4
    a = cache_path(tmp_path, '23_2', 0, 'same-model', .7, 'regional', 'prompt', 'openai')
    b = cache_path(tmp_path, '23_2', 0, 'same-model', .7, 'regional', 'prompt', 'gemini')
    assert a != b


def test_invalid_gemini_response_is_preserved_not_silently_resampled(tmp_path, monkeypatch):
    from extension import ensemble
    calls = []
    class Fake:
        def request(self, *args):
            calls.append(args)
            return 'missing explicit vote', None
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    args = (meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7, 1)
    with pytest.raises(RuntimeError, match='invalid cached vote'):
        run_ensemble(*args, max_calls=3, provider='gemini', workers=1)
    assert len(calls) == 3 and len(list(tmp_path.rglob('*.json'))) == 3
    with pytest.raises(ValueError, match='invalid cached vote'):
        run_ensemble(*args, max_calls=3, provider='gemini', workers=1)
    assert len(calls) == 3


def test_probe_is_budget_guarded(monkeypatch):
    from extension import ensemble
    made = []
    class Fake:
        def request(self, *args): made.append(args); return 'INTEREST RATE VOTE: 0.00%', None
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    with pytest.raises(ValueError): probe('gemini', 'gemini-3.1-flash-lite', .7, 0)
    assert not made
    probe('gemini', 'gemini-3.1-flash-lite', .7, 1)
    assert len(made) == 1


def test_fusion_cli_synthetic_integration_and_backup(tmp_path):
    """Synthetic-only plumbing test: no data or LLM service used."""
    from extension.run_all import main, parser, market_settings
    out = tmp_path / 'results'; out.mkdir()
    args = parser().parse_args(['--stage', 'fusion', '--outdir', str(out)])
    (out / 'market_metadata.json').write_text(json.dumps({'settings': market_settings(args)}), encoding='utf-8')
    dates = pd.to_datetime(['2023-02-01','2023-05-03','2024-03-20','2024-09-18'])
    pred = pd.DataFrame({'Date': dates, 'y': [2,2,1,0], 'p_mkt_cut': [.2]*4,
                         'p_mkt_hold': [.6]*4, 'p_mkt_hike': [.2]*4,
                         'train_max_date': pd.to_datetime(['2022-12-14']*2 + ['2023-12-13']*2),
                         'test_year': [2023,2023,2024,2024]})
    pred.to_csv(out / 'market_oos.csv', index=False)
    (out / 'old_result.txt').write_text('do not overwrite', encoding='utf-8')
    assert main(['--stage', 'fusion', '--outdir', str(out)]) == 0
    assert len(pd.read_csv(out / 'phase2/predictions.csv')) == 2
    assert (out / 'phase2/ablation.md').exists()
    prov = json.loads((out / 'baseline_provenance.json').read_text(encoding='utf-8'))
    assert any(x['source'] == 'recovered_from_completed_voting_tasks' for x in prov)
    backups = list((tmp_path / 'results_backups').glob('*/old_result.txt'))
    assert len(backups) == 1 and backups[0].read_text(encoding='utf-8') == 'do not overwrite'


def test_gemini_html_400_sdk_wire_request_and_redaction(tmp_path, monkeypatch, capsys):
    """Use the real SDK with a mock HTTP transport: no live request is sent."""
    import httpx
    from google import genai
    from extension.run_all import main
    secret = 'test-key-never-log-this-value'
    seen = []
    real_client = genai.Client
    def handler(request):
        seen.append(request)
        assert request.method == 'POST'
        assert str(request.url) == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent'
        assert request.headers['x-goog-api-key'] == secret
        body = json.loads(request.content)
        assert body['generationConfig']['temperature'] == .7
        assert body['contents'][0]['parts'][0]['text'].startswith('For a connectivity test')
        return httpx.Response(400, request=request, headers={'content-type':'text/html', 'via':'secret-proxy-name'},
                              text='<html><title>Error 400 (Bad Request)!!1</title>' + secret + '</html>')
    def client(**kwargs):
        assert kwargs['vertexai'] is False
        assert kwargs['http_options'].retry_options.attempts == 1
        kwargs['http_options'].httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
        return real_client(**kwargs)
    monkeypatch.setattr(genai, 'Client', client)
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    monkeypatch.setenv('HTTPS_PROXY', 'http://private-user:private-password@private-proxy:8080')
    outdir = tmp_path / 'existing-results'; outdir.mkdir()
    (outdir / 'result.txt').write_text('unchanged', encoding='utf-8')
    before = set(outdir.iterdir())
    assert main(['--stage','probe','--provider','gemini','--model','gemini-3.1-flash-lite',
                 '--max_calls','1','--outdir',str(outdir)]) == 1
    output = capsys.readouterr().err
    assert len(seen) == 1
    assert 'HTTP status: 400' in output
    assert 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent' in output
    assert 'HTML error page' in output and 'forwarding headers: present' in output
    assert 'origin cannot be established' in output
    for value in (secret, 'private-user', 'private-password', 'private-proxy', 'secret-proxy-name'):
        assert value not in output
    assert set(outdir.iterdir()) == before
    assert (outdir / 'result.txt').read_text(encoding='utf-8') == 'unchanged'


def test_gemini_sanitizes_json_error_and_request_query(monkeypatch):
    import httpx
    from google.genai import errors
    from extension.providers import gemini_error_diagnostic
    secret = 'test-secret-no-print'
    req = httpx.Request('POST', 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key=' + secret)
    response = httpx.Response(400, request=req, json={'error': {'status': 'INVALID_ARGUMENT',
                    'message': 'API key not valid. ' + secret}})
    error = errors.ClientError(400, response.json(), response)
    message = gemini_error_diagnostic(error)
    assert 'HTTP status: 400' in message and 'status=INVALID_ARGUMENT' in message
    assert 'message indicates invalid API key' in message
    assert secret not in message and '?key=' not in message
    assert 'x-goog-api-key' not in message


def test_gemini_sdk_rate_limit_counts_requests(monkeypatch):
    import httpx
    from google import genai
    from extension import ensemble
    real_client = genai.Client
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, request=request, json={'error': {'status':'RESOURCE_EXHAUSTED'}})
        return httpx.Response(200, request=request, json={'candidates': [{'content': {'role':'model',
            'parts':[{'text':'INTEREST RATE VOTE: 0.00%'}]}, 'finishReason':'STOP'}]})
    def client(**kwargs):
        kwargs['http_options'].httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
        return real_client(**kwargs)
    monkeypatch.setattr(genai, 'Client', client)
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    monkeypatch.setattr(ensemble.time, 'sleep', lambda _: None)
    provider = GeminiProvider()
    budget = CallBudget(2)
    text, _ = call_with_retry(provider, budget, 'gemini-3.1-flash-lite', .7, 'test', retries=1)
    assert text == 'INTEREST RATE VOTE: 0.00%' and len(calls) == 2 and budget.used == 2


def test_diagnose_is_offline_and_never_prints_secrets(tmp_path, monkeypatch, capsys):
    from extension.run_all import main
    secret = 'local-test-secret-with spaces'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy-user:proxy-password@proxy-host:8000')
    out = tmp_path / 'results'
    assert main(['--stage', 'diagnose', '--provider', 'gemini', '--outdir', str(out)]) == 0
    result = capsys.readouterr().out
    assert 'no network request was made' in result and 'GEMINI_API_KEY: present' in result
    assert 'contains whitespace: yes' in result
    assert 'HTTPS_PROXY' in result
    assert all(x not in result for x in (secret, 'proxy-user', 'proxy-password', 'proxy-host'))
    assert not out.exists()


def test_gemini_diagnostic_never_prints_unknown_host_or_url_secrets():
    import httpx
    from google.genai import errors
    from extension.providers import gemini_error_diagnostic
    secret = 'secret-token-123'
    req = httpx.Request('POST', f'https://{secret}.example.invalid/path/{secret}?key={secret}')
    res = httpx.Response(400, text=f'<html>{secret}</html>', request=req)
    output = gemini_error_diagnostic(errors.ClientError(400, {'message': res.text}, res))
    assert 'nonstandard endpoint' in output and 'HTTP status: 400' in output
    assert secret not in output and 'example.invalid' not in output


def test_probe_does_not_print_raw_model_text_or_non_numeric_usage(monkeypatch, capsys):
    from extension import ensemble
    class Fake:
        def request(self, *args):
            return 'INTEREST RATE VOTE: 0.00%\nsecret-token-should-not-appear', {
                'prompt_token_count': 5, 'other': 'sensitive', 'total_token_count': 'sensitive'}
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    probe('gemini', 'gemini-3.1-flash-lite', .7, 1)
    output = capsys.readouterr().out
    assert 'Validated vote class: 1' in output and "'prompt_token_count': 5" in output
    assert 'secret-token-should-not-appear' not in output and 'sensitive' not in output


@pytest.mark.parametrize('proxy_map, expected', [
    ({}, 'no'),
    ({'http': 'http://private:credentials@http-proxy:8000'}, 'no'),
    ({'https': 'http://private:credentials@https-proxy:8000'}, 'yes'),
    ({'all': 'socks5://private:credentials@all-proxy:8000'}, 'yes'),
])
def test_detected_system_proxy_is_boolean_only(monkeypatch, proxy_map, expected):
    import urllib.request
    from extension.providers import detected_https_proxy
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: proxy_map)
    assert detected_https_proxy() == expected


def test_system_proxy_discovery_failure_redacts_exception(monkeypatch):
    import urllib.request
    from extension.providers import detected_https_proxy
    def fail():
        raise OSError('proxy-password-in-registry')
    monkeypatch.setattr(urllib.request, 'getproxies', fail)
    assert detected_https_proxy() == 'unavailable'


def test_diagnose_mock_isolated_fake_key_and_no_result_writes(tmp_path, monkeypatch, capsys):
    import os
    import subprocess
    import urllib.request
    from extension.run_all import main
    from extension.providers import _MOCK_KEY
    actual_key = 'sensitive-real-key-never-send-to-child'
    other_key = 'sensitive-google-key-never-send-to-child'
    proxy = 'http://private-user:private-password@private-proxy:8000'
    monkeypatch.setenv('GEMINI_API_KEY', actual_key)
    monkeypatch.setenv('GOOGLE_API_KEY', other_key)
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    monkeypatch.setenv('SERVICE_TOKEN', 'sensitive-token')
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'https': proxy})
    real_run = subprocess.run
    observed = []
    def run(*args, **kwargs):
        env = kwargs['env']
        observed.append(env)
        assert env['GEMINI_API_KEY'] == _MOCK_KEY
        assert 'GOOGLE_API_KEY' not in env and 'HTTPS_PROXY' not in env and 'SERVICE_TOKEN' not in env
        assert kwargs['capture_output'] is True
        return real_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    out = tmp_path / 'results'; out.mkdir()
    backups = tmp_path / 'results_backups'; backups.mkdir()
    (out / 'fusion.txt').write_text('keep fusion results', encoding='utf-8')
    (backups / 'backup.txt').write_text('keep backup', encoding='utf-8')
    assert main(['--stage', 'diagnose', '--provider', 'gemini', '--outdir', str(out)]) == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert len(observed) == 1
    assert 'Python-detected HTTPS or ALL system/environment proxy: yes' in output
    assert 'POST: yes; expected endpoint: yes; JSON content type: yes; expected payload: yes' in output
    assert 'API-key header present: yes; fake key used: yes; Authorization header present: no' in output
    for secret in (actual_key, other_key, 'private-user', 'private-password', 'private-proxy', 'sensitive-token', _MOCK_KEY):
        assert secret not in output
    assert os.environ['GEMINI_API_KEY'] == actual_key and os.environ['GOOGLE_API_KEY'] == other_key
    assert os.environ['HTTPS_PROXY'] == proxy
    assert sorted(p.name for p in out.iterdir()) == ['fusion.txt']
    assert (out / 'fusion.txt').read_text(encoding='utf-8') == 'keep fusion results'
    assert sorted(p.name for p in backups.iterdir()) == ['backup.txt']
    assert (backups / 'backup.txt').read_text(encoding='utf-8') == 'keep backup'


def test_mock_worker_uses_fake_key_blocks_socket_and_does_not_print_headers(monkeypatch, capsys):
    import socket
    import httpx
    from google import genai
    from extension.providers import _gemini_mock_worker, _MOCK_KEY
    real_client = genai.Client
    secret = 'authorization-value-never-print'
    # Even when the suite runs on a user's machine, this in-process worker test
    # must not let the SDK read their actual key from the environment.
    monkeypatch.setenv('GEMINI_API_KEY', _MOCK_KEY)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    def never_connect(*args, **kwargs):
        raise AssertionError('real socket forbidden in tests')
    monkeypatch.setattr(socket.socket, 'connect', never_connect)
    def client(**kwargs):
        assert kwargs['api_key'] == _MOCK_KEY
        assert isinstance(kwargs['http_options'].httpx_client._transport, httpx.MockTransport)
        with socket.socket() as sock:
            with pytest.raises(RuntimeError, match='network disabled'):
                sock.connect(('203.0.113.1', 443))
        kwargs['http_options'].headers = {'Authorization': secret}
        return real_client(**kwargs)
    monkeypatch.setattr(genai, 'Client', client)
    _gemini_mock_worker()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    result = json.loads(captured.out)
    assert result['fake_key_used'] and result['authorization_header_present']
    assert result['expected_endpoint'] and result['payload_shape']
    assert secret not in output and _MOCK_KEY not in output


def test_diagnostic_never_relays_child_output_or_exceptions(monkeypatch, capsys):
    import subprocess
    from extension.providers import local_gemini_diagnostics
    secret = 'child-secret-never-display'
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=secret, stderr=secret)
    monkeypatch.setattr(subprocess, 'run', fail)
    local_gemini_diagnostics('gemini-3.1-flash-lite')
    captured = capsys.readouterr()
    assert 'Offline SDK mock check: unavailable (details redacted)' in captured.out
    assert secret not in captured.out + captured.err
    def bad_json(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=secret, stderr=secret)
    monkeypatch.setattr(subprocess, 'run', bad_json)
    local_gemini_diagnostics('gemini-3.1-flash-lite')
    captured = capsys.readouterr()
    assert 'Offline SDK mock check: unavailable (details redacted)' in captured.out
    assert secret not in captured.out + captured.err


def test_diagnostic_rejects_untrusted_child_fields(monkeypatch, capsys):
    import subprocess
    from extension.providers import _MOCK_CHECKS, local_gemini_diagnostics
    secret = 'child-field-secret-never-display'
    checks = {name: True for name in _MOCK_CHECKS}
    checks['expected_endpoint'] = secret
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(checks), stderr=secret))
    local_gemini_diagnostics('gemini-3.1-flash-lite')
    captured = capsys.readouterr()
    assert 'Offline SDK mock check: unavailable (details redacted)' in captured.out
    assert secret not in captured.out + captured.err


def test_unresolved_cached_tie_is_preserved_and_excluded_from_probabilities(tmp_path, monkeypatch):
    from extension import ensemble
    calls, per_role = [], {}
    class Fake:
        def request(self, model, temp, prompt):
            calls.append(1)
            role = next(role for role, label in ensemble.ROLES.items() if prompt.startswith(f'As {label},'))
            sample = per_role.get(role, 0)
            per_role[role] = sample + 1
            if sample:
                return 'INTEREST RATE VOTE: 0.00%', {'prompt_token_count': 10}
            return {'regional': 'Reasoning mentions a cut.\nINTEREST RATE VOTE: +0.25%',
                    'academic': 'INTEREST RATE VOTE: -0.25%',
                    'central': 'INTEREST RATE VOTE: 0.00%'}[role], None
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    args = (meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7)
    first = run_ensemble(*args, k=1, max_calls=3, workers=1, provider='gemini')
    assert first.empty and first.attrs['meeting_audit'][0]['decision_status'] == 'no_resolved_samples'
    paths = sorted(tmp_path.rglob('*.json'))
    before = {path: path.read_bytes() for path in paths}
    result = run_ensemble(*args, k=2, max_calls=3, workers=1, provider='gemini',
                          flag_votes=['23_2:0:regional'])
    assert len(calls) == 6 and len(list(tmp_path.rglob('*.json'))) == 6
    assert {path: path.read_bytes() for path in paths} == before
    assert result.p_ens_hold.iloc[0] == pytest.approx(.6)
    assert result.resolved_samples.iloc[0] == 1 and result.unresolved_samples.iloc[0] == 1
    assert result.attrs['run_audit']['reused_cache_records'] == 3
    assert result.attrs['run_audit']['provider_attempts_this_invocation'] == 3
    regional = next(v for v in result.attrs['vote_audit'] if v['sample_idx'] == 0 and v['role'] == 'regional')
    assert regional['vote'] == 2 and regional['review_status'] == 'reported_inconsistency'
    assert regional['sample_status'] == 'unresolved' and regional['source'] == 'existing_cache'
    assert ensemble.parse_response('Reasoning mentions a cut.\nINTEREST RATE VOTE: +0.25%') == 2


def test_partial_cache_with_majority_resumes_without_resampling(tmp_path, monkeypatch):
    from extension import ensemble
    calls = []
    class Fake:
        def request(self, *args):
            calls.append(1)
            return 'INTEREST RATE VOTE: 0.00%', None
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    args = (meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7)
    run_ensemble(*args, k=1, max_calls=3, workers=1, provider='gemini')
    before = {path: path.read_bytes() for path in tmp_path.rglob('*.json')}
    result = run_ensemble(*args, k=2, max_calls=3, workers=1, provider='gemini')
    assert len(calls) == 6 and len(list(tmp_path.rglob('*.json'))) == 6
    assert all(path.read_bytes() == content for path, content in before.items())
    assert result.p_ens_hold.iloc[0] == pytest.approx(2.5 / 3.5)


def test_metrics_rejects_out_of_range_and_fractional_labels():
    probs = np.tile([.2, .6, .2], (2, 1))
    for labels in ([-1, 1], [1, 3], [1, 1.5], [[0, 1]], [0, np.nan]):
        with pytest.raises(ValueError, match='invalid labels/probabilities'):
            metrics(labels, probs)


def test_gemini_httpx2_network_errors_retry_with_budget(monkeypatch):
    httpx2 = pytest.importorskip('httpx2')
    from google import genai
    from extension import ensemble
    calls = []
    class Models:
        def generate_content(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx2.ConnectError('private details must not be displayed')
            return SimpleNamespace(text='INTEREST RATE VOTE: 0.00%', usage_metadata=None)
    monkeypatch.setattr(genai, 'Client', lambda **kwargs: SimpleNamespace(models=Models()))
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-test-only')
    monkeypatch.setattr(ensemble.time, 'sleep', lambda _: None)
    provider = GeminiProvider()
    assert provider.retryable(httpx2.TimeoutException('fake'))
    assert provider.retryable(httpx2.ConnectError('fake'))
    assert provider.retryable(httpx2.ReadError('fake'))
    assert provider.retryable(httpx2.RemoteProtocolError('fake'))
    budget = CallBudget(2)
    text, _ = call_with_retry(provider, budget, 'gemini-3.1-flash-lite', .7, 'test', retries=1)
    assert text == 'INTEREST RATE VOTE: 0.00%' and len(calls) == budget.used == 2


def test_evaluation_rejects_fractional_outcome_before_writing(tmp_path):
    df = pd.DataFrame({'Date': pd.to_datetime(['2024-01-31']), 'y': [1.5],
                       'p_mkt_cut': [.2], 'p_mkt_hold': [.6], 'p_mkt_hike': [.2]})
    output = tmp_path / 'evaluation'
    with pytest.raises(ValueError, match='invalid labels/probabilities'):
        evaluate(df, {'Always-hold': 'always', 'Market-only logit': 'p_mkt_'}, outdir=output)
    assert not output.exists()


def test_openai_errors_are_redacted_and_rate_limits_remain_retryable(monkeypatch):
    import sys
    from extension import ensemble
    from extension.providers import OpenAIProvider, OpenAIRequestError
    secret = 'sensitive-openai-error-should-not-appear'
    calls = []
    class RateLimit(Exception):
        status_code = 429
    class Connection(Exception): pass
    class Timeout(Exception): pass
    class ServerError(Exception): pass
    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self, **kwargs):
            calls.append(1)
            if len(calls) in (1, 2): raise RateLimit(secret)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='INTEREST RATE VOTE: 0.00%'))], usage=None)
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeClient, RateLimitError=RateLimit,
        APIConnectionError=Connection, APITimeoutError=Timeout, InternalServerError=ServerError))
    monkeypatch.setattr(ensemble.time, 'sleep', lambda _: None)
    provider = OpenAIProvider()
    with pytest.raises(OpenAIRequestError) as caught:
        provider.request('fake-model', .7, 'prompt')
    assert secret not in str(caught.value) and '429' in str(caught.value)
    assert provider.retryable(caught.value)
    budget = CallBudget(2)
    text, _ = call_with_retry(provider, budget, 'fake-model', .7, 'prompt', retries=1)
    assert text == 'INTEREST RATE VOTE: 0.00%' and budget.used == 2


def test_ensemble_cli_writes_audit_and_refuses_to_overwrite(tmp_path, monkeypatch):
    from extension import ensemble
    from extension.run_all import main
    calls, per_role = [], {}
    class Fake:
        def request(self, model, temp, prompt):
            calls.append(1)
            role = next(r for r, name in ensemble.ROLES.items() if prompt.startswith(f'As {name},'))
            count = per_role.get(role, 0); per_role[role] = count + 1
            first = {'regional': '+0.25%', 'academic': '-0.25%', 'central': '0.00%'}
            return 'INTEREST RATE VOTE: ' + (first[role] if count == 0 else '0.00%'), {
                'prompt_token_count': 10, 'candidates_token_count': 3, 'total_token_count': 13}
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: Fake())
    meetings_input = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    run_ensemble(meetings_input, 'chain_of_draft/results', tmp_path / 'cache',
                 'gemini-3.1-flash-lite', .7, k=1, max_calls=3, workers=1, provider='gemini')
    out = tmp_path / 'new_results'
    args = ['--stage', 'ensemble', '--provider', 'gemini', '--model', 'gemini-3.1-flash-lite',
            '--k', '2', '--limit_meetings', '1', '--max_calls', '3', '--workers', '1',
            '--cache_dir', str(tmp_path / 'cache'), '--outdir', str(out), '--flag_vote', '23_2:0:regional']
    assert main(args) == 0 and len(calls) == 6
    votes = pd.read_csv(out / 'ensemble_votes.csv')
    meetings = pd.read_csv(out / 'ensemble_meetings.csv')
    probs = pd.read_csv(out / 'ensemble.csv')
    report = json.loads((out / 'ensemble_run.json').read_text(encoding='utf-8'))
    assert len(votes) == 6 and (votes.review_status == 'reported_inconsistency').sum() == 1
    assert meetings.resolved_samples.iloc[0] == 1 and meetings.unresolved_samples.iloc[0] == 1
    assert probs.ensemble_policy.iloc[0] == 'resolved_only_strict_majority_v1'
    assert report['provider_attempts_this_invocation'] == 3 and report['new_responses'] == 3
    assert report['reused_cache_records'] == 3
    assert report['new_response_token_usage']['input_tokens'] == {'total': 30, 'records_with_usage': 3, 'records': 3}
    assert report['existing_cache_token_usage']['input_tokens'] == {'total': 30, 'records_with_usage': 3, 'records': 3}
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    assert main(args) == 1 and len(calls) == 6
    assert before == {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    assert not (tmp_path / 'new_results_backups').exists()


def test_unknown_vote_flag_stops_before_calls(tmp_path, monkeypatch):
    from extension import ensemble
    monkeypatch.setattr(ensemble, 'make_provider', lambda provider: pytest.fail('no provider call allowed'))
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    with pytest.raises(ValueError, match='Vote flag does not match'):
        run_ensemble(meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7, 1,
                     max_calls=3, provider='gemini', flag_votes=['23_2:5:regional'])
    assert not list(tmp_path.rglob('*.json'))


def test_metrics_rejects_tied_maxima_instead_of_defaulting_to_cut():
    with pytest.raises(ValueError, match='tied top probabilities'):
        metrics([0], [[.5, .5, 0.]])


def test_final_eval_excludes_tied_maxima_from_common_intersection(tmp_path, monkeypatch):
    from extension.run_all import main, parser, market_settings
    # Simulate the Windows cp1252 default for any Path text I/O without an
    # explicit encoding, regardless of this machine's locale or UTF-8 mode.
    original_write_text, original_read_text = Path.write_text, Path.read_text
    def legacy_write(path, data, encoding=None, errors=None, **kwargs):
        return original_write_text(path, data, encoding=encoding or 'cp1252', errors=errors, **kwargs)
    def legacy_read(path, encoding=None, errors=None, **kwargs):
        return original_read_text(path, encoding=encoding or 'cp1252', errors=errors, **kwargs)
    monkeypatch.setattr(Path, 'write_text', legacy_write)
    monkeypatch.setattr(Path, 'read_text', legacy_read)
    with pytest.raises(UnicodeEncodeError):
        (tmp_path / 'legacy_encoding_probe.txt').write_text('\u2212')
    out = tmp_path / 'results'; out.mkdir()
    args = parser().parse_args(['--stage', 'eval', '--provider', 'gemini', '--outdir', str(out)])
    (out / 'market_metadata.json').write_text(json.dumps({'settings': market_settings(args)}), encoding='utf-8')
    dates = pd.to_datetime(['2023-02-01','2023-05-03','2024-03-20','2024-09-18'])
    pd.DataFrame({'Date': dates, 'y': [2,2,1,0], 'p_mkt_cut': [.2]*4,
                  'p_mkt_hold': [.6]*4, 'p_mkt_hike': [.2]*4,
                  'train_max_date': pd.to_datetime(['2022-12-14']*2 + ['2023-12-13']*2),
                  'test_year': [2023,2023,2024,2024]}).to_csv(out / 'market_oos.csv', index=False)
    pd.DataFrame({'Date': dates, 'p_ens_cut': [.2,.2,.4,.6], 'p_ens_hold': [.6,.6,.4,.2],
                  'p_ens_hike': [.2,.2,.2,.2], 'ensemble_k': [7]*4,
                  'ensemble_model': ['gemini-3.1-flash-lite']*4, 'ensemble_temp': [.7]*4,
                  'ensemble_provider': ['gemini']*4,
                  'ensemble_policy': ['resolved_only_strict_majority_v1']*4}).to_csv(out / 'ensemble.csv', index=False)
    assert main(['--stage', 'eval', '--provider', 'gemini', '--outdir', str(out)]) == 0
    exclusions = pd.read_csv(out / 'evaluation_exclusions.csv')
    assert any((exclusions.Date == '2024-03-20') & (exclusions.variant == 'K-ensemble only'))
    predictions = pd.read_csv(out / 'evaluation_predictions.csv')
    assert predictions.Date.tolist() == ['2024-09-18']
    assert len(pd.read_csv(out / 'ablation.csv')) == 6
    assert 'full \u2212 replication' in (out / 'ablation.md').read_text(encoding='utf-8')


def test_failed_ensemble_cli_records_invalid_counts_and_preserves_cache(tmp_path, monkeypatch, capsys):
    from extension import ensemble
    from extension.run_all import main
    secret = 'sensitive-text-not-for-diagnostics'
    calls = []
    class Fake:
        def request(self, *args):
            calls.append(1)
            return 'not a vote ' + secret, None
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda _: Fake())
    common = ['--stage', 'ensemble', '--provider', 'gemini', '--k', '1', '--limit_meetings', '1',
              '--max_calls', '3', '--workers', '1', '--cache_dir', str(tmp_path / 'cache')]
    out1 = tmp_path / 'failed_run'
    assert main(common + ['--outdir', str(out1)]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    report = json.loads((out1 / 'ensemble_run.json').read_text(encoding='utf-8'))
    assert report['completed'] is False and report['invalid_new_responses'] == 3
    assert report['provider_attempts_this_invocation'] == 3 and report['failed_provider_attempts_this_invocation'] == 0
    paths = sorted((tmp_path / 'cache').rglob('*.json'))
    before = {p: p.read_bytes() for p in paths}
    monkeypatch.setattr(ensemble, 'make_provider', lambda _: pytest.fail('invalid cache must stop before calls'))
    out2 = tmp_path / 'failed_resume'
    assert main(common + ['--outdir', str(out2)]) == 1
    report2 = json.loads((out2 / 'ensemble_run.json').read_text(encoding='utf-8'))
    assert report2['invalid_cached_records'] == 3 and report2['provider_attempts_this_invocation'] == 0
    assert {p: p.read_bytes() for p in paths} == before


def test_failed_provider_attempts_counted_and_redacted(tmp_path, monkeypatch, capsys):
    from extension import ensemble
    from extension.run_all import main
    class Fake:
        def request(self, *args): raise RuntimeError('sensitive-provider-secret')
        def retryable(self, error): return False
    monkeypatch.setattr(ensemble, 'make_provider', lambda _: Fake())
    out = tmp_path / 'failed_provider'
    args = ['--stage','ensemble','--provider','gemini','--k','1','--limit_meetings','1',
            '--max_calls','3','--workers','1','--cache_dir',str(tmp_path / 'cache'),'--outdir',str(out)]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert 'sensitive-provider-secret' not in captured.out + captured.err
    audit = json.loads((out / 'ensemble_run.json').read_text(encoding='utf-8'))
    assert audit['provider_attempts_this_invocation'] == 3
    assert audit['failed_provider_attempts_this_invocation'] == 3
    assert audit['invalid_new_responses'] == 0 and audit['failed_jobs'] == 3
    assert audit['prior_provider_attempts_and_failures'] == 'unknown'


def test_flagged_vote_must_match_existing_cache_before_spending(tmp_path, monkeypatch):
    from extension import ensemble
    monkeypatch.setattr(ensemble, 'make_provider', lambda _: pytest.fail('no provider call allowed'))
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    with pytest.raises(ValueError, match='flagged vote has no matching cached response'):
        run_ensemble(meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7, 1,
                     max_calls=3, provider='gemini', flag_votes=['23_2:0:regional'])
    assert not list(tmp_path.rglob('*.json'))


def test_cache_write_failure_is_not_misreported_as_provider_failure(tmp_path, monkeypatch):
    from extension import ensemble
    from pathlib import Path
    class Fake:
        def request(self, model, temp, prompt): return 'Dissenting Vote: 0.00%', {}
    monkeypatch.setattr(ensemble, 'make_provider', lambda _: Fake())
    monkeypatch.setattr(Path, 'write_text', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
    meetings = pd.DataFrame({'Date': pd.to_datetime(['2023-02-01'])})
    with pytest.raises(ensemble.EnsembleRunError) as caught:
        run_ensemble(meetings, 'chain_of_draft/results', tmp_path, 'gemini-3.1-flash-lite', .7, 1,
                     max_calls=3, workers=1, provider='gemini')
    audit = caught.value.audit
    assert audit['provider_attempts_this_invocation'] == 3
    assert audit['provider_responses_this_invocation'] == 3
    assert audit['failed_provider_attempts_this_invocation'] == 0
    assert audit['new_responses'] == 0 and audit['failed_jobs'] == 3
