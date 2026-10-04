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
