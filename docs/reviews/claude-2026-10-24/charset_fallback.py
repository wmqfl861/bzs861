"""Repro P2-4: a page declared UTF-8 with one stray byte falls through a strict guess chain.

Run from the repository root with the worker dependencies installed:
    PYTHONPATH=python python docs/reviews/claude-2026-10-24/charset_fallback.py
"""
from bzs.worker.fetch import decode_body

SAMPLES = {
    "UTF-8 Chinese + stray 0xE9": "<p>这是一篇关于搜索引擎的文章，介绍了倒排索引和排序。</p>".encode()
                                  + b"caf\xe9 " + "更多内容在这里。".encode(),
    "UTF-8 English + stray 0x92": b"<p>It\x92s a long article about databases and indexing.</p>",
}

for name, body in SAMPLES.items():
    text, codec = decode_body(body, "utf-8")
    print(f"{name:28s} -> codec={codec:12s} text[:40]={text[:40]!r}")
