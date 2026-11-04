"""A real HTTP server on 127.0.0.1 that stands in for the KHQRPay API in tests."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

TEST_KEY = "kp_test_example0000000000000000"


class Recorded:
    """One request the fake API received."""

    def __init__(self, method: str, path: str, headers: Any, body: bytes) -> None:
        self.method = method
        self.path = path  # raw request target, exactly as sent
        self.headers = headers  # case-insensitive email.message.Message
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeKHQRPay/1"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        api = self.server.api  # type: ignore[attr-defined]
        api.requests.append(Recorded(self.command, self.path, self.headers, body))
        status, headers, payload, delay = api.next_reply()
        if delay:
            time.sleep(delay)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The timeout test hangs up before the reply; don't print a traceback.
        pass


class FakeAPI:
    """A real HTTP server on 127.0.0.1 that records requests and replays scripted replies."""

    def __init__(self) -> None:
        self.requests = []  # type: List[Recorded]
        self._replies = []  # type: List[Any]
        self._lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.api = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % self._server.server_address[1]

    def reply(
        self,
        status: int = 200,
        json_body: Any = None,
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.0,
    ) -> None:
        all_headers = dict(headers or {})
        if body is None:
            body = json.dumps({} if json_body is None else json_body).encode("utf-8")
            all_headers.setdefault("Content-Type", "application/json")
        with self._lock:
            self._replies.append((status, all_headers, body, delay))

    def next_reply(self) -> Any:
        with self._lock:
            if self._replies:
                return self._replies.pop(0)
        return (200, {"Content-Type": "application/json"}, b"{}", 0.0)

    @property
    def last(self) -> Recorded:
        assert self.requests, "the fake API received no request"
        return self.requests[-1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
