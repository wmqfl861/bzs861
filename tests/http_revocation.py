"""Real loopback HTTP/CLI authorization and restart tests, without external calls."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest import mock

import integration


class RevocationHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bzs-revoke-http-")
        self.root = Path(self.temp.name)
        self.server = integration.RunningServer(self.root, no_worker=True)
        self.server.stop()
        self.second_bob = secrets.token_hex(32)
        self.add_token(self.second_bob, "bob")
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.temp.cleanup()

    def add_token(self, token, subject):
        config = json.loads(self.server.auth.read_text())
        config["tokens"].append({"sha256": hashlib.sha256(token.encode()).hexdigest(),
                                 "subject": subject, "admin": False})
        self.server.auth.write_text(json.dumps(config), encoding="utf-8")
        self.server.auth.chmod(0o600)

    def ok(self, operation, params=None, **options):
        code, response = self.server.call(operation, params, **options)
        self.assertEqual(code, 200, response)
        return response["result"]

    def denied(self, operation, params=None, *, token=None, error="E_PRINCIPAL_REVOKED"):
        code, response = self.server.call(operation, params, token=token)
        self.assertEqual(code, 403, response)
        self.assertEqual(response["errors"][0]["code"], error)
        self.assertNotIn("result", response)

    def rows(self, query):
        path = self.root / "state" / "metadata.sqlite"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute(query).fetchall()

    def test_administrator_only_strict_contract_and_unrelated_owner(self):
        self.denied("principal.revoke", {"subject": "alice"}, token=self.server.bob, error="E_FORBIDDEN")
        self.denied("principal.revoke", {"subject": "alice"}, error="E_FORBIDDEN")
        for params in ({"subject": "bob", "admin": True}, {"subject": "bob", "token": "x"},
                       {"subject": ["bob"]}, {"subject": "bad subject"}):
            code, response = self.server.call("principal.revoke", params)
            self.assertEqual(code, 400, response)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM principal_revocations"), [(0,)])
        receipt = self.ok("principal.revoke", {"subject": "bob"})
        self.assertEqual(receipt["auth_epoch"], 1)
        self.assertFalse(receipt["already_revoked"])
        for token in (self.server.bob, self.second_bob):
            self.denied("capabilities", token=token)
            self.denied("principal.revoke", {"subject": "alice"}, token=token)
        self.assertEqual(self.ok("stats")["documents"], 0)

    def test_existing_capabilities_and_pending_work_stay_denied_after_restart_and_token_rotation(self):
        bob = self.server.bob
        document = self.ok("document.ingest", {"url": "https://fixture.example/bob", "content": "private revocation marker",
                                               "content_type": "text/plain"}, token=bob)
        sid = document["snapshot_id"]
        evidence = self.ok("evidence.create", {"snapshot_id": sid, "start_byte": 0, "end_byte": 7}, token=bob)
        session = self.ok("search", {"query": "private"}, token=bob)
        job = self.ok("job.create", {"fixture_id": "developer", "idempotency_key": "revocation-job"}, token=bob)
        self.ok("job.control", {"job_id": job["job_id"], "action": "pause"}, token=bob)
        receipt = self.ok("principal.revoke", {"subject": "bob"})
        operations = [("document.read", {"snapshot_id": sid}),
                      ("document.range", {"snapshot_id": sid, "start_byte": 0}),
                      ("evidence.read", {"evidence_id": evidence["evidence_id"]}),
                      ("search.page", {"session_id": session["session_id"]}),
                      ("search.once", {"query": "private"}),
                      ("session.events", {"session_id": session["session_id"]}),
                      ("job.get", {"job_id": job["job_id"]}),
                      ("job.control", {"job_id": job["job_id"], "action": "resume"}),
                      ("fixtures.list", {}), ("stats", {})]
        for operation, params in operations:
            self.denied(operation, params, token=bob)
        self.assertEqual(self.rows("SELECT state FROM jobs WHERE owner='bob'"), [("cancelled",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM snapshots"), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM evidence"), [(1,)])
        command = self.server.cli("revoke-principal", "bob")
        self.assertEqual(command.returncode, 0, command.stderr)
        repeated = json.loads(command.stdout)["result"]
        self.assertTrue(repeated["already_revoked"])
        self.assertEqual(repeated["revocation_id"], receipt["revocation_id"])
        self.assertEqual(repeated["auth_epoch"], 1)
        self.server.stop()
        rotated = secrets.token_hex(32)
        self.add_token(rotated, "bob")
        self.server.start()
        for token in (bob, self.second_bob, rotated):
            self.denied("document.read", {"snapshot_id": sid}, token=token)
            self.denied("stats", token=token)
        again = self.ok("principal.revoke", {"subject": "bob"})
        self.assertEqual(again, repeated)
        self.assertEqual(self.ok("stats")["documents"], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM principal_revocations"), [(1,)])


class RowsConnectionLifecycle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="bzs-revoke-rows-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "state").mkdir()
        with closing(sqlite3.connect(self.root / "state" / "metadata.sqlite")) as connection:
            connection.execute("CREATE TABLE fixture_rows (value INTEGER NOT NULL)")
            connection.execute("INSERT INTO fixture_rows VALUES (7)")
            connection.commit()
        self.real_connect = sqlite3.connect
        self.connections = []

    def retain_connection(self, *args, **kwargs):
        connection = self.real_connect(*args, **kwargs)
        # Keep the real connection alive so garbage collection cannot hide a leak.
        self.connections.append(connection)
        self.addCleanup(connection.close)
        return connection

    def assert_connection_closed(self):
        self.assertEqual(len(self.connections), 1)
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            self.connections[0].execute("SELECT 1")

    def test_rows_closes_connection_after_success(self):
        with mock.patch.object(sqlite3, "connect", side_effect=self.retain_connection):
            self.assertEqual(RevocationHTTP.rows(self, "SELECT value FROM fixture_rows"), [(7,)])
        self.assert_connection_closed()

    def test_rows_closes_connection_after_query_exception(self):
        with mock.patch.object(sqlite3, "connect", side_effect=self.retain_connection):
            with self.assertRaises(sqlite3.OperationalError):
                RevocationHTTP.rows(self, "SELECT value FROM missing_table")
        self.assert_connection_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--fixtures", required=True)
    integration.CONFIG, rest = parser.parse_known_args()
    unittest.main(argv=[__file__, *rest], verbosity=2)
