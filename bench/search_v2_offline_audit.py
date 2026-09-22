"""Audit-only identity counterexamples and local finalization benchmark.

No actor, HTTP, profile file, or production output writes. Run from repo root:
  python bench/search_v2_offline_audit.py --output /tmp/identity.json
Optional --rows accepts public normalized rows captured by the free harness.
The default configuration is a reference policy, not a real user's profile.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import tracemalloc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--rows')
    args = parser.parse_args()
    # Never inherit a private profile or accidentally enable an optional guard.
    os.environ.pop('JOB_PROFILE', None)
    os.environ['SWEEP_EXPERIENCE_MISMATCH_GUARD'] = '0'
    def no_network(*args, **kwargs):
        raise RuntimeError('Network forbidden by offline audit harness')
    socket.socket.connect = no_network
    socket.create_connection = no_network
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import scraper

    def row(**kw):
        return dict(Title='Software Engineer', Company='Example',
                    Location='Bengaluru', **kw)
    cases = {
        'different_city_distinct_url_collapses': [
            row(**{'Job URL': 'https://example.test/jobs/1'}),
            dict(row(**{'Job URL': 'https://example.test/jobs/2'}), Location='Pune')],
        'same_req_across_companies_collapses': [
            row(req_number='123'), dict(row(req_number='123'), Company='Different')],
        'same_url_different_titles_survives': [
            row(**{'Job URL': 'https://example.test/jobs/1'}),
            dict(row(**{'Job URL': 'https://example.test/jobs/1'}), Title='Backend Engineer')],
        'query_only_job_id_collapses_without_company_title': [
            {'Job URL': 'https://example.test/job?id=1'},
            {'Job URL': 'https://example.test/job?id=2'}],
        'distinct_req_numbers_survive': [row(req_number='1'), row(req_number='2')],
        'unkeyed_rows_survive': [{}, {}],
    }
    expected = [1, 1, 2, 1, 2, 2]
    result = {'method': 'MEASURED synthetic counterexamples; no prevalence estimate',
              'cases': {}, 'network': 'blocked', 'production_changes': False}
    for (name, rows), count in zip(cases.items(), expected):
        unique = len(scraper.dedupe(rows))
        assert unique == count, (name, unique, count)
        result['cases'][name] = {'input': len(rows), 'output': unique,
                                'keys': [scraper.job_key(r) for r in rows]}
    assert scraper.is_recent('', 14) == (not scraper.SETTINGS['drop_undated'])
    result['undated_passes_recency'] = scraper.is_recent('', 14)
    if args.rows:
        payload = json.loads(Path(args.rows).read_text())
        rows = payload if isinstance(payload, list) else payload['rows']
        tracemalloc.start()
        start = time.perf_counter()
        out = scraper.finalize([dict(r) for r in rows])
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result['reference_policy_benchmark'] = {
            'input_rows': len(rows), 'final_rows': len(out),
            'seconds_with_tracemalloc_overhead': elapsed,
            'peak_python_allocations_bytes': peak,
            'last_stats': dict(scraper.LAST_STATS),
            'warning': 'Default reference policy; not personalized eligibility, RSS, or production wall time.'}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
