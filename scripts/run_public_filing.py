"""Replay the real SEC filing excerpt comparison; never calls the API."""
import json
import shutil
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from referee.engine import review
from referee.gateway import Gateway

def run():
    cid = 'aclaris-public-filings'
    src = ROOT / 'data/public-filings'
    out = ROOT / 'docs/cases' / cid
    gw = Gateway('replay', ROOT / 'recordings/public-filings')
    r = review(src / 'v1.pdf', src / 'v2.pdf', '', gw, out,
               case={'id': cid, 'title': 'Real contract: Aclaris public filings',
                     'description': 'SEC-filed employment agreements, 2023 to 2024. Verbatim excerpts from salary and employee-benefits clauses; original SEC sources linked on each document.'},
               route=False, terms=False, concurrency=1)
    sources = json.loads((src / 'sources.json').read_text())
    for source in sources:
        doc = r['documents'][source['version']]
        doc['name'] = f"{source['year']} public filing (verbatim excerpts)"
        doc['public_source'] = source['url']
    (out / 'review.json').write_text(json.dumps(r, indent=1) + '\n')
    for name in ('SOURCE.md', 'sources.json'):
        shutil.copy2(src / name, out / name)
    index = ROOT / 'docs/cases/index.json'
    rows = [x for x in json.loads(index.read_text()) if x['id'] != cid]
    rows.insert(0, {**r['case'], 'status': r['status']})
    index.write_text(json.dumps(rows, indent=1) + '\n')
    assert not r['errors'] and len(r['changes']) == 2 and all(c['blocking'] for c in r['changes'])
    assert gw.sent == 0
    print('Real SEC excerpts: 2 substantive changes held; no errors; replay sent 0 API calls.')

if __name__ == '__main__':
    run()
