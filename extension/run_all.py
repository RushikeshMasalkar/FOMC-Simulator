"""Run the extension in stages without changing the original replication."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from .market_data import load_fred
from .market_prior import load_meetings, build_features, walk_forward
from .committee import load_baseline
from .fusion import smooth_votes, walk_forward_fusion
from .ensemble import run_ensemble, probe
from .providers import local_gemini_diagnostics
from .evaluate import evaluate


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=['all', 'market', 'fusion', 'ensemble', 'eval', 'probe', 'diagnose'], default='all')
    p.add_argument('--k', type=int, default=7)
    p.add_argument('--temp', type=float, default=0.7)
    p.add_argument('--max_calls', type=int, default=0, help='Hard maximum API requests this invocation, including retries (default 0)')
    p.add_argument('--provider', choices=['openai', 'gemini'], default='openai')
    p.add_argument('--model', help='Default: gpt-4o-mini for OpenAI, gemini-3.1-flash-lite for Gemini')
    p.add_argument('--dry_run', action='store_true')
    p.add_argument('--limit_meetings', type=int)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--approve_full_batch', action='store_true', help='Only after inspecting smoke outputs')
    p.add_argument('--min_years', type=int, default=5)
    p.add_argument('--min_meetings', type=int, default=40)
    p.add_argument('--min_fusion', type=int, default=1)
    p.add_argument('--c', type=float, default=0.5)
    p.add_argument('--extra_features', action='store_true', help='Predeclare curve/VIX ablation; changes market model')
    p.add_argument('--bootstrap', type=int, default=1000)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--cutoff', help='Audited knowledge cutoff (YYYY-MM-DD), otherwise no split')
    p.add_argument('--meetings', default='state_of_art_model/fomc_meetings.csv')
    p.add_argument('--baseline_root', default='chain_of_draft/results')
    p.add_argument('--baseline_csv', help='Optional audited CSV: Date, comm_decision (0=cut, 1=hold, 2=hike)')
    p.add_argument('--ensemble_csv', help='Optional audited CSV: Date, p_ens_cut, p_ens_hold, p_ens_hike')
    p.add_argument('--fred_dir', default='data/fred')
    p.add_argument('--cache_dir', default='cache/ensemble')
    p.add_argument('--outdir', default='results')
    return p


def config(args, out):
    names = ('numpy', 'pandas', 'scipy', 'scikit-learn', 'matplotlib', 'pandas-datareader', 'openai', 'google-genai')
    versions = {}
    for name in names:
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = 'not installed'
    data = {'arguments': vars(args), 'python': platform.python_version(), 'versions': versions,
            'class_order': ['cut', 'hold', 'hike'], 'baseline_run': 1,
            'ensemble_method': 'three independent cached-discussion vote replays; not full CrewAI'}
    path = out / 'run_config.json'
    history = json.loads(path.read_text()) if path.exists() else {'runs': []}
    data['timestamp_utc'] = datetime.now(timezone.utc).isoformat()
    history['runs'].append(data)
    path.write_text(json.dumps(history, indent=2) + '\n')


def read_csv(path):
    return pd.read_csv(path, parse_dates=['Date'])


def baseline(meetings, args):
    if args.baseline_csv:
        data = read_csv(args.baseline_csv)
        if data.Date.duplicated().any() or not data.comm_decision.isin([0,1,2]).all():
            raise ValueError('baseline CSV must have unique dates and decisions in {0,1,2}')
        ps = np.array([smooth_votes([int(v)]) for v in data.comm_decision])
        data[['p_comm_cut', 'p_comm_hold', 'p_comm_hike']] = ps
        return data
    data, rejected = load_baseline(meetings, args.baseline_root)
    for item in rejected: print('Rejected baseline:', item, file=sys.stderr)
    for item in data.attrs.get('provenance', []):
        if item['source'].startswith('recovered'):
            print(f"Recovered {item['meeting_id']} from verified vote tasks; see baseline_provenance.json")
    if data.empty: raise ValueError('No valid checked-in single-run committee outputs')
    return data


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def market_settings(args):
    return {'min_years': args.min_years, 'min_meetings': args.min_meetings,
            'c': args.c, 'extra_features': args.extra_features,
            'meetings_sha256': file_hash(args.meetings)}


def read_market(args, out):
    metadata = json.loads((out / 'market_metadata.json').read_text())
    if metadata['settings'] != market_settings(args):
        raise ValueError('Market-stage settings or meeting file differ; use the same flags/data or rerun market in a fresh outdir')
    return read_csv(out / 'market_oos.csv')


def market_stage(meetings, args, out):
    data = load_fred(args.fred_dir)
    features = build_features(meetings, data)
    features.to_csv(out / 'market_features.csv', index=False)
    pred = walk_forward(features, args.min_years, args.min_meetings, args.c, args.extra_features)
    pred.to_csv(out / 'market_oos.csv', index=False)
    from .market_data import SERIES
    metadata = {'settings': market_settings(args), 'fred_sha256': {s: file_hash(Path(args.fred_dir) / f'{s}.csv') for s in SERIES}}
    (out / 'market_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Market OOS predictions: {len(pred)} meetings; saved {out / "market_oos.csv"}')


def phase2(meetings, args, out):
    market = read_market(args, out)
    single = baseline(meetings, args)
    (out / 'baseline_provenance.json').write_text(json.dumps(single.attrs.get('provenance', [{'source': 'external CSV', 'path': args.baseline_csv}]), indent=2) + '\n')
    joined = market.merge(single, on='Date', how='inner', validate='one_to_one')
    pred, theta = walk_forward_fusion(joined, min_fusion=args.min_fusion)
    folder = out / 'phase2'; folder.mkdir(parents=True, exist_ok=True)
    pred.to_csv(folder / 'predictions.csv', index=False)
    variants = {'Always-hold': 'always', 'Market-only logit': 'p_mkt_',
                'Replication (single run)': 'p_comm_', 'Replication + fusion': 'p_fused_'}
    table = evaluate(pred, variants, folder, args.bootstrap, args.seed, theta, args.cutoff, 'Phase 2: single-run fusion (not the six-variant final table)')
    print(f'Phase 2: {len(pred)} meetings; table at {folder / "ablation.md"}')
    print(table[['accuracy', 'macro_f1', 'mcc', 'brier', 'log_loss', 'ece', 'ece_adaptive']].to_string(na_rep='N/A'))


def ensemble_stage(meetings, args, out):
    data = run_ensemble(meetings, args.baseline_root, args.cache_dir, args.model, args.temp,
                        args.k, args.max_calls, args.workers, args.dry_run, args.limit_meetings, args.approve_full_batch, provider=args.provider)
    if data is not None:
        data['ensemble_k'] = args.k
        data['ensemble_model'] = args.model
        data['ensemble_provider'] = args.provider
        data['ensemble_temp'] = args.temp
        data.to_csv(out / 'ensemble.csv', index=False)
        print(f'Saved {out / "ensemble.csv"}. Inspect the printed votes and cached outputs before a full run.')


def final_eval(meetings, args, out):
    market = read_market(args, out)
    single = baseline(meetings, args)
    (out / 'baseline_provenance.json').write_text(json.dumps(single.attrs.get('provenance', [{'source': 'external CSV', 'path': args.baseline_csv}]), indent=2) + '\n')
    ensemble = read_csv(args.ensemble_csv or out / 'ensemble.csv')
    if ensemble.Date.duplicated().any(): raise ValueError('duplicate ensemble dates')
    if not args.ensemble_csv:
        # Old outputs have no provider metadata and cannot be safely attributed.
        if 'ensemble_provider' not in ensemble:
            raise ValueError('Legacy ensemble lacks provider metadata; use an audited --ensemble_csv or regenerate')
        expected = {'ensemble_k': args.k, 'ensemble_model': args.model, 'ensemble_temp': args.temp, 'ensemble_provider': args.provider}
        for key, value in expected.items():
            if key not in ensemble or not (ensemble[key] == value).all():
                raise ValueError(f'Ensemble {key} differs from evaluation settings; pass matching flags or use a separate outdir')
    ens_cols = ['p_ens_cut','p_ens_hold','p_ens_hike']
    q = ensemble[ens_cols].to_numpy(float)
    if not np.isfinite(q).all() or (q <= 0).any() or not np.allclose(q.sum(axis=1), 1):
        raise ValueError('ensemble probabilities must be positive, finite and sum to one')
    sb, st = walk_forward_fusion(market.merge(single, on='Date', validate='one_to_one'), min_fusion=args.min_fusion)
    eb, et = walk_forward_fusion(market.merge(ensemble, on='Date', validate='one_to_one'), comm_prefix='p_ens_', min_fusion=args.min_fusion)
    eb = eb[['Date'] + ens_cols + ['p_fused_cut','p_fused_hold','p_fused_hike']].rename(columns={f'p_fused_{c}': f'p_efused_{c}' for c in ('cut','hold','hike')})
    both = sb.merge(eb, on='Date', how='inner', validate='one_to_one')
    if both.empty: raise ValueError('No common meetings across every evaluated variant')
    both.to_csv(out / 'evaluation_predictions.csv', index=False)
    st['variant'] = 'single'; et['variant'] = 'ensemble'
    # Plot the full system's parameters; retain both sets of fold estimates.
    pd.concat([st, et]).to_csv(out / 'all_fusion_theta.csv', index=False)
    variants = {'Always-hold':'always', 'Market-only logit':'p_mkt_',
                'Replication (single run)':'p_comm_', 'Replication + fusion':'p_fused_',
                'K-ensemble only':'p_ens_', 'K-ensemble + fusion':'p_efused_'}
    table = evaluate(both, variants, out, args.bootstrap, args.seed, et, args.cutoff)
    print(table[['accuracy', 'macro_f1', 'mcc', 'brier', 'log_loss', 'ece', 'ece_adaptive']].to_string(na_rep='N/A'))
    print(f'Full intersection: {len(both)} meetings; see {out / "ablation.md"}')


def backup_results(out):
    if not out.exists():
        return
    if not out.is_dir() or out.resolve() == Path.cwd().resolve():
        raise ValueError('outdir must be a dedicated results directory, not the repository root')
    root = out.parent / (out.name + '_backups')
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = root / stamp
    shutil.copytree(out, backup)
    print(f'Existing results backed up to {backup}')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.model is None:
        args.model = 'gemini-3.1-flash-lite' if args.provider == 'gemini' else 'gpt-4o-mini'
    out = Path(args.outdir)
    try:
        if args.dry_run and args.stage not in ('ensemble',):
            raise ValueError('--dry_run is supported with --stage ensemble only')
        if args.stage == 'diagnose':
            if args.provider != 'gemini':
                raise ValueError('diagnose currently supports --provider gemini')
            local_gemini_diagnostics(args.model)
            return 0
        if args.stage == 'probe':
            probe(args.provider, args.model, args.temp, args.max_calls)
            return 0
        if not args.dry_run:
            backup_results(out)
            out.mkdir(parents=True, exist_ok=True)
            config(args, out)
        meetings = load_meetings(args.meetings)
        if args.stage in ('all', 'market'): market_stage(meetings, args, out)
        if args.stage in ('all', 'fusion'): phase2(meetings, args, out)
        if args.stage in ('all', 'ensemble'): ensemble_stage(meetings, args, out)
        if args.stage == 'eval': final_eval(meetings, args, out)
        if args.stage == 'all' and not args.dry_run:
            print('Stopped after ensemble for manual smoke-test review. Run --stage eval after reviewing and completing the batch.')
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f'Stopped: {e}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
