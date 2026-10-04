"""NEPSE access-token de-obfuscation.

``GET /api/authenticate/prove`` returns an ``accessToken`` with five junk characters
inserted at positions derived from ``salt1..salt5`` by functions in a WASM module that
ships with nepalstock.com's frontend. The community ``nepse`` package bundles that module;
we use it for this one step only so that everything else (timing, retries, storage) stays
under our control. If NEPSE rotates the scheme, this is the file to fix.
"""

from __future__ import annotations

from typing import Any, Protocol


class TokenParser(Protocol):
    def parse_token_response(self, token_response: dict[str, Any]) -> tuple[str, str]: ...


def default_token_parser() -> TokenParser:
    try:
        from nepse.TokenUtils import TokenParser as _WasmTokenParser
    except ImportError as exc:  # pragma: no cover - depends on install
        raise RuntimeError(
            "The 'nepse' package (NepseUnofficialApi) is required for NEPSE token parsing; "
            "install this project with `pip install -e .`"
        ) from exc
    return _WasmTokenParser()


def parse_prove_response(parser: TokenParser, body: dict[str, Any]) -> tuple[str, list[int]]:
    """Return the usable access token and integer salts from a /prove response."""
    normalized = dict(body)
    salts = [int(body[f"salt{i}"]) for i in range(1, 6)]
    for i, salt in enumerate(salts, start=1):
        normalized[f"salt{i}"] = salt
    access_token, _refresh = parser.parse_token_response(normalized)
    return access_token, salts


def _dummy_data() -> list[int]:
    """The 100-entry lookup table NEPSE's frontend uses to derive POST body ids."""
    import json
    from importlib.resources import files

    return json.loads(files("nepse").joinpath("data/DUMMY_DATA.json").read_text())


def floorsheet_payload_id(
    market_id: int, salts: list[int], day_of_month: int, dummy_data: list[int] | None = None
) -> int:
    """POST ``{"id": ...}`` value expected by the floorsheet endpoints.

    ``market_id`` is the ``id`` from market-open, ``salts`` are salt1..salt5 from the
    current token, ``day_of_month`` is today's day in Nepal time. Ported from
    NepseUnofficialApi's ``getPOSTPayloadIDForFloorSheet``.
    """
    table = dummy_data if dummy_data is not None else _dummy_data()
    e = table[market_id % len(table)] + market_id + 2 * day_of_month
    idx = 1 if e % 10 < 4 else 3
    return e + salts[idx] * day_of_month - salts[idx - 1]
