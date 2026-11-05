"""The customer machine: Meera's "laptop", the acme-laptop container.

Honest notes:
- acme-laptop is a container on the same host as the vendor's agents. It is
  NOT sandboxed from the vendor: anything with the Docker socket could exec
  into it. The separation in WOYM is Switch ownership. Only @acme-env, owned
  by Meera, uses this class, and only after her consent in chat.
- This class only ever runs the fixed, allow-listed commands below. Nothing
  from chat is passed to a shell. Override values and refs are regex-checked
  and passed as argv or `-e` values.

CLI (used by `make customer-up`):
    uv run python -m woym.sandbox.customer [--name kiran-laptop] up | down | probe | repro [--tz UTC] | install <ref>
    (--name picks the container and its profile; default acme-laptop, Meera's)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from woym import config
from woym.contracts import ENV_KEYS, Env, RunResult
from woym.sandbox import remote
from woym.sandbox.runner import EXPECTED, SandboxError, parse_output, tail

DOCKERFILE_DIR = config.ROOT / "infra" / "acme-laptop"
REF_MARKER = ".woym-ref"  # written by install(): the ref that was installed


@dataclass(frozen=True)
class Profile:
    """One customer's laptop, built from infra/acme-laptop/Dockerfile with build args."""

    user: str
    tz: str
    lang: str
    order_at: str  # the repro's Friday order time (local); the cutoff is 17:30
    image: str
    hostname: str

    @property
    def app_dir(self) -> str:
        return f"/home/{self.user}/acme-payouts"

    @property
    def lib_dir(self) -> str:
        return f"{self.app_dir}/node_modules/bizclock"


PROFILES: dict[str, Profile] = {
    # Meera (Acme, India): IST is +05:30. A 17:10 order is enough to show the bug.
    "acme-laptop": Profile("meera", "Asia/Kolkata", "en_IN.UTF-8", "17:10", "woym/acme-laptop:latest", "meera-laptop"),
    # Kiran (Acme Nepal, epilogue): NPT is +05:45, which the bug rounds to +06:00, a 15 min error.
    # A 17:10 order would still land before 17:30, so Kiran's order is 17:20 (seen as 17:35, so Monday).
    "kiran-laptop": Profile("kiran", "Asia/Kathmandu", "en_IN.UTF-8", "17:20", "woym/kiran-laptop:latest", "kiran-laptop"),
}
DEFAULT_PROFILE = PROFILES["acme-laptop"]


def profile_for(container: str) -> Profile:
    """The profile for a container name: exact match, else one whose key is in the
    name (e.g. test containers "woym-test-kiran-laptop"), else Meera's."""
    if container in PROFILES:
        return PROFILES[container]
    for key, prof in PROFILES.items():
        if key in container:
            return prof
    return DEFAULT_PROFILE


def probes(lib_dir: str) -> dict[str, list[str]]:
    """The only commands the customer's agent may run to read facts. Read-only, fixed argv."""
    return {
        "node": ["node", "-p", "process.versions.node.split('.')[0]"],
        "lang": ["printenv", "LANG"],
        "os": ["cat", "/etc/os-release"],
        # bizclock: the installed ref if install() wrote one, else "v" + package version
        "bizclock": ["sh", "-c", f"cat {lib_dir}/{REF_MARKER} 2>/dev/null || node -p \"'v' + require('{lib_dir}/package.json').version\""],
        # The TZ env var, never Intl (Intl reports Asia/Kolkata as Asia/Calcutta).
        "tz": ["printenv", "TZ"],
    }


# Meera's defaults, kept as module names for existing callers.
IMAGE = DEFAULT_PROFILE.image
USER = DEFAULT_PROFILE.user
APP_DIR = DEFAULT_PROFILE.app_dir
LIB_DIR = DEFAULT_PROFILE.lib_dir
PROBES = probes(LIB_DIR)
REPRO = ["node", "repro.js"]

# Env vars the causality check may override, and what values look like.
OVERRIDABLE = {"tz": "TZ", "lang": "LANG"}
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/+:-]{1,64}$")


def _os_family(os_release: str) -> str:
    fields = dict(
        line.split("=", 1) for line in os_release.splitlines() if "=" in line
    )
    fields = {k: v.strip().strip('"') for k, v in fields.items()}
    if fields.get("ID") == "alpine":
        return "alpine"
    return fields.get("VERSION_CODENAME") or fields.get("ID", "unknown")


