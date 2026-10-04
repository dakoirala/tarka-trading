"""Raw capture storage.

Every HTTP exchange is appended verbatim to gzip'd JSON Lines, partitioned by stream,
NEPSE trade date and hour. This is the source of truth: normalized tables are derived
from it (see ``compact.py``) and can be rebuilt whenever the parsing logic changes.

Layout: ``<root>/raw/<stream>/date=YYYY-MM-DD/<stream>-HH.jsonl.gz``. The date is the NPT
date the response arrived, unless a record carries ``partition_date`` (floorsheets use
their business date so backfilled history lands under the right day). Each write appends
a new gzip member, which standard gzip readers concatenate transparently.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .client import Fetch
from .schedule import ns_to_npt

STREAMS = ("market_status", "securities", "live_market", "depth", "floorsheet")


def fetch_record(stream: str, fetch: Fetch, **extra: Any) -> dict[str, Any]:
    return {
        "stream": stream,
        "req_ns": fetch.req_ns,
        "recv_ns": fetch.recv_ns,
        "path": fetch.path,
        "status": fetch.status,
        "error": fetch.error,
        **extra,
        "body": fetch.body,
    }


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, stream: str, recv_ns: int, partition_date: str | None = None) -> Path:
        ts = ns_to_npt(recv_ns)
        day = partition_date or ts.date().isoformat()
        return self.root / "raw" / stream / f"date={day}" / f"{stream}-{ts.hour:02d}.jsonl.gz"

    def write(self, records: Iterable[dict[str, Any]]) -> int:
        grouped: dict[Path, list[str]] = defaultdict(list)
        for rec in records:
            grouped[self.path_for(rec["stream"], rec["recv_ns"], rec.get("partition_date"))].append(
                json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
            )
        for path, lines in grouped.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "at", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return sum(len(v) for v in grouped.values())

    def read(self, stream: str, date: str) -> Iterator[dict[str, Any]]:
        day_dir = self.root / "raw" / stream / f"date={date}"
        for path in sorted(day_dir.glob(f"{stream}-*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # A crash mid-write can truncate the last line of a member.
                        continue
