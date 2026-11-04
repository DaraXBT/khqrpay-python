import json
import pickle
import socket
import uuid
from decimal import Decimal

import pytest

import khqrpay
from khqrpay import KHQRPay, KHQRPayError

from fake_api import TEST_KEY, FakeAPI


# --- construction ----------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "",
        "kp_live",
        "kp_test",
        "kp_live_",
        "kp_test_",
        "sk_test_Secret9",
        "pk_live_Secret9",
        "KP_TEST_Secret9",
        " kp_test_Secret9",
        "kp_test_Secret9\n",
        "kp_test_Sec ret9",
    ],
)
def test_refuses_a_bad_key(key):
    with pytest.raises(ValueError) as info:
        KHQRPay(key)
    message = str(info.value)
    assert "kp_live_" in message or "secret key" in message
    assert "Secret9" not in message and "ret9" not in message


@pytest.mark.parametrize("key", [None, 123, b"kp_test_abc"])
def test_refuses_a_key_that_is_not_a_string(key):
    with pytest.raises(TypeError):
        KHQRPay(key)  # type: ignore[arg-type]


def test_accepts_live_and_test_keys_and_hides_them():
    for key in ("kp_live_abc123", "kp_test_abc123"):
        client = KHQRPay(key)
        assert key not in repr(client)
        assert "abc123" not in repr(client)
    assert "mode=live" in repr(KHQRPay("kp_live_abc123"))
    assert "mode=test" in repr(KHQRPay("kp_test_abc123"))


def test_defaults():
    client = KHQRPay(TEST_KEY)
    assert client.base_url == "https://khqrpay.dev"
    assert client.timeout == 30.0
    assert khqrpay.__version__ == "0.1.0"


def test_base_url_and_timeout_are_overridable():
    client = KHQRPay(TEST_KEY, base_url="http://127.0.0.1:9999/", timeout=5)
    assert client.base_url == "http://127.0.0.1:9999"
    assert client.timeout == 5.0


@pytest.mark.parametrize("base_url", ["khqrpay.dev", "ftp://khqrpay.dev", "https://", "https://khqrpay.dev?x=1"])
def test_refuses_a_bad_base_url(base_url):
    with pytest.raises(ValueError):
        KHQRPay(TEST_KEY, base_url=base_url)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "30"])
def test_refuses_a_bad_timeout(timeout):
    with pytest.raises(ValueError):
        KHQRPay(TEST_KEY, timeout=timeout)


# --- create ----------------------------------------------------------------


def test_create_sends_the_request_and_returns_the_payment(api, client, vectors):
    api.reply(201, vectors["paymentResponse"])
    payment = client.payments.create(vectors["createRequest"]["body"])

    assert payment == vectors["paymentResponse"]
    request = api.last
    assert request.method == vectors["createRequest"]["method"] == "POST"
    assert request.path == vectors["createRequest"]["path"] == "/v1/payments"
    assert request.headers["Authorization"] == "Bearer " + TEST_KEY
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "khqrpay-python/0.1.0"
    assert uuid.UUID(request.headers["Idempotency-Key"]).version == 4
    assert request.json() == vectors["createRequest"]["body"]
    assert list(request.json()) == list(vectors["createRequest"]["body"])


def test_create_generates_a_new_idempotency_key_each_call(api, client):
    client.payments.create({"amount": "1.00"})
    client.payments.create({"amount": "1.00"})
    first, second = (r.headers["Idempotency-Key"] for r in api.requests)
    assert first != second


def test_create_passes_the_idempotency_key_through(api, client):
    client.payments.create({"amount": "1.00"}, idempotency_key="order-2048")
    assert api.last.headers["Idempotency-Key"] == "order-2048"


def test_create_uses_the_body_idempotency_key_when_no_header_key_is_given(api, client):
    client.payments.create({"amount": "1.00", "idempotency_key": "order-7"})
    assert api.last.headers["Idempotency-Key"] == "order-7"
    assert api.last.json() == {"amount": "1.00", "idempotency_key": "order-7"}
    client.payments.create({"amount": "1.00", "idempotency_key": "order-7"}, idempotency_key="order-8")
    assert api.last.headers["Idempotency-Key"] == "order-8"


