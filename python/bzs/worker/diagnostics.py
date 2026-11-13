"""Value-free, bounded RPC failure diagnostics for the private stderr log.

Never format the exception, its chain, source lines, request objects or locals.
Frame names are restricted to known code namespaces, not filesystem paths.
"""
from collections import deque
import logging
import re

_FRAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,95}\Z")
_TYPES = frozenset({"KeyError", "RuntimeError", "TypeError", "ValueError", "UnicodeError",
                    "UnicodeEncodeError", "UnicodeDecodeError", "OSError", "TimeoutError",
                    "AssertionError", "AttributeError", "IndexError", "MemoryError"})
_METHODS = frozenset({"health", "search.multi", "fetch.extract", "fetch.cancel", "map.fetch",
                      "fetch.profile", "fetch.extract_direct", "fetch.cancel_direct"})


def log_failure(logger: logging.Logger, method: str | None, exc: BaseException, *, result: bool = False) -> None:
    """Retain up to 12 locations from at most 64 frames, without any values."""
    frames = deque(maxlen=12)
    tb = exc.__traceback__
    for _ in range(64):
        if tb is None:
            break
        module = tb.tb_frame.f_globals.get("__name__")
        if not isinstance(module, str) or not module.startswith(("bzs.", "json", "aiohttp.")) or not _FRAME.fullmatch(module):
            module = "external"
        frames.append(f"{module}:{tb.tb_lineno}")
        tb = tb.tb_next
    category = type(exc).__name__
    if category not in _TYPES:
        category = "Exception"
    label = "rpc result is not serializable JSON" if result else "rpc internal failure"
    logger.error("%s (method=%s exception=%s frames=%s)", label,
                 method if method in _METHODS else "unknown", category, ">".join(frames) or "none")
