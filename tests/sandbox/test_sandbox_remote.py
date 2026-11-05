import io
import tarfile

import pytest
from sandboxfx import MAIN_SHA, V231_SHA

from woym.sandbox import remote


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    return remote.init_remote(tmp_path_factory.mktemp("r") / "bizclock.git")


def test_history_is_deterministic(bare):
    assert remote.ls_remote("main", bare) == MAIN_SHA
    assert remote.resolve("v2.3.1", bare) == V231_SHA
    assert remote.ls_remote("fix/case-1", bare) is None


def test_archive_has_repro_and_versions(bare):
    def files(ref):
        with tarfile.open(fileobj=io.BytesIO(remote.archive(ref, bare))) as t:
            return {m.name: t.extractfile(m).read().decode() for m in t.getmembers() if m.isfile()}

    v231, main = files("v2.3.1"), files("main")
    assert "examples/payout.js" in v231 and "examples/payout.js" in main
    assert '"version": "2.3.1"' in v231["package.json"]
    assert '"version": "2.3.2-dev"' in main["package.json"]
    assert "src/format.js" not in v231 and "src/format.js" in main
    # the bug is on both refs
    for f in (v231, main):
        assert "Math.round(-date.getTimezoneOffset() / 60)" in f["src/index.js"]


def test_init_keeps_existing_remote_unless_forced(bare):
    marker = bare / "woym-marker"
    marker.write_text("x")
    remote.init_remote(bare)
    assert marker.exists()
    remote.init_remote(bare, force=True)
    assert not marker.exists()
    assert remote.ls_remote("main", bare) == MAIN_SHA


@pytest.mark.parametrize("ref", ["-o", "--output=/tmp/x", "main..v2.3.1", "a b", "HEAD@{1}", "x;rm", "", "main/"])
def test_unsafe_refs_rejected(bare, ref):
    with pytest.raises(remote.RemoteError):
        remote.archive(ref, bare)
