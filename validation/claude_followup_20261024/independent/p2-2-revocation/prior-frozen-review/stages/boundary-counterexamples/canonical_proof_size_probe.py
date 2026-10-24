"""Independent cross-language proof-bound probe; no socket or compiled object.

The production Core's Json::canonical serializes each scalar via json-c PLAIN.
Key ordering does not change these ASCII, integer-only records' encoded length.
Use the actual system json-c serializer to measure the consumer's size, and the
imported production Worker helper to decide whether optional proof is emitted.
"""
import ctypes
import ctypes.util
import json

from bzs.worker.redirect_cache import encoded_size, make_chain


LIBRARY = ctypes.util.find_library("json-c")
LIB = ctypes.CDLL(LIBRARY)
LIB.json_tokener_parse.argtypes = [ctypes.c_char_p]
LIB.json_tokener_parse.restype = ctypes.c_void_p
LIB.json_object_to_json_string_ext.argtypes = [ctypes.c_void_p, ctypes.c_int]
LIB.json_object_to_json_string_ext.restype = ctypes.c_char_p
LIB.json_object_put.argtypes = [ctypes.c_void_p]
LIB.json_object_put.restype = ctypes.c_int


def consumer_size(value):
    record = LIB.json_tokener_parse(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    assert record
    try:
        return len(LIB.json_object_to_json_string_ext(record, 0))
    finally:
        LIB.json_object_put(record)


def proof_input(length):
    original = "https://route-review.example/start"
    prefix = "https://route-review.example/"
    final = prefix + "x" * (length - len(prefix))
    profile = "a" * 64
    policy = {"version": "bzs-http-freshness-1", "url": original, "request_profile": profile,
              "cache_control": "max-age=60", "vary": "", "date": "", "age": "",
              "expires": "", "pragma": "", "received_at_ms": 1700000000000,
              "response_delay_ms": 0, "invalid": False}
    hops = [{"url": original, "target_url": final, "http_status": 301,
             "content_bytes": 0, "http_freshness": policy}]
    expected = {"version": "bzs-http-redirect-chain-1", "request_url": original,
                "final_url": final, "hops": hops}
    return original, final, hops, profile, expected


def main():
    rows = []
    for length in (100, 3816, 3817, 3818, 3824):
        original, final, hops, profile, value = proof_input(length)
        emitted = make_chain(original, final, hops, profile)
        core_size = consumer_size(value)
        rows.append({"final_uri_bytes": length, "consumer_canonical_bytes": core_size,
                     "worker_encoded_size": encoded_size(value), "proof_emitted": emitted is not None,
                     "expected_proof_emitted": core_size <= 8192,
                     "passed": (emitted is not None) == (core_size <= 8192)})
    print(json.dumps({"scope": "Worker helper vs actual json-c scalar encoding; no HTTP",
                      "json_c_library": LIBRARY, "passed": sum(row["passed"] for row in rows),
                      "total": len(rows), "cases": rows}, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
