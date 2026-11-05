import pytest

from woym.contracts import BisectReport, BisectStep, Env, RunResult

VENDOR = Env(node="22", lang="en_US.UTF-8", os="alpine", bizclock="main", tz="UTC")
CUSTOMER = Env(node="20", lang="en_IN.UTF-8", os="bookworm", bizclock="v2.3.1", tz="Asia/Kolkata")
EXPECTED = "cutoff=Fri 17:30 payout=Fri"  # same for every env (sandbox contract)


def run(env: Env, passed: bool, where: str = "vendor-sandbox") -> RunResult:
    return RunResult(
        env=env,
        passed=passed,
        expected=EXPECTED,
        actual="cutoff=Fri 17:30 payout=Fri" if passed else "cutoff=Mon 17:00 payout=Mon",
        exit_code=0 if passed else 1,
        duration_s=1.23,
        log_tail="",
        where=where,
    )


def report() -> BisectReport:
    steps = [
        BisectStep(k, getattr(VENDOR, k), getattr(CUSTOMER, k),
                   run(VENDOR.with_value(k, getattr(CUSTOMER, k)), k != "tz"))
        for k in VENDOR.diff(CUSTOMER)
    ]
    return BisectReport(baseline=run(VENDOR, True), target=run(CUSTOMER, False), steps=steps, culprits=["tz"])


@pytest.fixture
def bisect_report() -> BisectReport:
    return report()
