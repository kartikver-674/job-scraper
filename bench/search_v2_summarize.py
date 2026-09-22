"""Derive audit tables from captured public snapshots, offline; no production edits."""
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.search_v2_free_audit import identity_functions


def main():
    dest = ROOT / 'docs/search-v2-evidence'
    baseline_path = Path('/tmp/search-v2-current-free-jobs.json')
    expansion_path = Path('/tmp/search-v2-expansion-jobs.json')
    baseline = json.loads(baseline_path.read_text())
    expansion = json.loads(expansion_path.read_text())
    records = json.loads((dest / 'current-free-inventory.json').read_text())
    checked = json.loads((dest / 'expansion-validation.json').read_text())['records']
    discovery = json.loads((dest / 'expansion-discovery-registry.json').read_text())['records']
    key = identity_functions()
    today = date(2026, 9, 21)  # Frozen observation date; not the date of a future rerun.
    def age(r):
        try:
            return (today - date.fromisoformat(r['Posted Date'][:10])).days
        except (ValueError, KeyError, TypeError):
            return None
    def url(r):
        return r.get('Job URL', '').split('?')[0].rstrip('/').lower()
    groups = defaultdict(list)
    for row in baseline:
        groups[row['Source']].append(row)
    keys, urls = {key(r) for r in baseline}, {url(r) for r in baseline if url(r)}
    egroups = defaultdict(list)
    for row in expansion:
        egroups[row['Source']].append(row)
    per_source = {}
    for source, rows in groups.items():
        ages = [age(r) for r in rows]
        per_source[source] = {
            'fresh_14d': sum(a is not None and 0 <= a <= 14 for a in ages),
            'fresh_21d': sum(a is not None and 0 <= a <= 21 for a in ages),
            'unknown_date': sum(a is None for a in ages),
            'future_date': sum(a is not None and a < 0 for a in ages)}
    collisions = defaultdict(list)
    for row in baseline:
        collisions[key(row)].append(row)
    ct_different_urls = [rows for k, rows in collisions.items() if k and k[0] == 'ct'
                         and len({url(r) for r in rows if url(r)}) > 1]
    ct_different_locations = [rows for rows in ct_different_urls
                              if len({r['Location'].lower() for r in rows}) > 1]
    candidates = {}
    for source, rows in egroups.items():
        candidates[source] = {'raw': len(rows), 'engine_keys': len({key(r) for r in rows}),
            'marginal_raw_engine_keys': len({key(r) for r in rows} - keys),
            'marginal_raw_url_keys': len({url(r) for r in rows if url(r)} - urls),
            'fresh_14d': sum(age(r) is not None and 0 <= age(r) <= 14 for r in rows)}
    summary = {
        'status': 'MEASURED OFFLINE public snapshot analysis; heuristic identities, not true opportunity count',
        'as_of': str(today),
        'baseline_sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        'expansion_sha256': hashlib.sha256(expansion_path.read_bytes()).hexdigest(),
        'baseline': {'raw': len(baseline), 'engine_keys': len(keys), 'url_keys': len(urls),
            'fresh_14d': sum(x['fresh_14d'] for x in per_source.values()),
            'fresh_21d': sum(x['fresh_21d'] for x in per_source.values()),
            'distinct_company_labels': len({r['Company'] for r in baseline}),
            'remote_location_mentions': sum('remote' in r['Location'].lower() for r in baseline),
            'same_engine_key_different_url_groups': len(ct_different_urls),
            'same_engine_key_different_url_location_groups': len(ct_different_locations),
            'rows_in_different_url_groups': sum(len(r) for r in ct_different_urls),
            'salary_present': sum(bool(r.get('Salary')) for r in baseline),
            'experience_field_present': sum(bool(r.get('Experience')) for r in baseline)},
        'discovery_additional_identifiers': sum(not r['in_baseline'] for r in discovery),
        'discovery_additional_by_family': dict(Counter(r['provider'] for r in discovery if not r['in_baseline'])),
        'validation_statuses': dict(Counter(r['status'] for r in checked)),
        'expansion': {'raw': len(expansion), 'engine_keys': len({key(r) for r in expansion}),
            'marginal_raw_engine_keys': len({key(r) for r in expansion} - keys),
            'marginal_raw_url_keys': len({url(r) for r in expansion if url(r)} - urls),
            'fresh_14d': sum(age(r) is not None and 0 <= age(r) <= 14 for r in expansion),
            'request_seconds': sum(r.get('elapsed_ms', 0) for r in checked) / 1000,
            'response_bytes': sum(r.get('bytes', 0) for r in checked),
            'successful_board_median_seconds': statistics.median(r['elapsed_ms']/1000 for r in checked if r['status']=='valid_nonempty'),
            'successful_board_mean_seconds': statistics.mean(r['elapsed_ms']/1000 for r in checked if r['status']=='valid_nonempty')},
        'per_baseline_source_dates': per_source, 'per_expansion_source': candidates}
    assert len(records) == 140 and sum(r['active'] for r in records) == 134
    assert len(checked) == 365 and len(egroups) == 187
    (dest / 'snapshot-analysis.json').write_text(json.dumps(summary, indent=2)+'\n')
    table = ['## Exact current inventory (all 140 configured records)', '',
        'Source token identifies provider and board. All 129 ATS entries are active by membership; country metadata is absent. '
        'Five feeds are enabled; six optional employer entries are disabled. Endpoints/configuration and every requested field are in '
        '[the full CSV](current-free-inventory.csv) and [JSON](current-free-inventory.json). '
        'Here U is the current engine identity within that source; F14 is dated within 14 days as of 2026-09-21. '
        'Eligible and relevant counts per real candidate are UNKNOWN for every row. '
        'A successful fetch with F14=0 is not proof jobs have closed. Probe seconds include parsing for ATS and feed work.', '',
        '| Source / board | Company | Enabled | Fetch | Raw | F14 | U | Within-source identity collapse | Seconds |',
        '|---|---|---|---|---:|---:|---:|---:|---:|']
    for r in records:
        status = 'OK' if r['fetch_succeeds'] is True else 'FAIL' if r['fetch_succeeds'] is False else 'UNKNOWN'
        f = per_source.get(r['source_name'],{}).get('fresh_14d', 'UNKNOWN')
        vals = [r['source_name'],r['company'], 'yes' if r['active'] else 'no', status,
                r['raw_job_count'], f,r['unique_job_count'],
                None if r['duplicate_rate'] is None else f"{100*r['duplicate_rate']:.1f}%",
                None if r['fetch_time_ms'] is None else f"{r['fetch_time_ms']/1000:.2f}"]
        table.append('| '+' | '.join('UNKNOWN' if x is None else str(x).replace('|','/') for x in vals)+' |')
    (dest / 'current-free-inventory-table.md').write_text('\n'.join(table)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if not k.startswith('per_')},indent=2))


if __name__ == '__main__':
    main()
