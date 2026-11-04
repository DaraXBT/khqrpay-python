import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

import khqrpay
from khqrpay import KHQRPay, KHQRPayError, webhooks

VECTORS = json.loads((Path(__file__).parent / "test-vectors.json").read_text(encoding="utf-8"))
CASES = VECTORS["cases"]
SECRET = VECTORS["secret"]
VALID = CASES[0]
assert VALID["name"] == "valid" and VALID["valid"] is True


def _secret(case):
    return case.get("secret", SECRET)


def _sign(body, t, secret=SECRET):
    message = ("%s." % t).encode("ascii") + (body.encode("utf-8") if isinstance(body, str) else body)
    return "t=%s,v1=%s" % (t, hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest())


ENTRY_POINTS = {
    "module": webhooks,
    "package": khqrpay.webhooks,
    "class": KHQRPay.webhooks,
    "instance": KHQRPay("kp_test_x1").webhooks,
}


def test_vectors_file_has_the_expected_shape():
    assert CASES
    names = [c["name"] for c in CASES]
    assert len(names) == len(set(names))
    assert any(c["valid"] for c in CASES) and any(not c["valid"] for c in CASES)
    assert {"createRequest", "paymentResponse", "errorResponse"} <= set(VECTORS)


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_verify_vector(case, entry):
    verify = ENTRY_POINTS[entry].verify
    for body in (case["body"], case["body"].encode("utf-8")):
        assert verify(body, case["header"], _secret(case), now=case["now"]) is case["valid"]


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_construct_event_vector(case, entry):
    construct_event = ENTRY_POINTS[entry].construct_event
    for body in (case["body"], case["body"].encode("utf-8")):
        if case["valid"]:
            event = construct_event(body, case["header"], _secret(case), now=case["now"])
            assert event == json.loads(case["body"])
        else:
            with pytest.raises(KHQRPayError) as info:
                construct_event(body, case["header"], _secret(case), now=case["now"])
            assert info.value.code == "invalid_signature"
            assert info.value.status == 400
            assert info.value.retry_after is None


def test_signature_hex_is_case_insensitive():
    t, v1 = VALID["header"].split(",")
    header = t + "," + v1.upper().replace("V1=", "v1=")
    assert header != VALID["header"]
    assert webhooks.verify(VALID["body"], header, SECRET, now=VALID["now"]) is True


def test_tolerance_is_configurable():
    old = next(c for c in CASES if c["name"] == "timestamp 6 minutes old")
    assert webhooks.verify(old["body"], old["header"], SECRET, now=old["now"]) is False
    assert webhooks.verify(old["body"], old["header"], SECRET, now=old["now"], tolerance_sec=360) is True
    assert webhooks.verify(VALID["body"], VALID["header"], SECRET, now=VALID["now"], tolerance_sec=5) is False


def test_now_defaults_to_the_system_clock():
    body = '{"id":"evt_now","type":"payment.scanned"}'
    header = _sign(body, int(time.time()))
    assert webhooks.verify(body, header, SECRET) is True
    assert webhooks.construct_event(body, header, SECRET)["id"] == "evt_now"
    stale = _sign(body, int(time.time()) - 3600)
    assert webhooks.verify(body, stale, SECRET) is False


def test_raw_bytes_are_used_as_received():
    # Bytes that aren't valid UTF-8 still verify; verify never decodes them.
    body = b'{"id":"evt_raw","note":"\xff"}'
    header = _sign(body, VALID["now"])
    assert webhooks.verify(body, header, SECRET, now=VALID["now"]) is True
    assert webhooks.verify(bytearray(body), header, SECRET, now=VALID["now"]) is True
    assert webhooks.verify(memoryview(body), header, SECRET, now=VALID["now"]) is True


def test_reserialized_json_does_not_verify():
    reserialized = json.dumps(json.loads(VALID["body"]))
    assert reserialized != VALID["body"]
    assert webhooks.verify(reserialized, VALID["header"], SECRET, now=VALID["now"]) is False


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "t=",
        "v1=" + "a" * 64,
        "t=abc,v1=" + "a" * 64,
        "t=+1793750400,v1=" + "a" * 64,
        "t= 1793750400,v1=" + "a" * 64,
        "t=1793750400 ,v1=" + "a" * 64,
        "t=1_793_750_400,v1=" + "a" * 64,
        "t=١٧٩٣,v1=" + "a" * 64,
        "t=1793750400,v1=" + "a" * 63,
        "t=1793750400,v1=" + "a" * 65,
        "t=1793750400,v1=" + "١" * 64,
        12345,
        b"t=1793750400",
    ],
)
def test_malformed_headers_return_false_and_never_raise(header):
    assert webhooks.verify(VALID["body"], header, SECRET, now=VALID["now"]) is False
    with pytest.raises(KHQRPayError) as info:
        webhooks.construct_event(VALID["body"], header, SECRET, now=VALID["now"])
    assert info.value.code == "invalid_signature"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw_body": None},
        {"raw_body": 123},
        {"raw_body": {"id": "evt_test_1"}},
        {"secret": None},
        {"secret": ""},
        {"secret": 42},
        {"now": "1793750410"},
        {"now": None, "tolerance_sec": "300"},
    ],
)
def test_bad_argument_types_return_false_and_never_raise(kwargs):
    args = {"raw_body": VALID["body"], "secret": SECRET, "now": VALID["now"], "tolerance_sec": 300}
    args.update(kwargs)
    assert (
        webhooks.verify(
            args["raw_body"], VALID["header"], args["secret"], now=args["now"], tolerance_sec=args["tolerance_sec"]
        )
        is False
    )


def test_empty_secret_never_verifies_even_a_matching_signature():
    body = VALID["body"]
    message = ("%s." % VALID["now"]).encode("ascii") + body.encode("utf-8")
    header = "t=%s,v1=%s" % (VALID["now"], hmac.new(b"", message, hashlib.sha256).hexdigest())
    assert webhooks.verify(body, header, "", now=VALID["now"]) is False


def test_header_parts_split_on_the_first_equals_and_last_value_wins():
    v1 = VALID["header"].split("v1=", 1)[1]
    assert webhooks.verify(VALID["body"], "t=1,t=1793750400,v1=" + v1, SECRET, now=VALID["now"]) is True
    assert webhooks.verify(VALID["body"], "t=1793750400,v1=%s,extra,x=y=z" % v1, SECRET, now=VALID["now"]) is True
    assert webhooks.verify(VALID["body"], "t=1793750400,v1=%s,t=1" % v1, SECRET, now=VALID["now"]) is False


def test_construct_event_rejects_a_signed_body_that_is_not_a_json_object():
    for body in ("not json", "[1,2,3]"):
        header = _sign(body, VALID["now"])
        assert webhooks.verify(body, header, SECRET, now=VALID["now"]) is True
        with pytest.raises(KHQRPayError) as info:
            webhooks.construct_event(body, header, SECRET, now=VALID["now"])
        assert info.value.code == "invalid_payload"
        assert info.value.status == 400


def test_construct_event_keeps_unicode():
    case = next(c for c in CASES if c["name"] == "unicode body")
    event = webhooks.construct_event(case["body"].encode("utf-8"), case["header"], SECRET, now=case["now"])
    assert event == {"id": "evt_km", "note": "ការទូទាត់ ✓"}


def test_error_messages_never_contain_the_secret():
    for case in CASES:
        if case["valid"]:
            continue
        with pytest.raises(KHQRPayError) as info:
            webhooks.construct_event(case["body"], case["header"], _secret(case), now=case["now"])
        assert _secret(case) not in str(info.value)
        assert _secret(case) not in repr(info.value)
