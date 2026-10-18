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
