"""Live-run lock (docs/contracts.md §8): nothing writes credentials.json while a demo runs."""

import os
import subprocess
import sys

import pytest

from woym.switch import creds


def test_no_lock_allows_save(tmp_path, monkeypatch):
    monkeypatch.setattr(creds, "LIVE_LOCK", tmp_path / "live.lock")
    creds.save({"room": {"id": "r"}}, tmp_path / "c.json")
    assert creds.load(tmp_path / "c.json")["room"]["id"] == "r"


@pytest.mark.parametrize("fmt", ["{pid}\n", '{{"pid": {pid}, "started": "x"}}'])
def test_live_lock_refuses_save(tmp_path, monkeypatch, fmt):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock = tmp_path / "live.lock"
        lock.write_text(fmt.format(pid=other.pid))
        monkeypatch.setattr(creds, "LIVE_LOCK", lock)
        with pytest.raises(creds.LiveRunLocked, match=str(other.pid)):
            creds.save({}, tmp_path / "c.json")
        assert not (tmp_path / "c.json").exists()
    finally:
        other.kill()


def test_stale_lock_is_removed(tmp_path, monkeypatch):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lock = tmp_path / "live.lock"
    lock.write_text(str(p.pid))
    monkeypatch.setattr(creds, "LIVE_LOCK", lock)
    creds.save({}, tmp_path / "c.json")
    assert not lock.exists()


def test_own_pid_may_save(tmp_path, monkeypatch):
    lock = tmp_path / "live.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(creds, "LIVE_LOCK", lock)
    creds.save({}, tmp_path / "c.json")
