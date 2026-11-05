"""INTEGRATION: the four agents over A's REAL WsSwitchClient, against a FAKE
Switch server (tests/agents/fakeswitch.py), with REAL engines:
DockerSandbox, DockerCustomerMachine on a test-only container, and
FixtureFixer on a temp bare remote. Full flow through FIXED.

Skipped without Docker. Uses its own container name, so it never touches the
demo's acme-laptop. Takes ~1-2 min (real 2 s send pacing, ~13 docker runs)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from fakeswitch import FakeSwitch
from woym.agents.acme_env import AcmeEnvAgent
from woym.agents.fixer_agent import FixerAgent
from woym.agents.runner_agent import RunnerAgent
from woym.agents.triage import TriageAgent
from woym.case import tags
from woym.case.state import CaseStore
from woym.contracts import Step
from woym.fixer import FixtureFixer
from woym.sandbox import remote
from woym.sandbox.customer import DockerCustomerMachine
from woym.sandbox.runner import DockerSandbox
from woym.switch.client import WsSwitchClient

ROOM = "room-int"
# Unique per run: two sessions running this test at once must not share (and
# re-install) one container. Never B's test container, never the demo acme-laptop.
CONTAINER = f"woym-test-acme-laptop-agents-{os.getpid()}"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="needs a docker daemon")


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    return remote.init_remote(tmp_path_factory.mktemp("remote") / "bizclock.git")


@pytest.fixture(scope="module")
def machine(bare):
    m = DockerCustomerMachine(container=CONTAINER, remote_path=bare)
    m.up("v2.3.1")
    yield m
    m.down()


async def wait_step(store: CaseStore, step: Step, timeout: float, fs: FakeSwitch) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        cases = store.all()
        if cases and cases[-1].step == step:
            return
        if cases and cases[-1].step == Step.FAILED:
            break
        await asyncio.sleep(0.2)
    got = store.all()[-1].step if store.all() else None
    transcript = "\n---\n".join(f"{m.sender_name}: {m.body}" for m in fs.log)
    raise AssertionError(f"waited for {step.value}, case is at {got}\n{transcript}")


async def test_full_flow_real_client_real_engines(tmp_path, bare, machine):
    machine.install("v2.3.1")  # start from the customer's released version, whatever ran before
    assert machine.probe().bizclock == "v2.3.1"   # consent card must show the tag, not a sha
    names = ["triage", "runner", "fixer", "acme-env"]
    store = CaseStore(tmp_path / "cases.json")
    async with FakeSwitch(ROOM, names, ["meera", "dev"], ping_s=1.0) as fs:
        clients = {n: WsSwitchClient(n, f"id-{n}", "test-key", ROOM, base_url=fs.base_url, transport=fs.transport())
                   for n in names}
        agents = [
            TriageAgent(clients["triage"], store=store),
            RunnerAgent(clients["runner"], DockerSandbox(remote_path=bare)),
            FixerAgent(clients["fixer"], FixtureFixer(remote=bare, work_root=tmp_path / "work"),
                       issues_path=tmp_path / "ISSUES.md"),
            AcmeEnvAgent(clients["acme-env"], machine),
        ]
        tasks = [asyncio.create_task(a.client.run(a.handle)) for a in agents]
        try:
            for c in clients.values():
                await asyncio.wait_for(c._connected.wait(), 15)
            await asyncio.sleep(0.5)

            root = fs.human("meera", "@triage payouts placed Friday 17:10 land on Monday for us. Works in your CI?")
            await wait_step(store, Step.CONSENT_PENDING, 90, fs)
            fs.human("meera", "yes", root)
            await wait_step(store, Step.CONFIRMED_BOTH, 240, fs)
            fs.human("dev", "@triage patch it", root)
            await wait_step(store, Step.VERIFY_REQUESTED, 240, fs)
            fs.human("meera", "yes", root)
            await wait_step(store, Step.FIXED_ON_CUSTOMER, 120, fs)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for c in clients.values():
                await c.aclose()

    case = store.all()[-1]
    if os.environ.get("WOYM_TRANSCRIPT"):  # e.g. WOYM_TRANSCRIPT=var/logs/int_transcript.txt
        with open(os.environ["WOYM_TRANSCRIPT"], "w") as f:
            f.write("\n\n".join(f"[{m.id}] {m.sender_name} -> {m.targets} (thread {m.thread_id})\n{m.body}"
                                for m in fs.log))
    by = lambda n: [m for m in fs.log if m.sender_name == n]  # noqa: E731
    text = "\n".join(m.body for m in fs.log)
    # the cards, from the right authors
    assert any("NOT REPRODUCED" in m.body for m in by("triage"))
    assert any("can I share this?" in m.body and "`v2.3.1`" in m.body for m in by("acme-env"))
    assert any("← CULPRIT" in m.body and "**tz**" in m.body for m in by("runner"))
    assert any("CONFIRMED ON BOTH MACHINES" in m.body for m in by("triage"))
    assert any("Matrix green" in m.body for m in by("runner"))
    assert any("FIXED ON MEERA'S MACHINE" in m.body for m in by("acme-env"))
    assert "FIXTURE" in text   # fixture mode is labelled
    # real engines did real work
    assert case.bisect.culprits == ["tz"]
    assert case.fix.sha == remote.ls_remote("fix/case-1", bare)
    assert machine.probe().bizclock == case.fix.sha
    # protocol: everything stayed in the case thread, hops via send_targeted, no step twice
    assert all(m.thread_id == root for m in fs.log if m.id != root)
    targeted = [(n, a["target_names"], tags.parse(a["body"])[1].value) for n, op, a in fs.ops
                if op == "send_targeted_message"]
    assert len(targeted) == len(set((n, s) for n, _, s in targeted))
    assert ("triage", ["acme-env"], "env_req") in targeted
    assert ("acme-env", ["triage"], "env") in targeted
    assert len(case.history) == len(set(case.history)) and case.hops <= 20
    assert all(fs.pongs.get(f"id-{n}", 0) > 0 for n in names)
