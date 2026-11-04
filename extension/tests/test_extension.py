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
    assert 'Non-hold: N/A (no non-hold meetings)' in (tmp_path / 'ablation.md').read_text()
    assert 'N/A' in (tmp_path / 'ablation.md').read_text()
    report = json.loads((tmp_path / 'confusion_and_classes.json').read_text())
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
    (out / 'market_metadata.json').write_text(json.dumps({'settings': market_settings(args)}))
    dates = pd.to_datetime(['2023-02-01','2023-05-03','2024-03-20','2024-09-18'])
    pred = pd.DataFrame({'Date': dates, 'y': [2,2,1,0], 'p_mkt_cut': [.2]*4,
                         'p_mkt_hold': [.6]*4, 'p_mkt_hike': [.2]*4,
                         'train_max_date': pd.to_datetime(['2022-12-14']*2 + ['2023-12-13']*2),
                         'test_year': [2023,2023,2024,2024]})
    pred.to_csv(out / 'market_oos.csv', index=False)
    (out / 'old_result.txt').write_text('do not overwrite')
    assert main(['--stage', 'fusion', '--outdir', str(out)]) == 0
    assert len(pd.read_csv(out / 'phase2/predictions.csv')) == 2
    assert (out / 'phase2/ablation.md').exists()
    prov = json.loads((out / 'baseline_provenance.json').read_text())
    assert any(x['source'] == 'recovered_from_completed_voting_tasks' for x in prov)
    backups = list((tmp_path / 'results_backups').glob('*/old_result.txt'))
    assert len(backups) == 1 and backups[0].read_text() == 'do not overwrite'


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
    (outdir / 'result.txt').write_text('unchanged')
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
    assert (outdir / 'result.txt').read_text() == 'unchanged'


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
