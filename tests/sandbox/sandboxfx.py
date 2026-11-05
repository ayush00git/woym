"""Shared helpers for the sandbox tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from woym.contracts import Env

CUSTOMER_ENV = Env(node="20", lang="en_IN.UTF-8", os="bookworm", bizclock="v2.3.1", tz="Asia/Kolkata")

# The fixture history is built with fixed authors and dates, so these never change
# unless fixtures/bizclock or the build script does.
MAIN_SHA = "2341e8436592be75696ea6f17d4a93906f8771cf"
V231_SHA = "ba94567ac38b370aa558e848345a08243465fa36"


def docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0


needs_docker = pytest.mark.skipif(not docker_ok(), reason="docker daemon not available")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
