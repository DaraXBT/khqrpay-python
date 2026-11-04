# KHQRPay for Python: official KHQR payments SDK for Cambodia

Accept KHQR payments from ABA, ACLEDA, Wing and every Bakong app, with the money going straight to your ABA account. [KHQRPay](https://khqrpay.dev) creates the QR, watches for the payment and sends you a signed webhook when it’s paid. It never holds your money. The full API reference is at [khqrpay.dev/docs](https://khqrpay.dev/docs).

This is the official Python SDK. It needs Python 3.8 or newer and has no dependencies: it uses only the standard library.

## Install

```sh
pip install khqrpay
```

## Create a payment

Get a secret key from your dashboard. Keep it on your server, in an environment variable, and never in your code or your logs. Use a `kp_test_` key while you build: it creates simulated payments. A `kp_live_` key moves real money.

```python
import os

from khqrpay import KHQRPay

client = KHQRPay(os.environ["KHQRPAY_SECRET_KEY"])

payment = client.payments.create({
    "amount": "16.88",
    "currency": "USD",
    "reference_id": "INV-2048",
    "metadata": {"cart_id": "c_1"},
})

print(payment["id"], payment["status"])  # pending
print(payment["checkout_url"])           # send the customer here
```

Then redirect the customer to `checkout_url`. KHQRPay hosts the page with the QR. In Flask:

```python
from flask import redirect

@app.post("/checkout")
def checkout():
    payment = client.payments.create(
        {"amount": "16.88", "reference_id": "INV-2048"},
        idempotency_key="INV-2048-attempt-1",  # one per checkout attempt
    )
    return redirect(payment["checkout_url"], code=303)
```

If you’d rather draw the QR yourself, `payment["qr_string"]` is the KHQR payload.

Amounts:

- Send amounts as strings: `"16.88"` for USD (up to 2 decimals), `"20000"` for KHR (whole riel).
- You may pass a number, such as `16.88` or `20000`. The SDK turns it into the same string, with no rounding.
- `currency` is `"USD"` or `"KHR"`, and defaults to USD. When it differs from your ABA link’s currency, KHQRPay converts the amount. The payment’s `amount` and `currency` are what the payer pays. `requested_amount` and `requested_currency` are what you sent.

Apart from turning a numeric amount into a string, the SDK sends your dict as the JSON body exactly as given. Every create also sends an `Idempotency-Key`. If you don’t pass one, the SDK makes a random one. Pass your own, one per checkout attempt (such as `INV-2048-attempt-1`), so a retry of that attempt gets the same payment back. Don’t reuse one key for the whole order: a customer who tries again after the QR expired would get the expired payment back.

The other calls:

```python
payment = client.payments.retrieve(payment["id"])

page = client.payments.list(status="paid", limit=50)
for item in page["data"]:
    print(item["id"], item["amount"], item["currency"])
while page["has_more"]:
    page = client.payments.list(status="paid", limit=50, cursor=page["next_cursor"])

usage = client.usage.retrieve()  # {"plan": "free", "limit": ..., "used": ..., ...}
```

Every call returns the API’s JSON as a dict. List items leave out `metadata`: read it with `retrieve`.

The calls block, like any `urllib` call. In an `async` app, run them in a thread. On Python 3.9 and up, that’s `await asyncio.to_thread(client.payments.create, params)`.

## Verify a webhook

When a payment is scanned, paid, expired or failed, KHQRPay sends a POST to your endpoint. It’s signed in the `X-KHQRPay-Signature` header with your endpoint’s signing secret (`whsec_...`).

> **Verify the raw body, before you parse it.** Pass the exact bytes you received. If your framework parses the JSON and you serialize it again, the bytes change and the check fails. Never verify `request.json`.

- `khqrpay.webhooks.construct_event(raw_body, signature_header, secret)` checks the signature and returns the parsed event. If the check fails, it raises `KHQRPayError` with `code="invalid_signature"`. If a correctly signed body isn’t a JSON object, it raises `code="invalid_payload"`.
- `khqrpay.webhooks.verify(...)` takes the same arguments and returns `True` or `False`. It never raises.
- Both reject a timestamp more than 5 minutes off. Change that with `tolerance_sec=`.
- Neither needs an API key. `KHQRPay.webhooks` and `client.webhooks` are the same functions.

### Flask

```python
import os

import khqrpay
from flask import Flask, request

app = Flask(__name__)
WEBHOOK_SECRET = os.environ["KHQRPAY_WEBHOOK_SECRET"]

@app.post("/webhooks/khqrpay")
def khqrpay_webhook():
    try:
        event = khqrpay.webhooks.construct_event(
            request.get_data(),  # the raw bytes, not request.json
            request.headers.get("X-KHQRPay-Signature"),
            WEBHOOK_SECRET,
        )
    except khqrpay.KHQRPayError:
        return "", 400

    if event.get("test") is True or event.get("livemode") is not True:
        return "", 200  # a test event or test-mode payment: nothing to fulfil
    if event["type"] == "payment.completed":
        payment = event["data"]["payment"]
        mark_order_paid(payment["reference_id"], event_id=event["id"])
    return "", 200
```

### Django

```python
import os

import khqrpay
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

WEBHOOK_SECRET = os.environ["KHQRPAY_WEBHOOK_SECRET"]

@csrf_exempt  # KHQRPay can't send a CSRF token; the signature protects this view
@require_POST
def khqrpay_webhook(request):
    try:
        event = khqrpay.webhooks.construct_event(
            request.body,  # the raw bytes, not json.loads(...)
            request.headers.get("X-KHQRPay-Signature"),
            WEBHOOK_SECRET,
        )
    except khqrpay.KHQRPayError:
        return HttpResponse(status=400)

    if event.get("test") is True or event.get("livemode") is not True:
        return HttpResponse(status=200)  # a test event or test-mode payment: nothing to fulfil
    if event["type"] == "payment.completed":
        payment = event["data"]["payment"]
        mark_order_paid(payment["reference_id"], event_id=event["id"])
    return HttpResponse(status=200)
```

### FastAPI

```python
import os

import khqrpay
from fastapi import FastAPI, Request, Response

app = FastAPI()
WEBHOOK_SECRET = os.environ["KHQRPAY_WEBHOOK_SECRET"]

@app.post("/webhooks/khqrpay")
async def khqrpay_webhook(request: Request):
    raw_body = await request.body()  # the raw bytes; don't declare a Pydantic body model
    try:
        event = khqrpay.webhooks.construct_event(
            raw_body,
            request.headers.get("X-KHQRPay-Signature"),
            WEBHOOK_SECRET,
        )
    except khqrpay.KHQRPayError:
        return Response(status_code=400)

    if event.get("test") is True or event.get("livemode") is not True:
        return Response(status_code=200)  # a test event or test-mode payment: nothing to fulfil
    if event["type"] == "payment.completed":
        payment = event["data"]["payment"]
        await mark_order_paid(payment["reference_id"], event_id=event["id"])
    return Response(status_code=200)
```

A few rules for the handler:

- Event types are `payment.completed`, `payment.scanned`, `payment.expired` and `payment.failed`.
- Answer with a 2xx quickly. Anything else, or no answer, is retried.
- Every retry has the same `event["id"]`. Store it and skip events you’ve already handled.
- Check `payment["reference_id"]`, `amount` and `currency` against your order before you ship.

## Errors and retries

Every failed call raises `KHQRPayError`:

| Attribute | What it holds |
|---|---|
| `code` | The stable error code. Branch on this. |
| `message` | An explanation for people. It may change, so don’t branch on it. |
| `status` | The HTTP status, or `0` when there was no response. |
| `retry_after` | Seconds from the `Retry-After` header, or `None`. |

Codes come from the API, for example `invalid_request`, `invalid_amount`, `unauthorized`, `quota_exceeded`, `payment_not_found` and `rate_limited`. For API calls, the SDK adds two of its own:

- `network_error`: KHQRPay couldn’t be reached, or the call timed out (30 seconds by default).
- `invalid_response`: the reply wasn’t the JSON the SDK expected.

The SDK never retries by itself. A create is safe to retry with the same idempotency key: you get the same payment back, never a second one. Retry on 429, 503, 502 and 500, after `Retry-After` when it’s set. A `network_error` is safe to retry the same way.

```python
import time

from khqrpay import KHQRPayError

RETRY_STATUSES = {429, 500, 502, 503}

def create_payment(client, params, idempotency_key, attempts=4):
    for attempt in range(attempts):
        try:
            # The same key on every attempt.
            return client.payments.create(params, idempotency_key=idempotency_key)
        except KHQRPayError as err:
            retryable = err.status in RETRY_STATUSES or err.code == "network_error"
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(err.retry_after if err.retry_after is not None else 2 ** attempt)

payment = create_payment(client, {"amount": "16.88", "reference_id": "INV-2048"}, "INV-2048-attempt-1")
```

Branch on the code for the rest:

```python
try:
    payment = client.payments.create({"amount": "0.001"})
except KHQRPayError as err:
    if err.code in ("invalid_amount", "amount_too_low", "amount_too_high"):
        ...  # show the customer a message
    else:
        raise
```

You can change the timeout and the base URL:

```python
client = KHQRPay(os.environ["KHQRPAY_SECRET_KEY"], timeout=10, base_url="https://khqrpay.dev")
```

## Test mode

With a `kp_test_` key, payments are simulated. No money moves, and test payments never count against your quota. Move one through its life with `simulate`. KHQRPay sends the webhook just as it would for a live payment, so you can test your handler end to end.

```python
client = KHQRPay(os.environ["KHQRPAY_TEST_SECRET_KEY"])  # kp_test_...

payment = client.payments.create({"amount": "1.00", "reference_id": "TEST-1"})

client.payments.simulate(payment["id"], "scanned")
paid = client.payments.simulate(payment["id"], "paid")
assert paid["status"] == "paid"
```

The status is `"scanned"`, `"paid"`, `"expired"` or `"failed"`. `simulate` only works with test keys.

## Guide

For a step-by-step walkthrough, read [Accept KHQR payments with Python](https://khqrpay.dev/guides/khqr-api-python).

## Troubleshooting

**`network_error` with `CERTIFICATE_VERIFY_FAILED` on a Mac:** Python from python.org doesn’t trust any HTTPS certificates until you run **Install Certificates.command** (in `/Applications/Python 3.x/`). Run it once and try again. Nothing in your code needs to change.

## License

MIT. See [LICENSE](LICENSE).
