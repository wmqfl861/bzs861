"""Repro P2-3: one non-conforming User-agent group makes the whole robots.txt deny_all.

Run from the repository root with the worker dependencies installed:
    PYTHONPATH=python python docs/reviews/claude-2026-10-24/robots_tokens.py
"""
from yarl import URL

from bzs.worker.robots import parse_robots, rule_decision

CASES = {
    "360Spider group": b"User-agent: 360Spider\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n",
    "Sogou web spider group": b"User-agent: Sogou web spider\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "Googlebot-Image/1.0": b"User-agent: Googlebot-Image/1.0\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "control (Baiduspider)": b"User-agent: Baiduspider\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n",
}

for name, body in CASES.items():
    record = parse_robots(body)
    decision = rule_decision(record, URL("https://example.com/article/1"))
    print(f"{name:24s} state={record['state']:9s} diag={record['diagnostics']} "
          f"-> /article/1 allowed={decision['allowed']}")
