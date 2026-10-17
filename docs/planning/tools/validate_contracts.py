"""Executable design contracts only. No network calls, model invocation, C++ runtime, or authorization.
Requires Python 3.10+ and jsonschema with Draft 2020-12 support.
The C++ implementation must independently pass these fixtures before release.
"""
from __future__ import annotations
import copy,json,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,10,16,12,0,0,tzinfo=timezone.utc) # fixed fixture clock, not wall clock
class ContractError(ValueError): pass

def timestamp(v):
    try:
        x=datetime.fromisoformat(v.replace('Z','+00:00'))
        if x.tzinfo is None: raise ValueError('timezone required')
        return x
    except (ValueError,AttributeError) as exc: raise ContractError('invalid timestamp') from exc

def guarded_shape(obj):
    if len(json.dumps(obj,ensure_ascii=False).encode())>65536: raise ContractError('request too large')
    if not isinstance(obj,dict): raise ContractError('object required')
    query=obj.get('query')
    if isinstance(query,dict):
        stack=[(query,1)];n=0
        while stack:
            node,depth=stack.pop();n+=1
            if depth>16 or n>512: raise ContractError('AST complexity exceeded')
            if not isinstance(node,dict): continue
            children=node.get('children',[])
            if isinstance(children,list):stack.extend((x,depth+1) for x in children)
            if 'child' in node:stack.append((node['child'],depth+1))

def normalize_models(obj):
    out=copy.deepcopy(obj);profiles=out['profiles'];default=out['default_text_profile'];roles=out['roles']
    if default is not None:
        if default not in profiles:raise ContractError('default profile missing')
        if 'text' not in profiles[default]['capabilities']:raise ContractError('default lacks text')
    for p in profiles.values():
        u=urlsplit(p['base_url'])
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ContractError('invalid model endpoint')
        # Trusted deployment egress rules, TLS policy and credentials are production checks, not inferred here.
    req={'planner':{'text'},'extractor':{'text'},'verifier':{'text'},'writer':{'text'},'embedder':{'embeddings'},'reranker':{'reranking','text'},'vision':{'image_input'},'asr':{'asr'}}
    out['effective_roles']={}
    for name,needed in req.items():
        text_role=name in ('planner','extractor','verifier','writer')
        v=roles.get(name,{} if text_role else None)
        if v is None:out['effective_roles'][name]=None;continue
        profile_id=v.get('profile',default if text_role else None)
        if profile_id is None:
            if v:raise ContractError('role overrides require a resolvable profile')
            out['effective_roles'][name]=None;continue
        if profile_id not in profiles:raise ContractError('role profile missing')
        p=profiles[profile_id]
        if not set(p['capabilities'])&needed:raise ContractError('role capability mismatch')
        limits={'timeout_ms':90000,'max_concurrency':1,'max_output_tokens':4096,'retry_limit':0}
        limits.update(p.get('limits',{}));ov=v.get('overrides',{})
        for k in limits:
            if k in ov:limits[k]=ov[k]
        reasoning=ov.get('reasoning',p.get('reasoning',{'mode':'provider_default'}))
        if reasoning.get('mode')=='explicit' and not reasoning.get('provider_parameters'):raise ContractError('explicit reasoning parameters missing')
        out['effective_roles'][name]={'profile':profile_id,'limits':limits,'reasoning':reasoning}
    for fid in out.get('text_fallback_profiles',[]):
        if fid not in profiles or 'text' not in profiles[fid]['capabilities']:raise ContractError('invalid text fallback')
    return out

def semantic(key,obj,now=NOW):
    if key=='model-config':return normalize_models(obj)
    if key=='source-manifest':
        if urlsplit(obj['endpoint']).scheme not in ('http','https'):raise ContractError('unsupported source scheme')
        if obj['enabled']:
            r=obj['review'];checked=timestamp(r['checked_at']);expires=timestamp(r['expires_at'])
            if checked>now or expires<=now or expires<=checked:raise ContractError('review time invalid or expired')
            if urlsplit(r['terms_url']).scheme not in ('http','https'):raise ContractError('invalid terms scheme')
            limits=obj['limits']
            if isinstance(limits.get('request_quota'),int) and not limits.get('quota_window_seconds'):raise ContractError('quota window missing')
        return obj
    if key=='search-request':
        fields={'host':{'eq','neq','in'},'path':{'eq','neq','contains','not_contains','in'},'content_type':{'eq','neq','in'},'language':{'eq','neq','in'},'published_at':{'before','after','eq'},'updated_at':{'before','after','eq'},'title':{'eq','neq','contains','not_contains','in'},'body':{'eq','neq','contains','not_contains','in'},'code':{'eq','neq','contains','not_contains','in'},'identifier':{'eq','neq','in'}}
        ranges={}
        for f in obj.get('hard_filters',[]):
            if f['operator'] not in fields[f['field']]:raise ContractError('field/operator mismatch')
            if f['field'] in ('published_at','updated_at'):
                v=f['value'];dt=timestamp(v+'T00:00:00Z' if len(v)==10 else v)
                if f['operator'] in ('before','after'):ranges.setdefault(f['field'],{})[f['operator']]=dt
        for x in ranges.values():
            if 'before' in x and 'after' in x and x['after']>=x['before']:raise ContractError('empty date range')
        if obj.get('network_mode')=='offline' and obj['scope']['corpus']!='local':raise ContractError('offline requires local corpus')
        return obj
    raise ContractError('unknown contract')

def validate(key,obj,schema_root=None,with_semantics=True):
    guarded_shape(obj)
    root=Path(schema_root) if schema_root else ROOT/'schemas'
    schema=json.loads((root/(key+'.schema.json')).read_text())
    Draft202012Validator.check_schema(schema)
    errors=sorted(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(obj),key=lambda e:str(e.path))
    if errors:raise ContractError(errors[0].message[:250])
    return semantic(key,obj) if with_semantics else obj

def run_tests():
    records=[]
    for case in json.loads((ROOT/'validation/contract_cases.json').read_text()):
        try:
            normalized=validate(case['schema'],case['input']);valid=True;error=None
            if 'effective_assertion' in case:
                role=case['effective_assertion']['role'];field=case['effective_assertion']['field'];want=case['effective_assertion']['value']
                got=normalized['effective_roles'][role]
                got=got['limits'][field] if got is not None else None
                if got!=want:raise ContractError('normalization mismatch')
        except (ContractError,RecursionError) as exc:valid=False;error=str(exc)
        records.append(dict(case=case['name'],expected_valid=case['valid'],actual_valid=valid,passed=valid==case['valid'],error=error))
    return records
if __name__=='__main__':
    results=run_tests();print(json.dumps({'scope':'design_contract_fixtures_only','count':len(results),'passed':sum(x['passed'] for x in results),'results':results},ensure_ascii=False,indent=2))
    sys.exit(0 if all(x['passed'] for x in results) else 1)
