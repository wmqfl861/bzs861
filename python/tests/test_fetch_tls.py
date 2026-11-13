"""CL-20: explicit TLS policy on the fetch connector.

Fetcher.start must not rely on aiohttp's implicit TLS defaults. It builds an
explicit ssl.SSLContext (certificate and hostname verification on, TLS 1.2
floor) and passes it to TCPConnector. Tests trust a generated test CA only
through such an explicitly created context; certificate and hostname
verification stay enabled everywhere, and constructing a Fetcher with a
context that disables either check is refused.
"""
import datetime
import ssl

import aiohttp
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bzs.worker.fetch import Fetcher
from bzs.worker.netguard import NetPolicy

CERT_HOST = "bzs-tls.test"        # the name on the server certificate
WRONG_HOST = "bzs-mismatch.test"  # a different name mapped to the same loopback server


async def test_fetcher_start_passes_explicit_tls_context_to_tcp_connector(monkeypatch):
    captured = {}
    real_connector = aiohttp.TCPConnector

    def capturing_connector(**kwargs):
        captured.update(kwargs)
        return real_connector(**kwargs)

    monkeypatch.setattr(aiohttp, "TCPConnector", capturing_connector)
    fetcher = Fetcher(NetPolicy())
    await fetcher.start()
    try:
        context = captured.get("ssl")
        # Assert on the object Fetcher.start itself passes to the connector,
        # not on aiohttp's implicit default (which leaves "ssl" absent here).
        assert isinstance(context, ssl.SSLContext), \
            "TCPConnector must receive an explicit ssl.SSLContext from Fetcher.start"
        assert context is fetcher.ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    finally:
        await fetcher.close()


@pytest.mark.parametrize("disable", ["verify_mode", "check_hostname"])
def test_verification_disabling_contexts_are_rejected(disable):
    context = ssl.create_default_context()
    context.check_hostname = False
    if disable == "verify_mode":
        context.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="certificate and hostname verification"):
        Fetcher(NetPolicy(), ssl_context=context)


def _identity(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(subject: x509.Name, issuer: x509.Name, public_key, signing_key,
                 *, ca: bool, san: str | None = None) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(public_key).serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=2)))
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return builder.sign(signing_key, hashes.SHA256())


@pytest.fixture(scope="session")
def pki(tmp_path_factory):
    """Two unrelated offline CAs; each directly signs one server certificate for CERT_HOST."""
    out = {}
    for name in ("trusted", "untrusted"):
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_cert = _certificate(_identity(f"bzs861 test CA ({name})"), _identity(f"bzs861 test CA ({name})"),
                               ca_key.public_key(), ca_key, ca=True)
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf_cert = _certificate(_identity(CERT_HOST), ca_cert.subject, leaf_key.public_key(), ca_key,
                                 ca=False, san=CERT_HOST)
        base = tmp_path_factory.mktemp(name)
        (base / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        (base / "server.pem").write_bytes(
            leaf_key.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption())
            + leaf_cert.public_bytes(serialization.Encoding.PEM))
        out[name] = {"ca_pem": str(base / "ca.pem"), "server_pem": str(base / "server.pem")}
    return out


def _server_ssl_context(entry: dict) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(entry["server_pem"])
    return context


async def _start_tls_page(server_context: ssl.SSLContext, body: str) -> tuple[web.AppRunner, int]:
    async def page(request):
        return web.Response(text=body, content_type="text/plain")

    app = web.Application()
    app.router.add_get("/page.txt", page)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]


def _tls_fetcher(ca_pem: str) -> Fetcher:
    client_context = ssl.create_default_context()
    client_context.load_verify_locations(cafile=ca_pem)
    return Fetcher(NetPolicy(["127.0.0.1/32"], test_mode=True),
                   test_hosts={CERT_HOST: "127.0.0.1", WRONG_HOST: "127.0.0.1"},
                   ssl_context=client_context)


async def test_loopback_tls_fetch_succeeds_with_explicit_test_ca(pki):
    runner, port = await _start_tls_page(_server_ssl_context(pki["trusted"]), "tls loopback body")
    try:
        fetcher = _tls_fetcher(pki["trusted"]["ca_pem"])
        await fetcher.start()
        try:
            result = await fetcher.fetch(f"https://{CERT_HOST}:{port}/page.txt")
            assert result.status == "ok"
            assert result.body == b"tls loopback body"
            # The injected context kept both checks on for the whole fetch.
            assert fetcher.ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert fetcher.ssl_context.check_hostname is True
        finally:
            await fetcher.close()
    finally:
        await runner.cleanup()


async def test_loopback_tls_rejects_certificate_from_unknown_ca(pki):
    runner, port = await _start_tls_page(_server_ssl_context(pki["untrusted"]), "must not arrive")
    try:
        fetcher = _tls_fetcher(pki["trusted"]["ca_pem"])
        await fetcher.start()
        try:
            result = await fetcher.fetch(f"https://{CERT_HOST}:{port}/page.txt")
            assert result.status == "network_error"
            assert result.body == b"" and result.http_status is None
            assert "certificate" in result.error.lower()
            assert result.external_requests == 1  # the failed attempt is still accounted
        finally:
            await fetcher.close()
    finally:
        await runner.cleanup()


async def test_loopback_tls_rejects_wrong_hostname_with_trusted_ca(pki):
    # The chain is trusted, but the certificate is issued for CERT_HOST; the
    # fetch goes through a different name mapped to the same loopback server.
    runner, port = await _start_tls_page(_server_ssl_context(pki["trusted"]), "must not arrive")
    try:
        fetcher = _tls_fetcher(pki["trusted"]["ca_pem"])
        await fetcher.start()
        try:
            result = await fetcher.fetch(f"https://{WRONG_HOST}:{port}/page.txt")
            assert result.status == "network_error"
            assert result.body == b"" and result.http_status is None
            assert "hostname mismatch" in result.error.lower()
        finally:
            await fetcher.close()
    finally:
        await runner.cleanup()
