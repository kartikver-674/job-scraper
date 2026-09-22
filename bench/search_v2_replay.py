"""Offline replay of public audit snapshots through unchanged production rules.

One synthetic profile per process; no private profiles, network or output writes
except the explicit audit JSON. Candidate descriptions remain in /tmp snapshots.
Example: python bench/search_v2_replay.py --case react_native --scope india
  --baseline /tmp/search-v2-current-free-jobs.json
  --expansion /tmp/search-v2-expansion-jobs.json --output /tmp/replay.json
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import socket
import sys
import time
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--scope', choices=['india', 'remote'], required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--expansion', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    os.environ.pop('JOB_PROFILE', None)
    os.environ['SWEEP_EXPERIENCE_MISMATCH_GUARD'] = '0'
    for key in list(os.environ):
        if key.startswith('APIFY_TOKEN'):
            del os.environ[key]
    def deny(*a, **kw):
        raise RuntimeError('Offline audit: network forbidden')
    socket.socket.connect = deny
    socket.create_connection = deny
    sys.path[:0] = [str(ROOT), str(ROOT / 'auto-apply')]
    import config
    import make_profile
    fixtures = json.loads((ROOT / 'docs/search-v2-evidence/paid-plans.json').read_text())
    fixture = next(f for f in fixtures['synthetic_profile_fixtures'] if f['case'] == args.case)
    prefs = dict(fixture['prefs'], work_scope=args.scope,
                 remote_scopes=[] if args.scope == 'india' else ['worldwide', 'remote'])
    # Scope-wide inventory; no explicit city picker narrowing. This is stated in output.
    module = ModuleType('synthetic_audit')
    rendered = make_profile.render('synthetic_audit', fixture['derived'], prefs)
    exec(compile(rendered, '<synthetic audit profile>', 'exec'), module.__dict__)
    config._overlay(module)
    import scraper
    from bench.search_v2_free_audit import ROLE_PATTERNS
    assert not scraper.experience_guard.enabled()

    def load(path):
        raw = Path(path).read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    baseline, baseline_sha = load(args.baseline)
    expansion, expansion_sha = load(args.expansion)
    # Recompute home-country board evidence from the complete probed board, before gates.
    for rows in [baseline, expansion]:
        home = defaultdict(bool)
        for row in rows:
            home[row.get('Source')] |= scraper.is_home_location(row.get('Location', ''))
        for row in rows:
            if ':' in row.get('Source', ''):
                row['hires_home'] = 'yes' if home[row['Source']] else 'no'

    def run(rows):
        start = time.perf_counter()
        cpu_start = time.process_time()
        source_survivors = [scraper._truncate_desc(dict(r)) for r in rows
                            if scraper.is_dev_title(r.get('Title', ''))
                            and scraper.location_allowed(r.get('Location', ''))]
        gate_seconds = time.perf_counter() - start
        timings = Counter()
        counts = defaultdict(Counter)
        originals = {n: getattr(scraper, n) for n in
                     ['score_job', 'is_recent', 'comp_ok', 'reachable',
                      'onsite_or_hybrid', 'in_home_country', 'dedupe', 'to_output']}
        def wrap(name):
            def wrapped(*a, **kw):
                t = time.perf_counter()
                out = originals[name](*a, **kw)
                timings[name] += time.perf_counter() - t
                counts[name]['calls'] += 1
                if name == 'score_job':
                    counts[name]['passed'] += out is not None
                elif name in ['is_recent', 'comp_ok', 'reachable', 'onsite_or_hybrid', 'in_home_country']:
                    counts[name]['passed'] += bool(out)
                elif name == 'dedupe':
                    counts[name]['input_rows'] += len(a[0])
                    counts[name]['output_rows'] += len(out)
                return out
            return wrapped
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in originals:
                stack.enter_context(patch.object(scraper, name, wrap(name)))
            out = scraper.finalize(source_survivors)
        return out, {
            'observed_normalized': len(rows), 'source_gate_passed': len(source_survivors),
            'final': len(out), 'score_positive': sum(r['score'] > 0 for r in out),
            'score_at_least_10': sum(r['score'] >= 10 for r in out),
            'known_fresh_14d_final': sum(scraper._parse_date(r.get('date_posted')) is not None
                and scraper.is_recent(r.get('date_posted'), 14) for r in out),
            'unique_final_companies': len({r['company'] for r in out}),
            'unique_final_locations': len({r['location'] for r in out}),
            'unique_final_sources': len({r['source_site'] for r in out}),
            'final_role_title_proxy_counts': {label: sum(bool(re.search(pattern, r['title'], re.I))
                for r in out) for label, pattern in ROLE_PATTERNS.items()},
            'source_attribution': dict(Counter(r['source_site'] for r in out)),
            'positive_score_source_attribution': dict(Counter(r['source_site'] for r in out if r['score'] > 0)),
            'high_score_source_attribution': dict(Counter(r['source_site'] for r in out if r['score'] >= 10)),
            'boundary_counts': dict(counts), 'boundary_seconds': dict(timings),
            'total_local_seconds': time.perf_counter() - start,
            'process_cpu_seconds': time.process_time() - cpu_start,
            'source_gate_seconds': gate_seconds,
            'process_peak_rss_platform_units': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'last_stats': dict(scraper.LAST_STATS)}

    base_out, base_stats = run(baseline)
    candidate_out, candidate_stats = run(expansion)
    combined_out, combined_stats = run(baseline + expansion)
    keys = {scraper.job_key(r) for r in base_out}
    by_source = {r['Source']: [] for r in expansion}
    raw_by_source = defaultdict(list)
    for row in expansion:
        raw_by_source[row['Source']].append(row)
    for row in candidate_out:
        by_source[row['source_site']].append(row)
    # Rank after measurement for a descriptive upper-envelope, not out-of-sample optimization.
    order = sorted(by_source, key=lambda s: (
        -sum(scraper.job_key(r) not in keys and r['score'] > 0 for r in by_source[s]),
        -len(by_source[s]), s))
    raw_baseline_keys = {scraper.job_key(r) for r in baseline}
    url_baseline_keys = {scraper._canonical_url(r.get('Job URL')) for r in baseline}
    observations = json.loads((ROOT / 'docs/search-v2-evidence/expansion-validation.json').read_text())['records']
    observation_by_source = {f"{r['provider']}:{r['board_id']}": r for r in observations}
    cumulative = []
    chosen = set()
    for n in [25, 50, 100, 250, len(order)]:
        if n > len(order) or n in chosen:
            continue
        chosen.add(n)
        rows = [r for s in order[:n] for r in by_source[s]]
        additional = [r for r in rows if scraper.job_key(r) not in keys]
        raw_rows = [r for s in order[:n] for r in raw_by_source[s]]
        cumulative.append({'boards_selected': n,
            'measured_source_boundary_seconds_sum': sum(observation_by_source[s]['elapsed_ms'] for s in order[:n]) / 1000,
            'measured_response_bytes_sum': sum(observation_by_source[s].get('bytes', 0) for s in order[:n]),
            'request_count': n, 'apify_cost_usd': 0,
            'raw_observations_added': len(raw_rows),
            'marginal_raw_engine_keys': len({scraper.job_key(r) for r in raw_rows} - raw_baseline_keys),
            'marginal_raw_url_keys': len({scraper._canonical_url(r.get('Job URL')) for r in raw_rows} - url_baseline_keys),
            'additional_final_keys': len({scraper.job_key(r) for r in additional}),
            'additional_positive_score_keys': len({scraper.job_key(r) for r in additional if r['score'] > 0}),
            'additional_score_at_least_10_keys': len({scraper.job_key(r) for r in additional if r['score'] >= 10}),
            'new_company_labels': len({r['company'] for r in additional} - {r['company'] for r in base_out}),
            'new_location_strings': len({r['location'] for r in additional} - {r['location'] for r in base_out}),
            'new_role_title_proxy_families': [label for label, pattern in ROLE_PATTERNS.items()
                if any(re.search(pattern, r['title'], re.I) for r in additional)
                and not any(re.search(pattern, r['title'], re.I) for r in base_out)],
            'board_ids': order[:n]})
    # Concentration after production global winner selection, including all zero sources in denominator.
    source_count = Counter(r['source_site'] for r in base_out if r['score'] > 0)
    denominator = sum(source_count.values())
    result = {'status': 'MEASURED OFFLINE synthetic profile; not real-candidate validation',
        'case': args.case, 'scope': args.scope, 'city_picker': 'not narrowed',
        'policy_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
        'baseline_snapshot_sha256': baseline_sha, 'expansion_snapshot_sha256': expansion_sha,
        'baseline': base_stats, 'expansion': candidate_stats, 'combined': combined_stats,
        'baseline_positive_score_concentration': {str(n):
            round(sum(c for _, c in source_count.most_common(n)) / denominator, 6)
            if denominator else None for n in [10, 25, 50]},
        'tranches': cumulative,
        'candidate_order': order,
        'top20_comparison': {'baseline_count': len(base_out[:20]),
            'combined_count': len(combined_out[:20]),
            'baseline_keys_retained_in_combined_top20': len({scraper.job_key(r) for r in base_out[:20]}
                & {scraper.job_key(r) for r in combined_out[:20]}),
            'new_to_baseline_in_combined_top20': sum(scraper.job_key(r) not in keys for r in combined_out[:20])},
        'limits': ['Snapshot includes only endpoint observations captured by separate probes.',
            'Feed internal filters/query choices differ from a full candidate web run.',
            'Score >0 and >=10 are descriptive fixture thresholds; no ranking/gate changes.',
            'Final means current engine eligibility, not verified reachable URLs or actual fit.',
            'Company labels for expansion may be slug-inferred; cross-board identity undercounts possible.',
            'Tranches ranked on this snapshot are optimistic descriptive selection, not validated future yield.',
            'Missing/unknown dates retained by production; known_fresh tracked separately.']}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'case': args.case, 'scope': args.scope,
        'baseline_final': len(base_out), 'expanded_final': len(combined_out),
        'baseline_positive': base_stats['score_positive'],
        'expanded_positive': combined_stats['score_positive']}))


if __name__ == '__main__':
    main()
