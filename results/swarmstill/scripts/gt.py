import json,glob,sys,re,collections,hashlib
base=sys.argv[1]
rows=[]
def norm_ans(fam,ans):
    if ans is None: return None
    if fam=='code_analysis':
        return str(ans).strip().strip('"').lower()
    try:
        v=json.loads(ans) if isinstance(ans,str) else ans
    except Exception:
        return str(ans)
    if isinstance(v,list):
        out=[]
        for x in v:
            if isinstance(x,dict): out.append(tuple(sorted(x.values()))[0] if x else None)
            else: out.append(x)
        return tuple(sorted(map(str,out)))
    return str(v)
for f in sorted(glob.glob(base+'/trajectory-*.json')):
    d=json.load(open(f))
    for ep in d['episodes']:
        for a in ('agent-1','agent-2'):
            acts=ep['agents'][a]['task_actions']
            fin=[t for t in acts if t['tool']=='save_final_answer' and isinstance(t['arguments'],dict) and (not isinstance(t['result'],dict) or t['result'].get('success',True))]
            ans=fin[-1]['arguments'].get('answer') if fin else None
            sig=None
            for t in acts:
                if t['tool'] in ('read_code','read_source'):
                    sig=hashlib.md5(json.dumps(t['result'],sort_keys=True).encode()).hexdigest()[:8]; break
            if sig is None and ep['task_family']=='data_search':
                # use the final answer key column + first id? fallback: none
                sig=None
            rows.append(dict(t=f[-7:-5],e=ep['episode'],a=a,fam=ep['task_family'],sig=sig,ans=norm_ans(ep['task_family'],ans),v=ep['agents'][a]['verdict']['verdict']))
groups=collections.defaultdict(list)
for r in rows:
    if r['sig']: groups[r['sig']].append(r)
truth={}
for s,g in groups.items():
    c=collections.Counter(r['ans'] for r in g)
    top,n=c.most_common(1)[0]
    truth[s]=(top,n,len(g))
for r in rows:
    if r['sig'] and truth[r['sig']][2]>=2:
        top,n,N=truth[r['sig']]
        r['correct']= (r['ans']==top) if n>N/2 else None
    else: r['correct']=None
json.dump(rows,open(sys.argv[2],'w'))
fam=collections.defaultdict(collections.Counter)
for r in rows: fam[r['fam']][r['correct']]+=1
print({k:dict(v) for k,v in fam.items()})
print('groups',len(groups), 'sizes', collections.Counter(len(g) for g in groups.values()))
