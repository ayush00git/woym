"""scripts/demo.py against fakes: a fake "room" advances a temp case store the
way @triage would when the scripted humans post. No Switch, no Docker."""

import importlib.util
import sys
import threading

import pytest

from woym import config
from woym.case.state import CaseStore
from woym.contracts import Step

_spec = importlib.util.spec_from_file_location("woym_demo", config.ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(_spec)
sys.modules["woym_demo"] = demo
_spec.loader.exec_module(demo)

S = Step


class FakeRoom:
    """Reacts to scripted posts by walking the case through steps, like the agents."""

    def __init__(self, store, fail_at=None, stall_at=None, install_consent=True, delay=0.01):
        self.store, self.fail_at, self.stall_at = store, fail_at, stall_at
        self.install_consent, self.delay = install_consent, delay
        self.posts = []
        self.case = None

    def _walk(self, steps):
        def go():
            for st in steps:
                if st == self.stall_at:
                    return
                case = self.store.get(self.case.id)
                case.step = S.FAILED if st == self.fail_at else st
                case.history.append(f"x:{st.value}")
                self.store.save(case)
                if case.step == S.FAILED:
                    return
                threading.Event().wait(self.delay)
        threading.Thread(target=go, daemon=True).start()

    def __call__(self, who, text, thread):
        self.posts.append((who, text, thread))
        if thread is None:  # the report
            self.case = self.store.open_case("room", "root-1", who)
            self._walk([S.SANDBOX_RUN, S.NOT_REPRODUCED, S.ENV_REQUESTED, S.CONSENT_PENDING])
            return "root-1"
        if (who, len(self.posts)) == ("meera", 2):  # share consent
            self._walk([S.ENV_RECEIVED, S.BISECTING, S.CULPRIT_FOUND, S.CONFIRM_REQUESTED, S.CONFIRMED_BOTH])
        elif who == "dev":
            tail = [S.FIX_REQUESTED, S.FIX_PUSHED, S.MATRIX_GREEN, S.VERIFY_REQUESTED]
            self._walk(tail if self.install_consent else tail + [S.FIXED_ON_CUSTOMER])
        else:  # install consent
            self._walk([S.FIXED_ON_CUSTOMER])
        return f"post-{len(self.posts)}"


def run(tmp_path, until="fixed", install_consent=True, **room_kw):
    store = CaseStore(tmp_path / "cases.json")
    room = FakeRoom(store, install_consent=install_consent, **room_kw)
    lines = []
    say = demo.Timeline(out=lines.append)
    code = 0
    try:
        demo.run_case(room, store, until, install_consent, "fixture", say, poll_s=0.01, open_timeout_s=2)
    except demo.DemoFailed as e:
        code = e.code
        lines.append(str(e))
    return code, room, "\n".join(lines)


def test_full_flow(tmp_path):
    code, room, out = run(tmp_path)
    assert code == 0
    assert room.posts == [
        ("meera", demo.REPORT, None), ("meera", "yes", "root-1"),
        ("dev", "@triage patch it", "root-1"), ("meera", "yes", "root-1"),
    ]
    assert "*** FIXED ON MEERA'S MACHINE ***" in out
    assert out.count("[SCRIPTED]") == 4
    assert "case 1 step -> consent" in out and "hop x:bisect" in out


def test_until_confirmed_stops_before_patch(tmp_path):
    code, room, out = run(tmp_path, until="confirmed")
    assert code == 0 and [p[0] for p in room.posts] == ["meera", "meera"]
    assert "*** CONFIRMED ON BOTH MACHINES ***" in out and "FIXED" not in out


def test_without_install_consent(tmp_path):
    code, room, out = run(tmp_path, install_consent=False)
    assert code == 0 and [p[0] for p in room.posts] == ["meera", "meera", "dev"]


def test_case_failed_exits_2(tmp_path):
    code, room, out = run(tmp_path, fail_at=S.BISECTING)
    assert code == 2 and "FAILED" in out and len(room.posts) == 2


def test_stall_times_out_with_exit_1(tmp_path, monkeypatch):
    real = demo.beats
    monkeypatch.setattr(demo, "beats", lambda *a: [demo.Beat(b.wait_for, 0.3, b.who, b.text, b.label) for b in real(*a)])
    code, room, out = run(tmp_path, stall_at=S.CONSENT_PENDING)
    assert code == 1 and "waiting for consent (at step env_req)" in out
    assert len(room.posts) == 1  # never posted the yes


def test_no_case_opened_times_out(tmp_path):
    store = CaseStore(tmp_path / "cases.json")
    with pytest.raises(demo.DemoFailed) as e:
        demo.run_case(lambda *a: "root", store, "fixed", True, "fixture", demo.Timeline(out=lambda s: None),
                      poll_s=0.01, open_timeout_s=0.2)
    assert e.value.code == 1 and "no case opened" in str(e.value)


def test_old_cases_are_ignored(tmp_path):
    store = CaseStore(tmp_path / "cases.json")
    old = store.open_case("room", "old", "meera")
    old.step = S.FIXED_ON_CUSTOMER
    store.save(old)
    room = FakeRoom(store)
    lines = []
    demo.run_case(room, store, "fixed", True, "fixture", demo.Timeline(out=lines.append), poll_s=0.01)
    assert "case 2 opened" in "\n".join(lines)


def test_reached_order():
    assert demo.reached("env", "consent")  # consent may be passed between polls
    assert not demo.reached("env_req", "consent")
    assert demo.reached("fixed", "verify_req")


def _agent(code):
    return [sys.executable, "-c", code]


def test_agent_procs_start_wait_stop(tmp_path):
    ready = "import time; print('x connected: rooms=[r]', flush=True); time.sleep(60)"
    procs = demo.AgentProcs({"a": _agent(ready), "b": _agent(ready)}, logs=tmp_path / "logs", run_dir=tmp_path / "run")
    procs.start(timeout_s=10, settle_s=0)
    assert (tmp_path / "run" / "a.pid").exists()
    procs.stop()
    assert all(p.poll() is not None for p in procs.procs.values())
    assert not (tmp_path / "run" / "a.pid").exists()


def test_agent_that_dies_is_reported(tmp_path):
    procs = demo.AgentProcs({"bad": _agent("import sys; print('boom: no creds'); sys.exit(4)")},
                            logs=tmp_path / "logs", run_dir=tmp_path / "run")
    with pytest.raises(demo.DemoFailed) as e:
        procs.start(timeout_s=10)
    assert e.value.code == 3 and "boom: no creds" in str(e.value)
    procs.stop()


def test_agent_that_never_connects_times_out(tmp_path):
    procs = demo.AgentProcs({"slow": _agent("import time; time.sleep(60)")}, logs=tmp_path / "l", run_dir=tmp_path / "r")
    with pytest.raises(demo.DemoFailed, match="not connected"):
        procs.start(timeout_s=0.5)
    procs.stop()
