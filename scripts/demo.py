"""Run a WOYM case end to end in the real Switch room (make demo).

  uv run python scripts/demo.py                  # full flow, through FIXED ON MEERA'S MACHINE
  uv run python scripts/demo.py --until confirmed  # M1: stop at CONFIRMED ON BOTH MACHINES
  options: --no-reset  --keep-agents  --fixer fixture|claude  --no-preflight

What it does:
1. preflight: Switch /health, Mattermost ping, var/switch/credentials.json,
   the acme-laptop container (run `make up setup customer-up` first);
2. scripts/reset.sh (unless --no-reset);
3. starts the four agents (logs in var/logs/<name>.log, pids in var/run/),
   waiting for each to log "connected: rooms=";
4. posts the SCRIPTED human lines with scripts/humans.py, each one only
   after @triage's case store (var/cases.json) reaches the right step:
     meera's report          -> case opens
     meera "yes" (share)     <- step consent
     dev "@triage patch it"  <- step confirmed
     meera "yes" (install)   <- step verify_req   (only if WOYM_INSTALL_CONSENT != 0)
     done                    <- step fixed
5. prints a timeline of every step and exits 0, or non-zero on failure:
   1 timeout, 2 case FAILED, 3 preflight/agent start failed.

The human lines are scripted, not typed by a person live; every one is
printed with a [SCRIPTED] label, and the README says so too.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from woym import config  # noqa: E402
from woym.case.state import CaseStore  # noqa: E402
from woym.contracts import Step  # noqa: E402

AGENTS = ["triage", "runner_agent", "fixer_agent", "acme_env"]
READY_MARK = "connected: rooms="
RUN_DIR = config.VAR / "run"

REPORT = (
    "@triage Payouts slip to Monday on our side. A Friday 17:10 order should settle Friday "
    "(cutoff 17:30) but bizclock says Monday. Repro: `node examples/payout.js` prints "
    "`cutoff=Mon 17:00 payout=Mon`. Your support says it works for you."
)

# Happy-path order of case steps, for "has the case reached X yet".
ORDER = [s.value for s in (
    Step.REPORTED, Step.SANDBOX_RUN, Step.NOT_REPRODUCED, Step.ENV_REQUESTED, Step.CONSENT_PENDING,
    Step.ENV_RECEIVED, Step.BISECTING, Step.CULPRIT_FOUND, Step.CONFIRM_REQUESTED, Step.CONFIRMED_BOTH,
    Step.FIX_REQUESTED, Step.FIX_PUSHED, Step.MATRIX_GREEN, Step.VERIFY_REQUESTED, Step.FIXED_ON_CUSTOMER,
)]  # fmt: skip


class DemoFailed(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------
# Pluggable edges (real by default, faked in tests/sandbox/test_sandbox_demo.py)
# --------------------------------------------------------------------------


class Poster(Protocol):
    def __call__(self, who: str, text: str, thread: str | None) -> str: ...


def humans_poster(who: str, text: str, thread: str | None) -> str:
    """scripts/humans.py (platform): prints the new post id."""
    cmd = ["uv", "run", "--quiet", "python", str(config.ROOT / "scripts" / "humans.py"), who, text]
    if thread:
        cmd += ["--thread", thread]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0 or not p.stdout.strip():
        raise DemoFailed(3, f"humans.py {who} failed (exit {p.returncode}): {p.stderr.strip()[-400:]}")
    return p.stdout.strip().splitlines()[-1]


@dataclass
class AgentProcs:
    """Starts agent processes, waits for their ready line, stops them."""

    commands: dict[str, list[str]]
    logs: Path = config.LOGS
    run_dir: Path = RUN_DIR
    env: dict[str, str] = field(default_factory=dict)
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)

    def start(self, timeout_s: float = 45, settle_s: float = 1.0) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for name, cmd in self.commands.items():
            log = open(self.logs / f"{name}.log", "w")
            self.procs[name] = subprocess.Popen(
                cmd, stdout=log, stderr=subprocess.STDOUT, cwd=config.ROOT,
                env={**os.environ, "PYTHONUNBUFFERED": "1", **self.env}, start_new_session=True,
            )  # fmt: skip
            (self.run_dir / f"{name}.pid").write_text(str(self.procs[name].pid))
        deadline = time.monotonic() + timeout_s
        waiting = set(self.commands)
        while waiting:
            for name in sorted(waiting):
                if self.procs[name].poll() is not None:
                    raise DemoFailed(3, f"agent {name} exited (code {self.procs[name].returncode}):\n"
                                        f"{tail(self.logs / f'{name}.log')}")
                if READY_MARK in (self.logs / f"{name}.log").read_text(errors="replace"):
                    waiting.discard(name)
            if waiting and time.monotonic() > deadline:
                raise DemoFailed(3, f"agents not connected after {timeout_s:.0f}s: {', '.join(sorted(waiting))}")
            time.sleep(0.2)
        time.sleep(settle_s)  # the room claim runs just after the first frame

    def stop(self) -> None:
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
        for name, p in self.procs.items():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            (self.run_dir / f"{name}.pid").unlink(missing_ok=True)


def tail(path: Path, n: int = 15) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except FileNotFoundError:
        return "(no log)"


# --------------------------------------------------------------------------
# The script
# --------------------------------------------------------------------------


@dataclass
class Beat:
    wait_for: str  # case step to reach first ("" = case just opened)
    timeout_s: float
    who: str | None = None  # scripted human line to post once reached
    text: str = ""
    label: str = ""


def beats(until: str, install_consent: bool, fixer: str) -> list[Beat]:
    fix_wait = 900 if fixer == "claude" else 120  # claude -p writes the fix; fixture applies a patch
    out = [
        Beat("consent", 120, config.CUSTOMER_HUMAN, "yes", "Meera agrees to share 5 values and run the repro"),
        Beat("confirmed", 180, label="CONFIRMED ON BOTH MACHINES"),
    ]
    if until == "confirmed":
        return out
    out.append(Beat("confirmed", 5, config.VENDOR_HUMAN, "@triage patch it", "Dev asks for a fix"))
    if install_consent:
        out.append(Beat("verify_req", fix_wait, config.CUSTOMER_HUMAN, "yes", "Meera agrees to install the fix"))
    out.append(Beat("fixed", fix_wait if not install_consent else 180, label="FIXED ON MEERA'S MACHINE"))
    return out


class Timeline:
    def __init__(self, out=print, clock: Callable[[], float] = time.monotonic):
        self.t0 = clock()
        self.clock = clock
        self.out = out
        self.rows: list[tuple[float, str]] = []

    def __call__(self, msg: str) -> None:
        t = self.clock() - self.t0
        self.rows.append((t, msg))
        self.out(f"[{t:6.1f}s] {msg}")


def reached(step: str, target: str) -> bool:
    if step == target:
        return True
    return step in ORDER and target in ORDER and ORDER.index(step) >= ORDER.index(target)


def run_case(post: Poster, store: CaseStore, until: str, install_consent: bool, fixer: str,
             say: Timeline, poll_s: float = 0.25, open_timeout_s: float = 60) -> int:
    known = {c.id for c in store.all()}
    say(f"[SCRIPTED] {config.CUSTOMER_HUMAN}: {REPORT}")
    root = post(config.CUSTOMER_HUMAN, REPORT, None)
    say(f"report posted (thread root {root})")

    case_id = None
    last_step = None
    seen: set[str] = set()

    def poll() -> str | None:
        nonlocal case_id, last_step
        if case_id is None:
            new = [c for c in store.all() if c.id not in known]
            if not new:
                return None
            case_id = max(c.id for c in new)
            say(f"case {case_id} opened by @triage")
        case = store.get(case_id)
        step = case.step.value
        for h in case.history:  # every hop, in order, as the agents fired them
            if h not in seen:
                seen.add(h)
                say(f"  hop {h}")
        if step != last_step:
            last_step = step
            say(f"case {case_id} step -> {step}")
        if step == Step.FAILED.value:
            raise DemoFailed(2, f"case {case_id} FAILED (history: {', '.join(case.history)})")
        return step

    def wait(target: str, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            step = poll()
            if step is not None and target and reached(step, target):
                return
            if step is not None and not target:
                return
            if time.monotonic() > deadline:
                where = f"at step {step}" if step else "no case opened"
                raise DemoFailed(1, f"timed out after {timeout_s:.0f}s waiting for {target or 'case'} ({where})")
            time.sleep(poll_s)

    wait("", open_timeout_s)
    for beat in beats(until, install_consent, fixer):
        wait(beat.wait_for, beat.timeout_s)
        if beat.who:
            say(f"[SCRIPTED] {beat.who}: {beat.text}   ({beat.label})")
            post(beat.who, beat.text, root)
        elif beat.label:
            say(f"*** {beat.label} ***")
    return case_id or 0


# --------------------------------------------------------------------------
# Preflight and main
# --------------------------------------------------------------------------


def preflight() -> list[str]:
    import httpx

    from woym.switch import creds

    problems = []
    for url in (f"{config.SWITCH_URL.rstrip('/')}/health", f"{config.MATTERMOST_URL.rstrip('/')}/api/v4/system/ping"):
        try:
            if httpx.get(url, timeout=5).status_code != 200:
                problems.append(f"{url} not healthy (run `make up`)")
        except httpx.HTTPError as e:
            problems.append(f"{url} unreachable: {type(e).__name__} (run `make up`)")
    try:
        data = creds.load()
        missing = [a for a in ("triage", "runner", "fixer", "acme-env") if a not in data.get("agents", {})]
        if missing or not data.get("room", {}).get("channel_id"):
            problems.append(f"credentials incomplete (agents missing: {missing}); run `make setup`")
    except FileNotFoundError as e:
        problems.append(str(e))
    p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", config.CUSTOMER_CONTAINER],
                       capture_output=True, text=True)
    if p.stdout.strip() != "true":
        problems.append(f"customer container {config.CUSTOMER_CONTAINER} not running (run `make customer-up`)")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--until", choices=["confirmed", "fixed"], default="fixed")
    ap.add_argument("--fixer", choices=["claude", "fixture"], default=config.FIXER_MODE)
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--keep-agents", action="store_true", help="leave agents running afterwards")
    a = ap.parse_args(argv)
    install_consent = os.environ.get("WOYM_INSTALL_CONSENT", "1") != "0"
    say = Timeline()

    say(f"WOYM demo: until={a.until} fixer={a.fixer} install_consent={int(install_consent)}")
    say("Human lines marked [SCRIPTED] are posted by scripts/humans.py, not typed live.")
    agents = AgentProcs(
        {n: ["uv", "run", "--quiet", "python", "-m", f"woym.agents.{n}"] for n in AGENTS},
        env={"FIXER": a.fixer, "WOYM_INSTALL_CONSENT": "1" if install_consent else "0"},
    )
    try:
        if not a.no_preflight:
            problems = preflight()
            if problems:
                raise DemoFailed(3, "preflight failed:\n  - " + "\n  - ".join(problems))
            say("preflight ok")
        if not a.no_reset:
            subprocess.run([str(config.ROOT / "scripts" / "reset.sh")], check=True, capture_output=True)
            say("reset ok (customer on v2.3.1, no fix branches, empty case store)")
        agents.start()
        say(f"agents connected: {', '.join(AGENTS)} (logs in {config.LOGS.relative_to(config.ROOT)}/)")
        case_id = run_case(humans_poster, CaseStore(), a.until, install_consent, a.fixer, say)
        say(f"DONE: case {case_id} reached {a.until} in {say.rows[-1][0]:.1f}s")
        return 0
    except DemoFailed as e:
        say(f"DEMO FAILED: {e}")
        for name in agents.procs:
            print(f"--- {name}.log (tail) ---\n{tail(config.LOGS / f'{name}.log')}")
        return e.code
    except subprocess.CalledProcessError as e:
        say(f"DEMO FAILED: {e.cmd[0]} exited {e.returncode}: {(e.stderr or b'').decode()[-400:]}")
        return 3
    finally:
        if a.keep_agents:
            say("agents left running (scripts/reset.sh stops them)")
        else:
            agents.stop()


if __name__ == "__main__":
    sys.exit(main())
