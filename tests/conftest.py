import json
from pathlib import Path

import httpx
import pytest

from tarka_md.client import NepseClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeTokenParser:
    """Mimics the WASM parser: strips nothing, just echoes the token."""

    def parse_token_response(self, body):
        return body["accessToken"], body["refreshToken"]


class FakeNepse:
    """In-process stand-in for nepalstock.com, routed through httpx.MockTransport."""

    def __init__(self):
        self.token_n = 0
        self.calls: list[str] = []
        self.expire_next = False
        self.fail_depth_ids: set[int] = set()
        self.status = "OPEN"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/api/authenticate/prove":
            self.token_n += 1
            return httpx.Response(200, json={
                "accessToken": f"tok{self.token_n}", "refreshToken": "r",
                "salt1": "1", "salt2": "2", "salt3": "3", "salt4": "4", "salt5": "5",
                "serverTime": 1_790_000_000_000,
            })
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Salter tok") or self.expire_next:
            self.expire_next = False
            return httpx.Response(401)
        if path == "/api/nots/nepse-data/market-open":
            return httpx.Response(200, json={"isOpen": self.status, "asOf": "2026-10-04T13:00:00", "id": 77})
        if path == "/api/nots/security":
            return httpx.Response(200, json=load("securities.json"))
        if path == "/api/nots/lives-market":
            return httpx.Response(200, json=load("lives_market.json"))
        if path.startswith("/api/nots/nepse-data/marketdepth/"):
            sid = int(path.rstrip("/").rsplit("/", 1)[1])
            if sid in self.fail_depth_ids:
                return httpx.Response(503)
            body = load("marketdepth.json")
            body["securityId"] = sid
            return httpx.Response(200, json=body)
        return httpx.Response(404)


@pytest.fixture
def fake_nepse():
    return FakeNepse()


@pytest.fixture
def make_client(fake_nepse):
    def _make(**kw):
        return NepseClient(
            transport=httpx.MockTransport(fake_nepse.handler),
            token_parser=FakeTokenParser(),
            **kw,
        )
    return _make
