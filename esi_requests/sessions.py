import asyncio
from typing import List, Union

import aiohttp
from tqdm.asyncio import tqdm_asyncio

from .checker import ESIRequestChecker
from .data.etag_cache import ETagCache
from .models import ESIRequest, ESIResponse, PreparedESIRequest
from .parser import ESIRequestParser


class Session:
    
    def __init__(self) -> None:
        self.__request_checker = ESIRequestChecker()
        self.__request_parser = ESIRequestParser()
        self.__etag_cache = ETagCache()

        self.__async_session = None
        self.__async_event_loop = asyncio.get_event_loop()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method: str, endpoint: str, **kwargs) -> Union["ESIResponse", List["ESIResponse"]]:
        
        headers = kwargs.pop("headers", {})
        params = kwargs.pop("params", {})

        request = ESIRequest(endpoint=endpoint, method=method, params=params, headers=headers, **kwargs)

        preps = self.prepare_request(request)
        
        result = self.__async_event_loop.run_until_complete(self.issue(preps))
        if len(result) == 1:
            return result[0]
        return result

    def prepare_request(self, request: "ESIRequest") -> List["PreparedESIRequest"]:
        requests = self.__request_parser(request)

        # Update ETag headers
        # see https://developers.eveonline.com/blog/article/esi-etag-best-practices.
        for req in requests:
            if "If-None-Match" not in req.headers:
                req.headers["If-None-Match"] = self.__etag_cache.get(req.url, "")

        return requests
    
    async def issue(self, requests: List["PreparedESIRequest"]):
        if not isinstance(requests[0], PreparedESIRequest):
            raise ValueError("You can only issue PreparedESIRequest. Use Session.prepare_request() before issue.")

        if self.__async_session is None:
            self.__async_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        
        tasks = [asyncio.create_task(self.issue_one_request(request)) for request in requests]
        await tqdm_asyncio.gather(*tasks)

        return [task.result() for task in tasks]
        
    
    async def issue_one_request(self, request: "PreparedESIRequest"):
        # Checks if request would cause error on ESI side
        valid = await self.__request_checker(request)

        if not valid:
            # If the request would cause error, the request would not be sent to ESI.
            # But still, a ESIResponse will be generated, 
            # and this request "appears" to be sent to ESI.
            resp: "ESIResponse" = self.__request_checker.generate_fake_response()
        
        else:
            # First send the request, then check for ETag
            r = await self.send(request)

            if r.status == 304:
                cache_line = self.__etag_cache.get(request.url)

                if cache_line is None:
                    # TODO: I don't know how to handle cache miss when 304,
                    # if someone could help it would be appreciated
                    raise NotImplementedError("Not sure how to handle this situation... Please create a Issue on GitHub.")
                
                resp = cache_line.response
            
            else:
                resp = await self.build_response(request, r)
                if resp.ok:
                    self.cache_response(resp)

        return resp

    async def send(self, request: "PreparedESIRequest", **kwargs):
        if isinstance(request, ESIRequest):
            raise ValueError("You can only send ESIRequest that is prepared by Session.prepare.")

        async with self.__async_session.request(
            request.method, request.url, headers=request.headers
        ) as resp:
            await resp.read()
            return resp

    async def build_response(self, request: "ESIRequest", r: "aiohttp.ClientResponse") -> "ESIResponse":
        resp = ESIResponse()

        # aiohttp always gives a status
        resp.status = r.status

        resp.headers = dict(r.headers)  # ETag and Expires live in this headers
        resp.reason = r.reason
        resp.url = r.url
        resp.request_info = request

        # when the response content expires, specified by ESI
        resp.expires = resp.headers.get("Expires")

        resp.text = await r.text()

        return resp

    def cache_response(self, resp: "ESIResponse"):
        etag = resp.headers.get("ETag", "*")
        expires = resp.headers.get("Expires", 24 * 3600)  # 1 day
        self.__etag_cache.set(resp.url, etag=etag, response=resp, expires=expires)

    def close(self):
        if self.__async_session is None:
            return

        if not self.__async_session.closed:
            if self.__async_session._connector_owner:
                self.__async_session._connector._close()  # silence deprecation warning
            self.__async_session._connector = None

        if not self.__async_event_loop.is_closed():
            self.__async_event_loop.run_until_complete(asyncio.sleep(0))
            self.__async_event_loop.close()
