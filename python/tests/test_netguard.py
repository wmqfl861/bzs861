import pytest

from bzs.worker.netguard import GuardedResolver, NetPolicy, PolicyError

POLICY = NetPolicy()

BLOCKED = [
    "http://127.0.0.1/", "http://localhost/", "http://foo.localhost/", "http://10.0.0.1/", "http://172.16.5.4/",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data", "http://100.64.0.1/", "http://0.0.0.0/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[64:ff9b::7f00:1]/", "http://[fe80::1]/", "http://[fc00::1]/",
    "http://2130706433/", "http://0x7f.1/", "http://127.1/", "http://017700000001/", "file:///etc/passwd",
    "gopher://example.com/", "ftp://example.com/", "http://user:pw@example.com/", "http://printer.local/",
    "http://metadata.google.internal/", "http:///nohost",
    "http://１２７.０.０.１/", "http://127。0。0。1/", "http://１６９．２５４．１６９．２５４/",
    "http://ｌｏｃａｌｈｏｓｔ/", "http://2130706433。/", "http://127.0.0.1./",
    "http://@example.com/", "http://user:pw@例え.テスト/", "http://example.com:0/",
    "http://[64:ff9b:1:7f00:0:abcd:808:808]/", "http://[4000::1]/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocked_urls(url):
    with pytest.raises(PolicyError):
        POLICY.check_url(url)


@pytest.mark.parametrize("url", ["https://example.com/", "http://93.184.216.34:8080/x", "https://[2606:4700::1111]/",
                                 "https://zh.wikipedia.org/wiki/%E6%90%9C%E7%B4%A2", "https://1e100.net/"])
def test_allowed_urls(url):
    POLICY.check_url(url)


def test_test_allowance_is_narrow():
    p = NetPolicy(["127.0.0.1/32"], test_mode=True)
    p.check_url("http://127.0.0.1:9/")
    with pytest.raises(PolicyError):
        p.check_url("http://127.0.0.2/")
    with pytest.raises(PolicyError):
        p.check_url("http://10.0.0.1/")


@pytest.mark.parametrize("cidrs", [["127.0.0.1/32"], ["10.0.0.0/8"], ["198.18.0.0/15"]])
def test_production_cannot_allow_private_ranges(cidrs):
    with pytest.raises(ValueError):
        NetPolicy(cidrs)


def test_fixture_allowances_are_exact_loopback_only():
    for cidr in ("127.0.0.0/8", "10.0.0.0/8", "198.18.0.0/15"):
        with pytest.raises(ValueError):
            NetPolicy([cidr], test_mode=True)


async def test_fixed_dns_is_only_an_explicit_loopback_fixture():
    with pytest.raises(ValueError):
        GuardedResolver(NetPolicy(), {"bzs-e2e.test": "127.0.0.1"})
    for hosts in ({"production.example": "127.0.0.1"}, {"bzs-e2e.test": "10.0.0.1"}):
        with pytest.raises(ValueError):
            GuardedResolver(NetPolicy(test_mode=True), hosts)


def test_international_host_is_canonical_before_policy_and_connection():
    checked = POLICY.prepare_url("https://例え.テスト/资料")
    assert checked.raw_host == "xn--r8jz45g.xn--zckzah"
    assert POLICY.check_url(str(checked)) == ("https", checked.raw_host, 443)


class FakeInner:
    def __init__(self, addrs):
        self.addrs = addrs

    async def resolve(self, host, port, family):
        return [{"hostname": host, "host": a, "port": port, "family": 2, "proto": 0, "flags": 0} for a in self.addrs]

    async def close(self):
        pass


async def test_resolver_drops_private_addresses():
    r = GuardedResolver(POLICY)
    r._inner = FakeInner(["127.0.0.1", "93.184.216.34", "10.1.1.1"])
    assert [x["host"] for x in await r.resolve("mixed.example", 80)] == ["93.184.216.34"]
    r._inner = FakeInner(["169.254.169.254"])
    with pytest.raises(PolicyError):
        await r.resolve("rebind.example", 80)
