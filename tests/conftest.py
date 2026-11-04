import json
from pathlib import Path
from typing import Any, Dict

import pytest

from fake_api import TEST_KEY, FakeAPI
from khqrpay import KHQRPay

VECTORS_PATH = Path(__file__).parent / "test-vectors.json"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep requests to the fake API off any proxy configured on the machine.
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture(scope="session")
def vectors() -> Dict[str, Any]:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def api():
    fake = FakeAPI()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def client(api: FakeAPI) -> KHQRPay:
    return KHQRPay(TEST_KEY, base_url=api.base_url)
