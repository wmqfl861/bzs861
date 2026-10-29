from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from bzs.artifact_output import write_new_directory


def test_concurrent_publishers_produce_exactly_one_complete_output(tmp_path):
    out = tmp_path / "shared-result"
    gate = Barrier(2)

    def publish(label):
        gate.wait(timeout=5)
        try:
            write_new_directory(out, {
                "report.json": ('{"owner":"' + label + '"}\n').encode("utf-8"),
                "report.md": ("owner " + label + "\n").encode("utf-8"),
            }, label="test")
            return ("published", label)
        except ValueError as exc:
            assert "E_OUTPUT_" in str(exc)
            return ("rejected", label)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("first", "second")))

    assert sum(state == "published" for state, _ in results) == 1
    assert sum(state == "rejected" for state, _ in results) == 1
    assert (out / "report.json").is_file() and (out / "report.md").is_file()
    import json
    value = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert value["owner"] in {"first", "second"}
    assert (out / "report.md").read_text(encoding="utf-8") == "owner " + value["owner"] + "\n"
    assert not list(tmp_path.glob(".shared-result.stage-*"))


def test_artifact_output_rejects_nonregular_publication_lock(tmp_path):
    out = tmp_path / "result"
    (tmp_path / ".result.publish.sqlite").mkdir()
    with pytest.raises(ValueError, match="E_OUTPUT_PATH"):
        write_new_directory(out, {"report.json": b"{}"}, label="test")
    assert not out.exists()



def test_parent_directory_flush_failure_reports_published_state(tmp_path, monkeypatch):
    import bzs.artifact_output as output

    out = tmp_path / "published-result"
    real = output._fsync_directory
    calls = 0

    def fail_parent(path):
        nonlocal calls
        calls += 1
        if path == tmp_path:
            raise OSError("synthetic directory flush failure")
        return real(path)

    monkeypatch.setattr(output, "_fsync_directory", fail_parent)
    with pytest.raises(OSError, match="E_OUTPUT_DURABILITY_UNCONFIRMED"):
        write_new_directory(out, {"report.json": b"{}", "report.md": b"complete"}, label="test")
    assert (out / "report.json").read_bytes() == b"{}"
    assert (out / "report.md").read_bytes() == b"complete"
    assert not list(tmp_path.glob(".published-result.stage-*"))



def test_external_destination_race_never_replaces_existing_directory(tmp_path, monkeypatch):
    import bzs.artifact_output as output

    out = tmp_path / "raced-output"
    real_rename = output._rename_noreplace

    def race_create_target(stage, destination):
        destination.mkdir()
        (destination / "owner.txt").write_text("external-owner", encoding="utf-8")
        return real_rename(stage, destination)

    monkeypatch.setattr(output, "_rename_noreplace", race_create_target)
    with pytest.raises(FileExistsError):
        write_new_directory(out, {"report.json": b"ours"}, label="test")
    assert (out / "owner.txt").read_text(encoding="utf-8") == "external-owner"
    assert not (out / "report.json").exists()
    assert not list(tmp_path.glob(".raced-output.stage-*"))
