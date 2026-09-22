"""Audit only: validate a bounded, explicit list of public ATS board URLs.

No discovery crawler, paid clients, config writes, retries, or concurrency.
Run from repository root with python -m bench.search_v2_expansion_audit.
Descriptions are retained only in the optional /tmp replay snapshot, never in
the checked-in endpoint evidence. All geographic/title metrics are proxies,
not eligibility, freshness, job reachability, or candidate relevance claims.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from sources.ats import ATS, _row
from sources._http import dig

INDIA = re.compile(r"\bindia\b|bengaluru|bangalore|hyderabad|gurgaon|gurugram|pune|mumbai|noida|delhi|chennai|chandigarh|mohali", re.I)
TECH = re.compile(r"engineer|developer|software|full.?stack|frontend|backend|react|mobile|devops|cloud|data|machine learning|\bqa\b|automation|salesforce|business analyst", re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', default='docs/search-v2-evidence/expansion-validation-targets.json')
    parser.add_argument('--out', default='docs/search-v2-evidence/expansion-validation.json')
    parser.add_argument('--jobs', default='/tmp/search-v2-expansion-jobs.json')
    parser.add_argument('--timeout', type=float, default=12)
    parser.add_argument('--delay', type=float, default=.25)
    parser.add_argument('--limit', type=int, default=500)
    args = parser.parse_args()
    targets = json.loads(Path(args.targets).read_text())
    if not 1 <= args.limit <= 1000 or len(targets) > args.limit:
        parser.error('Provide at most --limit explicit targets (limit range 1..1000).')
    if args.timeout <= 0 or args.delay < .1:
        parser.error('Positive timeout and at least 0.1 seconds serial delay required.')
    if not Path(args.jobs).resolve().is_relative_to(Path('/tmp').resolve()):
        parser.error('Public job descriptions may only be written below /tmp.')
    evidence, alljobs, blocked = [], [], set()
    start = time.perf_counter()
    for index, target in enumerate(targets, 1):
        provider, token = target['provider'], target['board_id']
        if provider not in ATS or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
            raise ValueError('Only known providers and literal board identifiers allowed')
        record = dict(target, endpoint=ATS[provider]['url'].format(token=token),
                      observed_at=datetime.now(timezone.utc).isoformat(), requests=0)
        tick = time.perf_counter()
        if provider in blocked:
            record.update(status='provider_stopped_after_429', elapsed_ms=0)
            evidence.append(record)
            continue
        try:
            req = urllib.request.Request(record['endpoint'], headers={
                'User-Agent': 'Sweep-Audit/1.0 (bounded public job-board validation)',
                'Accept': 'application/json'})
            record['requests'] = 1
            with urllib.request.urlopen(req, timeout=args.timeout) as response:
                body = response.read(32 * 1024 * 1024 + 1)
                record.update(http_status=response.status, bytes=len(body),
                              content_type=response.headers.get('Content-Type'),
                              cache_control=response.headers.get('Cache-Control'),
                              etag=response.headers.get('ETag'),
                              last_modified=response.headers.get('Last-Modified'))
            if len(body) > 32 * 1024 * 1024:
                raise ValueError('audit response byte bound exceeded')
            payload = json.loads(body)
            spec = ATS[provider]
            items = dig(payload, spec['list']) if spec['list'] else payload
            if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                raise ValueError('expected array of job objects')
            nt = time.perf_counter()
            rows = [_row(item, provider, token, target['company_name'], spec) for item in items]
            record['normalize_ms'] = round((time.perf_counter() - nt) * 1000, 2)
            home = any(INDIA.search(row['Location']) for row in rows)
            for row in rows:
                row['hires_home'] = 'yes' if home else 'no'
            ids = [str(item.get('id') or item.get('_id') or '') for item in items]
            record.update(status='valid_nonempty' if rows else 'valid_empty_identity_unconfirmed',
                          raw_count=len(rows),
                          unique_url_count=len({r['Job URL'] for r in rows if r['Job URL']}),
                          id_present_count=sum(bool(i) for i in ids),
                          id_unique_count=len({i for i in ids if i}),
                          title_present_count=sum(bool(r['Title']) for r in rows),
                          location_present_count=sum(bool(r['Location']) for r in rows),
                          date_present_count=sum(bool(r['Posted Date']) for r in rows),
                          description_present_count=sum(bool(r['Description']) for r in rows),
                          india_location_proxy=sum(bool(INDIA.search(r['Location'])) for r in rows),
                          technical_title_proxy=sum(bool(TECH.search(r['Title'])) for r in rows),
                          india_technical_proxy=sum(bool(INDIA.search(r['Location']) and TECH.search(r['Title'])) for r in rows),
                          remote_location_proxy=sum('remote' in r['Location'].lower() for r in rows),
                          provider_reported_total=payload.get('totalFound') if isinstance(payload, dict) else None,
                          response_sha256=hashlib.sha256(body).hexdigest(),
                          first_item_keys=sorted(items[0]) if items else [],
                          sample_job_urls=[r['Job URL'] for r in rows[:3]])
            alljobs.extend(rows)
        except urllib.error.HTTPError as exc:
            record.update(status='http_error', http_status=exc.code, error=str(exc))
            if exc.code == 429:
                blocked.add(provider)
        except Exception as exc:
            record.update(status='fetch_or_schema_error', error=f'{type(exc).__name__}: {exc}')
        record['elapsed_ms'] = round((time.perf_counter() - tick) * 1000, 2)
        evidence.append(record)
        # Checkpoints retain already-observed results if interrupted.
        Path(args.out).write_text(json.dumps({'records': evidence, 'elapsed_seconds': round(time.perf_counter()-start, 3)}, indent=1))
        Path(args.jobs).write_text(json.dumps(alljobs))
        print(index, provider, token, record['status'], record.get('raw_count', ''), flush=True)
        time.sleep(args.delay)
    print(json.dumps(dict(Counter(r['status'] for r in evidence))), flush=True)


if __name__ == '__main__':
    main()
