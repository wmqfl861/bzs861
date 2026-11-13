import json

import pytest

from bzs.mcp_auth import StaticTokenVerifier, add_client, load_clients, revoke_client


async def test_tokens_are_per_client_digest_only_and_revocable(tmp_path):
    path = tmp_path / "mcp_clients.json"
    laptop = add_client("laptop", path)
    server = add_client("build-server", path)
    assert laptop != server and laptop.startswith("bzsmcp_")
    raw = path.read_text(encoding="utf-8")
    assert laptop not in raw and server not in raw and len(json.loads(raw)["clients"]) == 2
    verifier = StaticTokenVerifier(path)
    assert (await verifier.verify_token(laptop)).client_id == "laptop"
    assert await verifier.verify_token("bzsmcp_guess") is None
    with pytest.raises(ValueError):
        add_client("laptop", path)
    with pytest.raises(ValueError):
        add_client("bad name!", path)
    assert revoke_client("laptop", path) and not revoke_client("laptop", path)
    # The running verifier must observe revocation without reconstructing the server.
    assert await verifier.verify_token(laptop) is None
    assert (await verifier.verify_token(server)).client_id == "build-server"
    new_client = add_client("new-client", path)
    assert (await verifier.verify_token(new_client)).client_id == "new-client"
    path.write_text("{invalid json", encoding="utf-8")
    assert await verifier.verify_token(server) is None
    path.unlink()
    assert await verifier.verify_token(server) is None


@pytest.mark.parametrize("module", ["bzs.mcp_server", "bzs.cli"])
def test_http_mcp_cannot_disable_client_auth(module, monkeypatch, capsys):
    import importlib
    impl = importlib.import_module(module)
    args = ["--transport", "streamable-http", "--insecure-no-auth"]
    if module == "bzs.cli":
        args.insert(0, "mcp")
    with pytest.raises(SystemExit) as exc:
        impl.main(args)
    assert exc.value.code == 2
    assert "requires a client token" in capsys.readouterr().err


async def test_malformed_client_entries_fail_closed(tmp_path):
    import hashlib, json as _json
    from bzs.mcp_auth import add_client
    path = tmp_path / "mcp_clients.json"
    good = add_client("laptop", path)
    digest = hashlib.sha256(b"x").hexdigest()
    for broken in (
        {"name": "a", "sha256": digest, "scopes": "search"},           # string scope
        {"name": "a", "sha256": digest, "disabled": "yes"},            # non-bool disabled
        {"name": 7, "sha256": digest},                                 # non-str name
        {"name": "a", "sha256": "ZZ" + digest[2:]},                    # non-hex digest
        "not-an-object",
    ):
        raw = _json.loads(path.read_text(encoding="utf-8"))
        raw["clients"].append(broken)
        path.write_text(_json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError):
            load_clients(path)
        # The verifier fails closed for the whole file, not just the entry.
        assert await StaticTokenVerifier(path).verify_token(good) is None
    path.write_text(_json.dumps({"clients": _json.loads(path.read_text(encoding="utf-8"))["clients"][:1]}), encoding="utf-8")
    assert (await StaticTokenVerifier(path).verify_token(good)).client_id == "laptop"


async def test_concurrent_adds_never_lose_tokens(tmp_path):
    import threading
    from bzs.mcp_auth import add_client
    path = tmp_path / "mcp_clients.json"
    names = [f"client-{i:02d}" for i in range(24)]
    results = {}
    errors = []

    def worker(name):
        try:
            results[name] = add_client(name, path)
        except Exception as exc:  # noqa: BLE001 - collect and assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    stored = {c["name"] for c in load_clients(path)}
    assert stored == set(names)  # no silently dropped write
    for name, token in results.items():
        assert (await StaticTokenVerifier(path).verify_token(token)).client_id == name



@pytest.mark.parametrize("raw", [
    '{"clients":[],"clients":[]}',
    '{"clients":[{"name":"one","name":"two","sha256":"' + "a" * 64 + '"}]}',
    '{"clients":[{"name":"one","sha256":"' + "a" * 64 + '","sha256":"' + "b" * 64 + '"}]}',
])
def test_client_registry_rejects_duplicate_json_keys(tmp_path, raw):
    path = tmp_path / "mcp_clients.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        load_clients(path)
