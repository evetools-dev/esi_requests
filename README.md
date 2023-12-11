[![GitHub tag](https://img.shields.io/badge/evetools--dev-esi__requests-blue)](https://github.com/evetools-dev/esi_requests)
[![PyPI Latest Release](https://img.shields.io/pypi/v/esi__requests.svg)](https://pypi.org/project/esi-requests/)

# What is it?
**<big>esi_requests</big>** wraps EVE-Online's [ESI API](https://esi.evetech.net/ui/) with **requests** style methods:

```python
import esi_requests

r = esi_requests.get("/markets/{region_id}/orders/", region_id=10000002, type_id=1403)
print(r.status)  # 200
print(r.json())  # {[{'duration': 90, 'is_buy_order': False, ...}
```

with **asyncio** enabled and simplified:

```python
import esi_requests

resps = esi_requests.get("/markets/{region_id}/orders/", region_id=10000002, type_id=[1403, 12005, 626])
print(resps)  # [<Response [200]>, <Response [200]>, <Response [200]>]
print(resps[0].status)  # 200
print(resps[0].url)  # https://esi.evetech.net/latest/markets/10000002/orders/?datasource=tranquility&order_type=all&page=1&type_id=1403
```

which internally uses *aiohttp* to send requests asynchronously. This is equivalent to:

```python
import asyncio
import aiohttp

async def request(region_id, type_ids):
    url = "https://esi.evetech.net/latest/markets/{region_id}/orders/?datasource=tranquility&order_type=all&page=1&type_id={type_id}"
    async with aiohttp.ClientSession() as session:
        async for type_id in type_ids:
            async with session.get(url.format(region_id=region_id, type_id=type_id)) as resp:
                print(resp.status)  # 200
                print(await resp.json())  # {[{'duration': 90, 'is_buy_order': False, ...}
                pages = int(resp.headers["X-Pages"])
                async for page in range(2, pages + 1):
                    async with session.get(url.format(region_id=region_id, type_id=type_id) + f"&page={page}") as resp:
                        print(resp.status)  # 200
                        print(await resp.json())  # {[{'duration': 90, 'is_buy_order': False, ...}

region_id = 10000002
type_id = [1403, 12005, 626]
loop = asyncio.get_event_loop()
loop.run_until_complete(request(region_id, type_id))
```

# Why use it?

One word: **simplicity**.

You don't need to read ESI documentations for hours, or know anything about *aiohttp* or *asyncio*, or anything about *OAuth2*. All you need is to log in your account, and you are ready to enjoy this **simple** and **fast** API wrapper. 

# Where to get it?

The source code is currently hosted on GitHub at: [https://github.com/evetools-dev/esi_requests/](https://github.com/evetools-dev/esi_requests/)

Installation available via `pip`:

```bash
pip install esi-requests
```

# Features

* One-line `async` enabled: no need to master *aiohttp* and *asyncio*
* Simple `requests`-like api
* Simplified `OAuth2` SSO authentication: all you need is to log in your account
* Support `ETag` headers: compliant with [ESI recommendation](https://developers.eveonline.com/blog/article/esi-etag-best-practices)
