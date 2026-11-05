import pytest

from woym.case import tags
from woym.contracts import Step


def test_tag_format_exact():
    assert tags.tag(42, Step.ENV_RECEIVED, node="20", tz="Asia/Kolkata") == \
        "`woym case=42 step=env node=20 tz=Asia/Kolkata`"


def test_tag_accepts_step_string_and_encodes_bool_list_spaces():
    t = tags.tag(1, "sandbox_run", passed=True, failed=False, keys=["node", "lang", "tz"],
                 actual="cutoff=Fri 17:30 payout=Mon")
    assert t == "`woym case=1 step=sandbox_run passed=1 failed=0 keys=node,lang,tz " \
                "actual=cutoff%3DFri%2017%3A30%20payout%3DMon`".replace("%3A", ":")
    assert " " not in t.split("actual=")[1]


def test_roundtrip_message():
    msg = tags.message("Here are the 5 values.", 7, Step.ENV_RECEIVED,
                       lang="en_IN.UTF-8", actual="a b=c `x`|y", passed=False, keys=("a", "b"))
    case_id, step, kv = tags.parse(msg)
    assert (case_id, step) == (7, Step.ENV_RECEIVED)
    assert kv == {"lang": "en_IN.UTF-8", "actual": "a b=c `x`|y", "passed": "0", "keys": "a,b"}
    assert tags.as_bool(kv["passed"]) is False
    assert tags.as_list(kv["keys"]) == ["a", "b"]
    assert tags.strip_tag(msg) == "Here are the 5 values."


def test_none_dropped_and_percent_roundtrip():
    t = tags.tag(3, Step.BISECTING, culprit=None, v="100%")
    assert "culprit" not in t
    assert tags.parse(t)[2] == {"v": "100%"}


@pytest.mark.parametrize("key", ["case", "step", "Bad", "1x", "a-b", ""])
def test_bad_keys_rejected(key):
    with pytest.raises((ValueError, TypeError)):  # "step" collides with the positional arg
        tags.tag(1, Step.ENV_RECEIVED, **{key: "v"})


def test_list_items_with_commas_rejected():
    with pytest.raises(ValueError):
        tags.tag(1, Step.ENV_RECEIVED, keys=["a,b"])


def test_unknown_step_rejected_on_build():
    with pytest.raises(ValueError):
        tags.tag(1, "nope")


@pytest.mark.parametrize("text", [
    "",
    "just chat",
    "woym case=1 step=env",                       # no backticks
    "`woym case=1 step=env`\nthen more text",      # tag not last
    "`wym case=1 step=env`",
    "`woym step=env`",                             # no case
    "`woym case=1`",                               # no step
    "`woym case=x step=env`",
    "`woym case=-1 step=env`",
    "`woym case=1 step=bogus`",
    "`woym case=1 step=env junk`",                 # token without =
    "`woym case=1 step=env a=1 a=2`",              # duplicate key
    "`woym case=1 step=env Bad=1`",
])
def test_parse_rejects(text):
    assert tags.parse(text) is None


def test_parse_tolerates_trailing_blank_lines_and_indent():
    assert tags.parse("hi\n  `woym case=5 step=fixed actual=ok`  \n\n") == (5, Step.FIXED_ON_CUSTOMER, {"actual": "ok"})


def test_every_step_roundtrips():
    for s in Step:
        assert tags.parse(tags.tag(9, s))[:2] == (9, s)


def test_as_bool_rejects_garbage():
    assert tags.as_bool(None) is None
    with pytest.raises(ValueError):
        tags.as_bool("true")


def test_strip_tag_untagged_unchanged():
    assert tags.strip_tag("hello") == "hello"
