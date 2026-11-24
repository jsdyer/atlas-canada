#!/usr/bin/env python3
"""Reassess saved captures from existing public Atlas results; creates no measurements."""
import sys,json,urllib.request,concurrent.futures
from pathlib import Path
p=Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'scripts'));import collect
import tempfile
cache=Path(tempfile.mkdtemp(prefix='atlas-reanalysis-'))
def get(mid):
    f=cache/f'{mid}.json'
    if not f.exists():
        with urllib.request.urlopen(f'https://atlas.ripe.net/api/v2/measurements/{mid}/results/?format=json',timeout=60) as r:data=json.load(r)
        f.write_text(json.dumps(data))
    return mid,json.loads(f.read_text())
for f in (p/'web/data/runs').glob('*.json'):
    c=json.loads(f.read_text())
    if c.get('classification_version') == 2: continue
    mids=[t['measurement'] for t in c['targets']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: raw=dict(pool.map(get,mids))
    probes=collect.load_probes(collect.Net('AtlasCanada read-only verification'),[r['prb_id'] for rows in raw.values() for r in rows],c['settings']['address_family'])
    lookup={(r['prb_id'],mid):r for mid,rows in raw.items() for r in rows}
    for t in c['traces']:
        r=lookup[(t['probe'],t['measurement'])];t['src_country']=probes.get(t['probe'],{}).get('country')
        t['eligible']=t['reached'] and t['src_country']=='CA' and t['dst_country']=='CA'
        t['left_country']=t['eligible'] and bool(t['foreign_countries'])
        t['unanswered_hops']=sum(not any(x.get('from') for x in h.get('result',[]) if isinstance(x,dict)) for h in r.get('result',[]))
    c['classification_version']=2;c['summary']=collect.summarize(c['traces'],'CA')
    c['classification_note']='Reassessed original measurement results with verified probe country metadata; capture time and located hops preserved.'
    collect.write_json(f,c)
    ix=p/'web/data/index.json';idx=json.loads(ix.read_text())
    for e in idx['captures']:
        if e['id']==c['id']:e.update(eligible=c['summary']['eligible'],left_country=c['summary']['left_country'])
    collect.write_json(ix,idx)
    collect.write_json(p/'web/data/status.json',{'last_success':c['at'],'traces':len(c['traces']),'targets':len(c['targets'])})
    print(c['id'],{k:c['summary'][k] for k in ['traces','eligible','left_country','with_unanswered_hops']})
