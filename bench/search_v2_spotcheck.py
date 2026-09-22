"""Explicit, sequential audit checks: two failed baseline routes and nine boards.

No actor clients. Only public GETs from measured endpoints. No full content saved.
Run with --live; without it prints the exact bounded request list.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'docs/search-v2-evidence'
BOARDS = ['fivetran', 'abnormalsecurity', 'apolloio', 'pubmatic', 'brex',
          'vercel', 'jumio', 'catawiki', 'zetaglobal']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    validated = json.loads((DEST / 'expansion-validation.json').read_text())['records']
    targets = [
        {'purpose': 'recheck baseline 404', 'url': 'https://boards-api.greenhouse.io/v1/boards/postman/jobs?content=true'},
        {'purpose': 'recheck failing configured WWR route', 'url': 'https://weworkremotely.com/categories/remote-jobs.rss'},
        {'purpose': 'check documented canonical WWR route', 'url': 'https://weworkremotely.com/remote-jobs.rss'}]
    for board in BOARDS:
        record = next(r for r in validated if r['provider']=='greenhouse' and r['board_id']==board)
        targets.append({'purpose': 'board identity', 'board': board,
                        'url': f'https://boards-api.greenhouse.io/v1/boards/{board}'})
        targets.append({'purpose': 'one observed job URL reachable', 'board': board,
                        'url': record['sample_job_urls'][0]})
    if not args.live:
        print(json.dumps(targets, indent=2))
        return
    result = []
    for target in targets:
        from datetime import datetime, timezone
        row = dict(target, observed_at=datetime.now(timezone.utc).isoformat())
        start = time.perf_counter()
        try:
            request = urllib.request.Request(target['url'], headers={'User-Agent': 'Sweep-Audit/1.0'})
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read(2*1024*1024+1)
                row.update(status=response.status, final_url=response.url,
                           content_type=response.headers.get('Content-Type'), bytes_read=len(body))
                if row['purpose']=='board identity':
                    row['company_name'] = json.loads(body).get('name')
                if 'WWR route' in row['purpose'] and response.status==200:
                    import xml.etree.ElementTree as ET
                    root=ET.fromstring(body)
                    row['rss_items']=len(root.findall('.//item'))
        except Exception as exc:
            row.update(status=getattr(exc,'code',None), error=f'{type(exc).__name__}: {exc}')
        row['seconds']=round(time.perf_counter()-start,3)
        result.append(row)
        (DEST / 'first-tranche-spotchecks.json').write_text(json.dumps(result,indent=2)+'\n')
        print(target['purpose'],target.get('board',''),row.get('status'),row.get('company_name',''),flush=True)
        time.sleep(.25)


if __name__=='__main__':
    main()