def test_create_sends_the_body_exactly_as_given(api, client):
    params = {
        "metadata": {"note": "ការទូទាត់", "nested": {"a": [1, None]}},
        "amount": "20000",
        "currency": "KHR",
        "reference_id": None,
        "custom_field": True,
    }
    original = json.loads(json.dumps(params))
    client.payments.create(params)
    assert api.last.json() == original
    assert list(api.last.json()) == list(original)
    assert params == original  # not mutated


@pytest.mark.parametrize(
    "amount, sent",
    [
        (16.88, "16.88"),
        (4.5, "4.5"),
        (0.1 + 0.2, "0.30000000000000004"),
        (20000, "20000"),
        (20000.0, "20000"),
        (Decimal("16.88"), "16.88"),
        (Decimal("1E+2"), "100"),
        ("16.88", "16.88"),
        ("abc", "abc"),
    ],
)
def test_create_sends_amounts_as_strings_with_no_rounding(api, client, amount, sent):
    params = {"amount": amount}
    client.payments.create(params)
    assert api.last.json()["amount"] == sent
    assert params == {"amount": amount}


def test_create_refuses_a_non_finite_amount(api, client):
    for amount in (float("nan"), float("inf"), Decimal("NaN")):
        with pytest.raises(ValueError):
            client.payments.create({"amount": amount})
    assert api.requests == []


def test_create_refuses_params_that_are_not_a_dict(client):
    with pytest.raises(TypeError):
        client.payments.create("16.88")  # type: ignore[arg-type]


def test_create_accepts_a_200_replay(api, client, vectors):
    api.reply(200, vectors["paymentResponse"])
    assert client.payments.create({"amount": "16.88"}, idempotency_key="k")["id"] == vectors["paymentResponse"]["id"]


# --- errors ----------------------------------------------------------------


def test_error_response_raises_khqrpay_error(api, client, vectors):
    error = vectors["errorResponse"]
    api.reply(error["status"], error["body"])
    with pytest.raises(KHQRPayError) as info:
        client.payments.create({})
    assert info.value.code == "invalid_request"
    assert info.value.status == 400
    assert info.value.message == "amount is required"
    assert str(info.value) == "amount is required"
    assert info.value.retry_after is None
    assert TEST_KEY not in repr(info.value)


def test_429_sets_retry_after(api, client):
    api.reply(429, {"error": "rate_limited", "message": "Too many requests"}, headers={"Retry-After": "30"})
    with pytest.raises(KHQRPayError) as info:
        client.payments.create({"amount": "1.00"})
    assert info.value.code == "rate_limited"
    assert info.value.status == 429
    assert info.value.retry_after == 30


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2015 07:28:00 GMT", "soon", "-5", "1.5"])
def test_retry_after_that_is_not_whole_seconds_is_none(api, client, value):
    api.reply(503, {"error": "temporarily_unavailable", "message": "Try again"}, headers={"Retry-After": value})
    with pytest.raises(KHQRPayError) as info:
        client.usage.retrieve()
    assert info.value.code == "temporarily_unavailable"
    assert info.value.retry_after is None


def test_non_json_502_is_invalid_response(api, client):
    api.reply(502, body=b"<html>Bad gateway</html>", headers={"Content-Type": "text/html"})
    with pytest.raises(KHQRPayError) as info:
        client.payments.create({"amount": "1.00"})
    assert info.value.code == "invalid_response"
    assert info.value.status == 502
    assert info.value.retry_after is None


def test_non_2xx_json_without_an_error_code_is_invalid_response(api, client):
    api.reply(500, {"message": "upstream said no"})
    with pytest.raises(KHQRPayError) as info:
        client.usage.retrieve()
    assert info.value.code == "invalid_response"
    assert info.value.status == 500
    assert info.value.message == "upstream said no"


@pytest.mark.parametrize("body", [b"", b"not json", b"[1,2]", b"\xff\xfe"])
def test_2xx_that_is_not_a_json_object_is_invalid_response(api, client, body):
    api.reply(200, body=body)
    with pytest.raises(KHQRPayError) as info:
        client.usage.retrieve()
    assert info.value.code == "invalid_response"
    assert info.value.status == 200


def test_unreachable_server_is_network_error():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = KHQRPay(TEST_KEY, base_url="http://127.0.0.1:%d" % port, timeout=5)
    with pytest.raises(KHQRPayError) as info:
        client.usage.retrieve()
    assert info.value.code == "network_error"
    assert info.value.status == 0
    assert info.value.retry_after is None
    assert TEST_KEY not in str(info.value)


