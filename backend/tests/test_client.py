import asyncio

import httpx
import pytest

from app.plk.client import PlkClient, PlkError, PlkRateLimited


def make(handler):
    return PlkClient("sk_test", "https://plk.test", transport=httpx.MockTransport(handler), retry_delays=(0, 0))


def test_sends_api_key_and_paginates():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        page = int(req.url.params["page"])
        return httpx.Response(200, json={"trains": [], "pagination": {"hasNextPage": page < 3}},
                              headers={"X-RateLimit-Remaining": str(100 - page)})

    res = asyncio.run(make(handler).operations([30601, 80416]))
    assert len(res.body) == 3 and res.requests == 3
    assert res.limits == {"x-ratelimit-remaining": "97"}
    r = seen[0]
    assert r.headers["X-API-Key"] == "sk_test"
    assert r.url.path == "/api/v1/operations"
    assert r.url.params["stations"] == "30601,80416"
    assert r.url.params["withPlanned"] == "true"


def test_schedule_params():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"routes": []})

    from datetime import date
    asyncio.run(make(handler).schedules(30601, 80416, date(2026, 10, 23), date(2026, 10, 25)))
    p = seen[0].url.params
    assert (p["fromStations"], p["toStations"], p["dateFrom"], p["dateTo"]) == ("30601", "80416", "2026-10-23", "2026-10-25")


def test_rate_limit_raises_with_retry_after():
    with pytest.raises(PlkRateLimited) as exc:
        asyncio.run(make(lambda r: httpx.Response(429, headers={"Retry-After": "120"})).carriers())
    assert exc.value.retry_after == 120


def test_retries_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={"carriers": []})

    res = asyncio.run(make(handler).carriers())
    assert res.requests == 3


def test_4xx_is_error_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(PlkError, match="HTTP 401"):
        asyncio.run(make(handler).carriers())
    assert len(calls) == 1


def test_requires_key():
    with pytest.raises(PlkError):
        PlkClient("", "https://plk.test")
