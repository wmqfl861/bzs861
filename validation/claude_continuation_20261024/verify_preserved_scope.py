"""Read-only comparison of reserved project inputs against the batch baseline."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "59081e14fdda765844031e13f874e588334f1a36"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def raw_value(text, key):
    start = re.search(r'^  "' + re.escape(key) + r'": ', text, re.MULTILINE).end()
    _, end = json.JSONDecoder().raw_decode(text, start)
    return text[start:end].encode("utf-8")


unchanged = Counter()
tree = git("ls-tree", "-r", "-z", BASE, "validation", "config", "docs/planning", ".github/workflows")
for row in tree.split(b"\0"):
    if not row:
        continue
    info, name = row.split(b"\t", 1)
    mode, kind, oid = info.split()
    path = ROOT / name.decode()
    assert kind == b"blob" and mode in (b"100644", b"100755")
    assert path.is_file() and not path.is_symlink(), str(path)
    data = path.read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    assert actual == oid.decode(), name.decode()
    unchanged[name.decode().split("/")[0]] += 1

status_path = "docs/implementation_status.json"
old_text = git("show", BASE + ":" + status_path).decode()
new_text = (ROOT / status_path).read_text()
old, new = json.loads(old_text), json.loads(new_text)
assert new["features"] == old["features"]
assert raw_value(old_text, "features") == raw_value(new_text, "features")
history = [key for key in old if key.startswith("historical_") and isinstance(old[key], dict)]
for key in history:
    assert raw_value(old_text, key) == raw_value(new_text, key), key
archive = new["historical_verification_claude_followup_20261024"]
for key, value in archive.items():
    if key in old:
        assert old[key] == value, key
assert new["status_counts"] == old["status_counts"] == {"partial": 37, "not_started": 46, "accepted": 0}
assert len(new["features"]) == 83 and new["planned_acceptance_items"] == 161
assert new["version"] == "0.1.10" and new["integration_inputs"]["schema"] == 13
assert new["integration_inputs"]["index_format"] == "bzs-ascii-cjk/2+codepoint-grams/1"
sources = json.loads((ROOT / "config/sources.json").read_bytes())["sources"]
sites = json.loads((ROOT / "config/sites.json").read_bytes())["sites"]
assert len(sources) == 17 and not any(item.get("enabled") for item in sources) and not sites
assert (ROOT / "CMakeLists.txt").read_bytes() == git("show", BASE + ":CMakeLists.txt")
print(json.dumps({
    "base": BASE, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "unchanged_baseline_files": dict(unchanged), "features": 83,
    "features_raw_sha256": hashlib.sha256(raw_value(new_text, "features")).hexdigest(),
    "preserved_historical_objects": history, "status_counts": new["status_counts"],
    "acceptance_items": 161, "registered_sources": 17, "enabled_sources": 0,
    "registered_sites": 0, "version": "0.1.10", "schema": 13,
    "all_checks_passed": True
}, ensure_ascii=False, indent=2))
