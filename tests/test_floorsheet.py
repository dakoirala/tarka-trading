import asyncio
from datetime import date

import pyarrow.parquet as pq

from tarka_md.collector import Collector, Config
from tarka_md.compact import compact_date
from tarka_md.floorsheet import capture_market_floorsheet, capture_security_floorsheets
from tarka_md.schedule import now_npt
from tarka_md.storage import RawStore


def test_market_floorsheet_paginates_and_dedupes(tmp_path, make_client, fake_nepse):
    async def go():
        async with make_client() as c:
            return await capture_market_floorsheet(c, RawStore(tmp_path))

    result = asyncio.run(go())
    assert (result.pages, result.trades, result.failed_pages) == (2, 4, 0)
    assert set(fake_nepse.post_ids) == {fake_nepse.expected_post_id()}

    day = now_npt().date().isoformat()
    stats = compact_date(tmp_path, day)
    assert stats.table_rows["trades"] == 3  # contract 2 seen twice
    rows = pq.read_table(tmp_path / "parquet" / "trades" / f"date={day}" / "part-0.parquet").to_pylist()
    assert [r["contract_id"] for r in rows] == [1, 2, 3]
    assert rows[0]["buyer_broker"] == 58 and rows[0]["business_date"].isoformat() == day


def test_security_floorsheet_backfill_lands_under_business_date(tmp_path, make_client):
    async def go():
        async with make_client() as c:
            return await capture_security_floorsheets(
                c, RawStore(tmp_path), {131: "NABIL", 2790: "NICA"}, date(2025, 1, 5))

    result = asyncio.run(go())
    assert result.trades == 1 and result.empty == ["NICA"]
    stats = compact_date(tmp_path, "2025-01-05")
    assert stats.table_rows["trades"] == 1


def test_floorsheet_captured_after_close(tmp_path, make_client, fake_nepse):
    cfg = Config(data_dir=tmp_path, symbols=["NABIL"], ignore_schedule=True,
                 status_interval_s=0.05, depth_interval_s=0.05, live_interval_s=0.05)

    async def go():
        async with make_client() as client:
            col = Collector(cfg, client)
            runner = asyncio.create_task(col.run())
            await asyncio.sleep(0.2)
            fake_nepse.status = "CLOSE"
            await asyncio.sleep(0.3)
            col.stop()
            await asyncio.wait_for(runner, 2)
            return col

    col = asyncio.run(go())
    assert col.state.floorsheet_on == now_npt().date()
    assert len(list(col.store.read("floorsheet", now_npt().date().isoformat()))) == 2
