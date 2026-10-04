import json
from pathlib import Path

import httpx
import pytest

from tarka_md.auth import floorsheet_payload_id
from tarka_md.client import NepseClient
from tarka_md.schedule import now_npt

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
        self.market_id = 77
        self.throttle_next: float | None = None  # respond 429 with this Retry-After once
        self.post_ids: list[int] = []

    def expected_post_id(self) -> int:
        return floorsheet_payload_id(self.market_id, [1, 2, 3, 4, 5], now_npt().day)

    def floorsheet(self, request, rows_for_page, total_pages):
        body = json.loads(request.content or b"{}")
        self.post_ids.append(body.get("id"))
        if body.get("id") != self.expected_post_id():
            return httpx.Response(400)
        page = int(request.url.params.get("page", 0))
        return httpx.Response(200, json={"floorsheets": {
            "content": rows_for_page(page), "totalPages": total_pages, "number": page}})

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
        if self.throttle_next is not None:
            delay, self.throttle_next = self.throttle_next, None
            return httpx.Response(429, headers={"Retry-After": str(delay)})
        if path == "/api/nots/nepse-data/market-open":
            return httpx.Response(200, json={"isOpen": self.status, "asOf": "2026-10-04T13:00:00", "id": self.market_id})
        if path == "/api/nots/nepse-data/floorsheet" and request.method == "POST":
            today = now_npt().date().isoformat()
            # Page 1 repeats contract 2 to mimic pages shifting while trades arrive.
            pages = {0: [1, 2], 1: [2, 3]}
            return self.floorsheet(
                request, lambda p: [trade(c, today, 131, "NABIL") for c in pages.get(p, [])], 2)
        if path.startswith("/api/nots/security/floorsheet/") and request.method == "POST":
            sid = int(path.rsplit("/", 1)[1])
            day = request.url.params["businessDate"]
            rows = [] if sid == 2790 else [trade(100 + sid, day, sid, "NABIL")]
            return self.floorsheet(request, lambda p: rows, 1)
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


def trade(contract_id, day, sid, symbol):
    return {"contractId": contract_id, "stockId": sid, "stockSymbol": symbol,
            "buyerMemberId": 58, "sellerMemberId": 34, "contractQuantity": 10,
            "contractRate": 500.0, "contractAmount": 5000.0, "businessDate": day,
            "tradeTime": f"{day}T11:0{contract_id % 10}:00", "tradeBookId": 9000 + contract_id}


@pytest.fixture
def fake_nepse():
    return FakeNepse()


@pytest.fixture
def make_client(fake_nepse):
    def _make(**kw):
        kw.setdefault("rate_per_s", 0)  # no pacing in tests unless asked for
        kw.setdefault("backoff_base_s", 0.01)
        return NepseClient(
            transport=httpx.MockTransport(fake_nepse.handler),
            token_parser=FakeTokenParser(),
            **kw,
        )
    return _make
