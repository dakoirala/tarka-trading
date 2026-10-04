import asyncio


def test_token_refresh_on_401(make_client, fake_nepse):
    async def go():
        async with make_client() as c:
            first = await c.market_status()
            fake_nepse.expire_next = True
            second = await c.market_status()
            return first, second

    first, second = asyncio.run(go())
    assert first.ok and second.ok
    assert fake_nepse.token_n == 2
    assert second.body["isOpen"] == "OPEN"


def test_5xx_is_retried_then_reported(make_client, fake_nepse):
    fake_nepse.fail_depth_ids = {131}

    async def go():
        async with make_client(max_retries=1) as c:
            return await c.market_depth(131)

    f = asyncio.run(go())
    assert not f.ok and f.status == 503 and f.error == "HTTP 503"
    assert fake_nepse.calls.count("/api/nots/nepse-data/marketdepth/131/") == 2


def test_bad_token_response_is_reported_not_raised(make_client, fake_nepse):
    import httpx

    original = fake_nepse.handler

    def handler(request):
        if request.url.path == "/api/authenticate/prove":
            return httpx.Response(200, text="<html>maintenance</html>")
        return original(request)

    fake_nepse.handler = handler

    async def go():
        async with make_client(max_retries=0) as c:
            return await c.market_status()

    f = asyncio.run(go())
    assert not f.ok and f.error.startswith("token:")
