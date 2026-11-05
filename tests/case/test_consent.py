import pytest

from woym.case.consent import ConsentRequest, Verdict, classify, evaluate
from woym.contracts import MessageEvent

REQ = ConsentRequest(case_id=1, room_id="room", thread_id="root", customer="meera", values={"tz": "UTC"})


def ev(text="yes", **kw):
    base = dict(room_id="room", message_id="m1", thread_id="root", sender_id="u-meera", sender_name="meera",
                sender_kind="human", text=text, addressed=False)
    base.update(kw)
    return MessageEvent(**base)


@pytest.mark.parametrize("text", ["yes", "Yes!", "YES", "y", "yep, go ahead", "ok", "sure 👍", "@acme-env yes",
                                  "approved", "✅"])
def test_yes(text):
    assert classify(text) == Verdict.GRANTED
    assert evaluate(REQ, ev(text)) == Verdict.GRANTED


@pytest.mark.parametrize("text", ["no", "No.", "don't", "don’t share", "yes but not the tz", "not yet",
                                  "wait", "nope", "yes, only node", "👎"])
def test_refusals_and_hedges(text):
    assert evaluate(REQ, ev(text)) == Verdict.REFUSED


@pytest.mark.parametrize("text", ["", "hmm", "what does this share?", "yesterday", "eyes", "okayish"])
def test_neither(text):
    assert evaluate(REQ, ev(text)) == Verdict.IGNORED


def test_wrong_sender_name():
    assert evaluate(REQ, ev(sender_name="dev", sender_id="u-dev")) == Verdict.IGNORED


def test_agent_named_meera_ignored():
    assert evaluate(REQ, ev(sender_kind="agent")) == Verdict.IGNORED
    assert evaluate(REQ, ev(sender_kind="system")) == Verdict.IGNORED


def test_wrong_thread_or_top_level_or_room():
    assert evaluate(REQ, ev(thread_id="other")) == Verdict.IGNORED
    assert evaluate(REQ, ev(thread_id=None)) == Verdict.IGNORED
    assert evaluate(REQ, ev(room_id="other")) == Verdict.IGNORED


def test_sender_id_preferred_over_handle():
    req = ConsentRequest(1, "room", "root", "meera", customer_id="u-meera")
    assert evaluate(req, ev()) == Verdict.GRANTED
    # someone who renamed themselves "meera" but has a different id
    assert evaluate(req, ev(sender_id="u-imposter")) == Verdict.IGNORED