def test_timeout_is_network_error(api):
    api.reply(200, {}, delay=2.0)
    client = KHQRPay(TEST_KEY, base_url=api.base_url, timeout=0.2)
    with pytest.raises(KHQRPayError) as info:
        client.usage.retrieve()
    assert info.value.code == "network_error"
    assert info.value.status == 0


def test_redirects_are_not_followed_so_the_key_stays_put(api, client):
    elsewhere = FakeAPI()
    elsewhere.start()
    try:
        api.reply(302, body=b"", headers={"Location": elsewhere.base_url + "/v1/usage"})
        with pytest.raises(KHQRPayError) as info:
            client.usage.retrieve()
        assert info.value.status == 302
        assert info.value.code == "invalid_response"
        assert elsewhere.requests == []
    finally:
        elsewhere.stop()


def test_error_survives_pickling():
    error = KHQRPayError("rate_limited", "slow down", 429, 30)
    copy = pickle.loads(pickle.dumps(error))
    assert (copy.code, copy.message, copy.status, copy.retry_after) == ("rate_limited", "slow down", 429, 30)


# --- retrieve, list, simulate, usage ----------------------------------------


def test_retrieve_url_encodes_the_id(api, client, vectors):
    api.reply(200, vectors["paymentResponse"])
    assert client.payments.retrieve("pay/../x y?z#w%")["object"] == "payment"
    assert api.last.method == "GET"
    assert api.last.path == "/v1/payments/pay%2F..%2Fx%20y%3Fz%23w%25"
    assert api.last.headers["Authorization"] == "Bearer " + TEST_KEY
    assert api.last.headers["User-Agent"] == "khqrpay-python/0.1.0"
    assert "Content-Type" not in api.last.headers
    assert "Idempotency-Key" not in api.last.headers
    assert api.last.body == b""


def test_retrieve_sends_a_plain_id_unchanged(api, client):
    client.payments.retrieve("pFQ3xV8aN2rL7kTm9sB4cWd6hJ1yZ0eU")
    assert api.last.path == "/v1/payments/pFQ3xV8aN2rL7kTm9sB4cWd6hJ1yZ0eU"


@pytest.mark.parametrize("payment_id", ["", None, 42])
def test_retrieve_and_simulate_refuse_an_empty_id(api, client, payment_id):
    with pytest.raises(ValueError):
        client.payments.retrieve(payment_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        client.payments.simulate(payment_id, "paid")  # type: ignore[arg-type]
    assert api.requests == []


def test_list_without_params_sends_no_query(api, client):
    page = {"object": "list", "data": [], "has_more": False, "next_cursor": None}
    api.reply(200, page)
    assert client.payments.list() == page
    assert api.last.method == "GET"
    assert api.last.path == "/v1/payments"


def test_list_builds_the_query_string(api, client):
    client.payments.list(status="paid", limit=50, cursor="c/1+2= x")
    assert api.last.path == "/v1/payments?status=paid&limit=50&cursor=c%2F1%2B2%3D%20x"


def test_list_omits_unset_params(api, client):
    client.payments.list(limit=10)
    assert api.last.path == "/v1/payments?limit=10"
    client.payments.list(status="pending", cursor=None)
    assert api.last.path == "/v1/payments?status=pending"
    client.payments.list(cursor="abc")
    assert api.last.path == "/v1/payments?cursor=abc"


def test_simulate_posts_the_status(api, client, vectors):
    api.reply(200, dict(vectors["paymentResponse"], status="paid"))
    payment = client.payments.simulate("pay test/1", "paid")
    assert payment["status"] == "paid"
    assert api.last.method == "POST"
    assert api.last.path == "/v1/test/payments/pay%20test%2F1/simulate"
    assert api.last.headers["Content-Type"] == "application/json"
    assert api.last.json() == {"status": "paid"}


def test_usage_retrieve(api, client):
    usage = {
        "plan": "free",
        "limit": 50,
        "used": 3,
        "remaining": 47,
        "period_start": "2026-11-01T00:00:00.000Z",
        "period_end": "2026-12-01T00:00:00.000Z",
    }
    api.reply(200, usage)
    assert client.usage.retrieve() == usage
    assert api.last.method == "GET"
    assert api.last.path == "/v1/usage"


def test_base_url_with_a_path_prefix(api):
    client = KHQRPay(TEST_KEY, base_url=api.base_url + "/proxy/")
    client.usage.retrieve()
    assert api.last.path == "/proxy/v1/usage"
