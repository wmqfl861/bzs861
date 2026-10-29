"""2026-10-28 review regressions and bounded runtime contracts."""
import hashlib
import json
import pytest
from bzs.mcp_auth import StaticTokenVerifier, load_clients


@pytest.mark.parametrize('raw_kind', ['duplicate-enabled', 'duplicate-name', 'duplicate-digest', 'newline-name'])
async def test_ambiguous_auth_registry_fails_closed(tmp_path, raw_kind):
    token = 'test-only-token'
    digest = hashlib.sha256(token.encode()).hexdigest()
    valid = {'name':'one','sha256':digest}
    if raw_kind == 'duplicate-enabled':
        raw = '{"clients":['+json.dumps(valid)[:-1]+',"disabled":true,"disabled":false}]}'
    elif raw_kind == 'duplicate-name':
        raw = json.dumps({'clients':[valid, {'name':'one','sha256':'b'*64}]})
    elif raw_kind == 'duplicate-digest':
        raw = json.dumps({'clients':[valid, {'name':'two','sha256':digest}]})
    else:
        raw = json.dumps({'clients':[{**valid,'name':'one\n'}]})
    p = tmp_path/'clients.json';p.write_text(raw,encoding='utf-8')
    assert await StaticTokenVerifier(p).verify_token(token) is None


@pytest.mark.parametrize('data', [[],None,{}, {'clients':{}}, {'clients':None},
                                  {'clients':[], 'extra':True}])
def test_invalid_top_level_is_a_validation_error(tmp_path, data):
    p=tmp_path/'clients.json';p.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_clients(p)


def test_nonregular_and_oversize_files_fail_before_parsing(tmp_path, monkeypatch):
    from bzs import mcp_auth as auth
    import os
    p=tmp_path/'clients.json'
    if hasattr(os, 'mkfifo'):
        os.mkfifo(p)
        with pytest.raises(ValueError): load_clients(p)
        p.unlink()
    p.write_bytes(b' '*65)
    monkeypatch.setattr(auth, 'MAX_CLIENTS_BYTES', 64)
    with pytest.raises(ValueError, match='byte limit'): load_clients(p)


async def test_registry_limits_preserve_existing_token_and_do_not_write(tmp_path, monkeypatch):
    from bzs import mcp_auth as auth
    p=tmp_path/'clients.json';token=auth.add_client('one',p)
    before=p.read_bytes()
    monkeypatch.setattr(auth, 'MAX_CLIENTS', 1)
    with pytest.raises(ValueError, match='entry limit'): auth.add_client('two',p)
    assert p.read_bytes() == before
    assert (await auth.StaticTokenVerifier(p).verify_token(token)).client_id == 'one'
    monkeypatch.setattr(auth, 'MAX_CLIENTS', 2)
    monkeypatch.setattr(auth, 'MAX_CLIENTS_BYTES', len(before)+1)
    with pytest.raises(ValueError, match='byte limit'): auth.add_client('two',p)
    assert p.read_bytes() == before


async def test_concurrent_revoke_and_add_preserve_authorization(tmp_path):
    import asyncio
    from bzs import mcp_auth as auth
    p=tmp_path/'clients.json';old=[auth.add_client(f'old{i}',p) for i in range(8)]
    async def add(i): return await asyncio.to_thread(auth.add_client,f'new{i}',p)
    new = await asyncio.gather(*(add(i) for i in range(8)),
                               *(asyncio.to_thread(auth.revoke_client,f'old{i}',p) for i in range(8)))
    verifier=auth.StaticTokenVerifier(p)
    for token in old: assert await verifier.verify_token(token) is None
    for i,token in enumerate(new[:8]): assert (await verifier.verify_token(token)).client_id == f'new{i}'
    assert len(auth.load_clients(p)) == 8


def test_cli_auth_check_is_readonly_and_does_not_expose_digests(tmp_path,monkeypatch,capsys):
    from bzs import cli, mcp_auth as auth
    monkeypatch.setenv('BZS_HOME',str(tmp_path))
    token=auth.add_client('client')
    before=auth.clients_path().read_bytes()
    monkeypatch.setattr(cli,'start',lambda *_: (_ for _ in ()).throw(AssertionError('must not start')))
    assert cli.main(['mcp-token','check']) == 0
    out=capsys.readouterr()
    assert json.loads(out.out) == {'valid':True,'clients':1,'active':1}
    assert token not in out.out and hashlib.sha256(token.encode()).hexdigest() not in out.out
    assert auth.clients_path().read_bytes() == before
    auth.clients_path().write_text('{"clients":false}')
    assert cli.main(['mcp-token','check']) == 2
    assert 'E_CLI' in capsys.readouterr().err