class DockerCustomerMachine:
    """Implements woym.contracts.CustomerMachine via `docker exec`."""

    def __init__(self, container: str = config.CUSTOMER_CONTAINER, remote_path: Path | None = None,
                 timeout_s: float = config.RUN_TIMEOUT_S, profile: Profile | None = None):
        self.container = container
        self.remote_path = Path(remote_path or config.REMOTE)
        self.timeout_s = timeout_s
        self.profile = profile or profile_for(container)
        self.probes = probes(self.profile.lib_dir)

    # -- plumbing ---------------------------------------------------------

    def _exec(self, argv: list[str], env: dict[str, str] | None = None, stdin: bytes | None = None,
              check: bool = True) -> subprocess.CompletedProcess[bytes]:
        cmd = ["docker", "exec", "-u", self.profile.user, "-w", self.profile.app_dir]
        if stdin is not None:
            cmd.append("-i")
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [self.container, *argv]
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            raise SandboxError(f"customer machine: {argv[0]} timed out after {self.timeout_s:.0f}s") from None
        if proc.returncode in (125, 126, 127) or (check and proc.returncode != 0):
            err = (proc.stderr or proc.stdout).decode(errors="replace").strip()
            raise SandboxError(f"customer machine: {argv[0]} failed (exit {proc.returncode}): {err}")
        return proc

    # -- CustomerMachine --------------------------------------------------

    def probe(self) -> Env:
        out = {k: self._exec(argv).stdout.decode().strip() for k, argv in self.probes.items()}
        out["os"] = _os_family(out["os"])
        return Env(**{k: out[k] for k in ENV_KEYS})

    def run_repro(self, overrides: dict[str, str] | None = None) -> RunResult:
        env_vars: dict[str, str] = {}
        for key, value in (overrides or {}).items():
            if key not in OVERRIDABLE:
                raise SandboxError(f"customer machine: can't override {key!r}; allowed: {sorted(OVERRIDABLE)}")
            if not _SAFE_VALUE_RE.match(value):
                raise SandboxError(f"customer machine: refusing unsafe {key} value {value!r}")
            env_vars[OVERRIDABLE[key]] = value

        facts = self.probe()
        for key, value in (overrides or {}).items():
            facts = facts.with_value(key, value)

        started = time.perf_counter()
        proc = self._exec(REPRO, env=env_vars, check=False)
        duration = time.perf_counter() - started
        stdout = proc.stdout.decode(errors="replace")
        output = stdout + proc.stderr.decode(errors="replace")
        actual = parse_output(stdout)
        if not actual:
            raise SandboxError(f"customer machine: repro printed no result line (exit {proc.returncode}): {tail(output, 5)}")
        return RunResult(
            env=facts,
            passed=proc.returncode == 0 and actual == EXPECTED,
            expected=EXPECTED,
            actual=actual,
            exit_code=proc.returncode,
            duration_s=round(duration, 3),
            log_tail=tail(output),
            where="customer-machine",
        )

    def install(self, sha: str) -> None:
        """Replace node_modules/bizclock with the tree at `sha` from the vendor
        remote, streamed in over stdin. Swaps directories so a failed unpack
        leaves the old install in place."""
        code = remote.archive(sha, self.remote_path)  # validates the ref
        lib = self.profile.lib_dir
        new, old = f"{lib}.new", f"{lib}.old"
        script = (
            f'set -e; rm -rf "{new}" "{old}"; mkdir -p "{new}"; tar -x -f - -C "{new}"; '
            f'printf "%s" "$WOYM_REF" > "{new}/{REF_MARKER}"; '
            f'if [ -d "{lib}" ]; then mv "{lib}" "{old}"; fi; mv "{new}" "{lib}"; rm -rf "{old}"'
        )
        self._exec(["sh", "-c", script], env={"WOYM_REF": sha}, stdin=code)

    # -- lifecycle (make customer-up / customer-down) ----------------------

    def up(self, ref: str = "v2.3.1") -> Env:
        """Build the image, (re)start the container, install bizclock at `ref`."""
        remote.init_remote(self.remote_path)
        prof = self.profile
        _run(["docker", "build", "-q", "-t", prof.image,
              "--build-arg", f"CUSTOMER={prof.user}", "--build-arg", f"TZ_NAME={prof.tz}",
              "--build-arg", f"LANG_NAME={prof.lang}", "--build-arg", f"ORDER_AT={prof.order_at}",
              str(DOCKERFILE_DIR)], timeout=600)
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, timeout=60)
        _run([
            "docker", "run", "-d", "--init", "--name", self.container, "--hostname", prof.hostname,
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "512m", prof.image,
        ])  # fmt: skip
        self.install(ref)
        return self.probe()

    def down(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, timeout=60)


def _run(cmd: list[str], timeout: float = 120) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise SandboxError(f"{' '.join(cmd[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def main(argv: list[str]) -> int:
    argv = list(argv)
    name = config.CUSTOMER_CONTAINER
    if "--name" in argv:  # e.g. --name kiran-laptop (the epilogue's second customer)
        i = argv.index("--name")
        name = argv[i + 1]
        del argv[i:i + 2]
    m = DockerCustomerMachine(container=name)
    cmd = argv[0] if argv else "probe"
    if cmd == "up":
        print(json.dumps(m.up(*argv[1:2]).as_dict()))
    elif cmd == "down":
        m.down()
    elif cmd == "probe":
        print(json.dumps(m.probe().as_dict()))
    elif cmd == "repro":
        overrides = {"tz": argv[argv.index("--tz") + 1]} if "--tz" in argv else None
        r = m.run_repro(overrides)
        print(f"{r.actual} passed={r.passed} exit={r.exit_code} {r.duration_s}s env={r.env.as_dict()}")
    elif cmd == "install":
        m.install(argv[1])
        print(json.dumps(m.probe().as_dict()))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
