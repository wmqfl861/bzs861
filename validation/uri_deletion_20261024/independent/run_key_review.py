#!/usr/bin/env python3
"""Independent URI-key vectors; raw URL identities and literal reserved bytes."""
import hashlib,json,subprocess,sys,time
from pathlib import Path
E=Path(__file__).resolve().parent
D=Path(sys.argv[1]).resolve()
out=D/'key-review';out.mkdir(exist_ok=False)
vectors=[]
base='https://keys.example'
unreserved='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
for value in range(256):
    for form in ['%02x','%02X']:
        escape='%'+(form%value);normalized=chr(value) if chr(value) in unreserved else '%'+('%02X'%value)
        vectors.append({'name':f'byte_{value}_{form}', 'url':base+'/pre'+escape+'post?q='+escape,
                        'expected':base+'/pre'+normalized+'post?q='+normalized})
for name,url,key in [
 ('host_default_fragment','https://KEYS.EXAMPLE:00443/%61#frag',base+'/a'),
 ('http_default','http://KEYS.EXAMPLE:00080/%41','http://keys.example/A'),
 ('https_nondefault','https://KEYS.EXAMPLE:00080/%41','https://keys.example:80/A'),
 ('empty_path',base,base+'/'),('empty_query',base+'?',base+'/'),
 ('root_empty_query',base+'/?',base+'/'),('root_fragment',base+'#frag',base+'/'),
 ('query_only',base+'?x=%7e',base+'/?x=~'),
 ('dot_literal',base+'/a/../b',base+'/a/../b'),
 ('dot_encoded',base+'/a/%2e%2E/b',base+'/a/../b'),
 ('encoded_unicode',base+'/%e4%b8%ad',base+'/%E4%B8%AD'),
 ('query_order',base+'/a?z=%41&a=2',base+'/a?z=A&a=2'),
 ('query_duplicates',base+'/a?x=1&x=2',base+'/a?x=1&x=2'),
 ('malformed_escape',base+'/a%G0',base+'/a%G0'),
 ('malformed_short',base+'/a%1',base+'/a%1'),
 ('malformed_no_partial','https://KEYS.EXAMPLE:00443/%61%XZ','https://KEYS.EXAMPLE:00443/%61%XZ'),
 ('invalid_authority','https://bad..example/%61','https://bad..example/%61'),
 ('relative','not/a/url','not/a/url'),('different_scheme','ftp://keys.example/a','ftp://keys.example/a'),
 ('uppercase_scheme_legacy','HTTPS://KEYS.EXAMPLE/%61','HTTPS://KEYS.EXAMPLE/%61'),
 ('invalid_port','https://keys.example:0/%61','https://keys.example:0/%61'),
 ('private_metadata','http://127.0.0.1:80/%61','http://127.0.0.1/a')]:
    vectors.append({'name':name,'url':url,'expected':key})
binary=D/'key-probe';stdin=('\n'.join(v['url'] for v in vectors)+'\n').encode()
t=time.monotonic();p=subprocess.run([str(binary)],input=stdin,capture_output=True)
(out/'stdout.jsonl').write_bytes(p.stdout);(out/'stderr.log').write_bytes(p.stderr)
(out/'vectors.json').write_text(json.dumps(vectors,indent=2)+'\n')
received=[json.loads(line) for line in p.stdout.splitlines()]
errors=[]
for i,(expected,actual) in enumerate(zip(vectors,received)):
    if actual['input']!=expected['url'] or actual['key']!=expected['expected'] or not actual['idempotent']:
        errors.append({'index':i,'expected':expected,'actual':actual})
result={'returncode':p.returncode,'vectors':len(vectors),'outputs':len(received),'errors':errors,
        'elapsed_seconds':time.monotonic()-t,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'probe_sha256':hashlib.sha256((E/'key_probe.cpp').read_bytes()).hexdigest(),
        'status':'passed' if p.returncode==0 and len(received)==len(vectors) and not errors else 'failed'}
(out/'review-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));sys.exit(result['status']!='passed')
