import asyncio

import pyarrow.parquet as pq

from tarka_md.collector import Collector, Config, select_universe
from tarka_md.compact import compact_date
from tarka_md.schedule import now_npt
from conftest import load


def test_select_universe_filters():
    secs = load("securities.json")
    assert select_universe(secs, None) == {131: "NABIL", 2790: "NICA"}
    assert select_universe(secs, ["nica", "MISSING"]) == {2790: "NICA"}


def test_once_then_compact(tmp_path, make_client, fake_nepse):
    fake_nepse.fail_depth_ids = {2790}
    cfg = Config(data_dir=tmp_path, once=True)

    async def go():
        async with make_client(max_retries=0) as client:
            await Collector(cfg, client).run()

    asyncio.run(go())
    day = now_npt().date().isoformat()
    stats = compact_date(tmp_path, day)

    assert stats.raw_records == {"depth": 2, "live_market": 1}
    assert stats.failed_requests == {"depth": 1, "live_market": 0}
    assert stats.table_rows == {"l2_depth": 5, "l1_bbo": 1, "l1_trades": 2}

    bbo = pq.read_table(tmp_path / "parquet" / "l1_bbo" / f"date={day}" / "part-0.parquet").to_pylist()
    assert bbo[0]["symbol"] == "NABIL" and bbo[0]["bid_price"] == 500.5
    assert str(bbo[0]["recv_ts"].tzinfo) == "UTC"


def test_loop_stops_cleanly(tmp_path, make_client):
    cfg = Config(data_dir=tmp_path, ignore_schedule=True, depth_interval_s=0.05,
                 live_interval_s=0.05, status_interval_s=0.05)

    async def go():
        async with make_client() as client:
            col = Collector(cfg, client)
            runner = asyncio.create_task(col.run())
            await asyncio.sleep(0.4)
            col.stop()
            await asyncio.wait_for(runner, 2)
            return col

    col = asyncio.run(go())
    depth = list(col.store.read("depth", now_npt().date().isoformat()))
    assert len(depth) >= 4  # several sweeps of 2 symbols
