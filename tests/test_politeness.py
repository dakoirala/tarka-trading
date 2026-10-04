import asyncio
import time

from tarka_md.client import RateLimiter


def test_rate_limiter_spaces_requests():
    async def go():
        limiter = RateLimiter(20)  # 50 ms apart
        start = time.monotonic()
        await asyncio.gather(*(limiter.acquire() for _ in range(5)))
        return time.monotonic() - start

    assert asyncio.run(go()) >= 0.19


def test_429_retry_after_pauses_then_succeeds(make_client, fake_nepse):
    async def go():
        async with make_client() as c:
            await c.market_status()  # warm the token
            fake_nepse.throttle_next = 0.3
            start = time.monotonic()
            f = await c.market_status()
            return f, time.monotonic() - start

    f, elapsed = asyncio.run(go())
    assert f.ok and elapsed >= 0.3


def test_honest_user_agent(make_client, fake_nepse, monkeypatch):
    monkeypatch.setenv("TARKA_MD_CONTACT", "ops@example.com")
    seen = {}
    original = fake_nepse.handler

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        return original(request)

    fake_nepse.handler = handler

    async def go():
        async with make_client() as c:
            await c.market_status()

    asyncio.run(go())
    assert seen["ua"].startswith("tarka-md/") and "ops@example.com" in seen["ua"]
