"""P1-B: real loopback Core/Worker/MCP, with deterministic held RPCs; no internet."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest

import integration
from integration import RunningServer, HeldWorker

CONFIG = None


class Admission(unittest.TestCase):
    def stack(self, *extra, delayed=False):
        temporary = tempfile.TemporaryDirectory(prefix="bzs-admission-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fixture = integration.DirectReadReceipts()
        def response(params, profile):
            if delayed:
                time.sleep(2)
            return fixture.worker_response(params, profile)
        worker = HeldWorker(response=response)
        self.addCleanup(worker.close)
        token = root / "worker.token"
        token.write_text(secrets.token_hex(32)); token.chmod(0o600)
        sources = root / "sources.json"
        sources.write_text('{"version":1,"sources":[]}')
        server = RunningServer(root, no_worker=True, extra_args=[
            "--threads", "2", "--worker", f"http://127.0.0.1:{worker.port}",
            "--worker-token-file", str(token), "--sources", str(sources), *extra])
        self.addCleanup(server.stop)
        self.addCleanup(worker.release.set)
        return root, server, worker

    def test_local_stats_not_starved_by_held_external_reads(self):
        _, server, worker = self.stack()
        with ThreadPoolExecutor(max_workers=18) as pool:
            reads = [pool.submit(server.call, "web.read", {"url": f"https://admission.example/held/{i}",
                                                          "timeout_ms": 10000}) for i in range(2)]
            try:
                self.assertTrue(worker.wait_calls(2))
                start = time.monotonic()
                stats = list(pool.map(lambda _: server.call("stats", timeout=3), range(16)))
                elapsed = time.monotonic() - start
                self.assertEqual([code for code, _ in stats], [200] * 16)
                self.assertLess(elapsed, 1.0)
                self.assertFalse(worker.release.is_set())
                print(json.dumps({"held_reads": 2, "stats": 16, "stats_batch_seconds": elapsed}), flush=True)
            finally:
                worker.release.set()
            self.assertEqual([r.result()[0] for r in reads], [200, 200])

    def test_16_delayed_reads_and_16_stats_with_bounded_client_retry(self):
        from bzs.core_client import CoreClient
        _, server, worker = self.stack(delayed=True)
        worker.release.set()
        async def run():
            client = CoreClient(f"http://127.0.0.1:{server.port}", server.alice)
            latencies = []
            async def stats():
                start = time.monotonic()
                result = await client.call("stats")
                latencies.append(time.monotonic()-start)
                return result
            reads = [client.call("web.read", {"url": f"https://admission.example/delayed/{i}",
                                              "freshness": "refresh", "timeout_ms": 10000}) for i in range(16)]
            out = await asyncio.gather(*reads, *(stats() for _ in range(16)))
            return out, sorted(latencies)[15]
        started = time.monotonic()
        result, p95 = asyncio.run(run())
        self.assertEqual(len(result), 32)
        self.assertTrue(all("snapshot_id" in item for item in result[:16]))
        self.assertLess(p95, 1.0)
        self.assertEqual(worker.calls, 16, "admission retry cannot duplicate a physical fetch")
        self.assertEqual(worker.peak, 2)
        print(json.dumps({"reads": 16, "stats": 16, "failures": 0, "stats_p95_seconds": p95,
                          "batch_seconds": time.monotonic()-started}), flush=True)

    def test_full_or_expired_queue_is_retriable_without_late_side_effects(self):
        _, server, worker = self.stack("--request-threads", "1", "--request-queue", "1",
                                       "--request-queue-timeout-ms", "100")
        with ThreadPoolExecutor(max_workers=4) as pool:
            held = pool.submit(server.call, "web.read", {"url": "https://admission.example/held", "timeout_ms": 10000})
            try:
                self.assertTrue(worker.wait_calls(1))
                requests = [pool.submit(server.call, "document.ingest", {
                    "url": f"https://admission.example/never/{i}", "content": "never committed", "content_type": "text/plain"}) for i in range(3)]
                result = [request.result(timeout=2) for request in requests]
                for code, body in result:
                    self.assertEqual(code, 503, body)
                    self.assertEqual(body["errors"][0]["code"], "E_REQUEST_BACKPRESSURE")
                    self.assertIsInstance(body["errors"][0]["retry_after_ms"], int)
                messages = [body["errors"][0]["message"] for _, body in result]
                self.assertTrue(any("expired" in msg for msg in messages), messages)
                self.assertTrue(any("full" in msg for msg in messages), messages)
                self.assertEqual(worker.calls, 1)
            finally:
                worker.release.set()
            self.assertEqual(held.result()[0], 200)
        self.assertEqual(server.call("stats")[1]["result"]["documents"], 1)

    def test_mcp_stdio_24_parallel_local_searches(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        root, server, worker = self.stack()
        worker.release.set()
        for i in range(24):
            code, body = server.call("document.ingest", {"url": f"https://admission.example/local/{i}",
                "content": ("offline concurrency search fixture "*1024), "content_type": "text/plain"})
            self.assertEqual(code, 200, body)
        async def run():
            env = dict(os.environ, PYTHONPATH=str(CONFIG.repo / "python"),
                       BZS_CORE_URL=f"http://127.0.0.1:{server.port}", SEARCH_API_TOKEN=server.alice)
            parameters = StdioServerParameters(command=CONFIG.python, args=["-m", "bzs.mcp_server"],
                                                env=env, cwd=str(CONFIG.repo / "python"))
            with (root / "mcp.log").open("w") as log:
                async with stdio_client(parameters, errlog=log) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        return await asyncio.gather(*(session.call_tool("search_local", {"query": "-absentfixture"}) for _ in range(24)))
        results = asyncio.run(run())
        self.assertEqual(len(results), 24)
        for result in results:
            self.assertFalse(result.is_error, result)
            self.assertGreater(result.structured_content["returned_count"], 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    CONFIG, remaining = parser.parse_known_args()
    CONFIG.fixtures = str(CONFIG.repo / "tests/fixtures")
    integration.CONFIG = CONFIG
    sys.path.insert(0, str(CONFIG.repo / "python"))
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
