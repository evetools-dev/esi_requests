# esi_requests

esi_requests tries to use **requests** style methods:

```python
>>> import esi_requests
>>> r = esi_requests.get("/markets/{region_id}/orders/", region_id=10000002, type_id=1403)
>>> r.status
200
>>> r.json()
{[{'duration': 90, 'is_buy_order': False, ...}
```

with **async** enabled and simplified:

```python
>>> resps = esi_requests.get("/markets/{region_id}/orders/", region_id=10000002, type_id=[1403, 12005, 626])
# equivalent to: esi_requests.get("/markets/{region_id}/orders/", region_id=10000002, params={"type_id": [1403, 12005, 626]})
>>> resps
[<Response [200]>, <Response [200]>, <Response [200]>]
>>> resps[0].status
200
>>> resps[0].url
'https://esi.evetech.net/latest/markets/10000002/orders/?datasource=tranquility&order_type=all&page=1&type_id=1403'
```

which internally uses *aiohttp* to send requests asynchronously. 

### Features

* One-line `async` enabled: no need to master *aiohttp* and *asyncio*
* Simple `requests`-like api
* Simplified `OAuth2` SSO authentication: all you need is to log in your account
* Support `ETag` headers: compliant with [ESI recommendation](https://developers.eveonline.com/blog/article/esi-etag-best-practices)
