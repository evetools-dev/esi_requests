"""
esi_requests
~~~~~~~~~~~~

This module provides a simple interface for making HTTP requests to the EVE Online ESI API.

By default, the `Session` class will automatically handle retries for certain error codes, and raise an exception for other error codes. You can customize this behavior by passing a custom error handling function to the `Session` constructor.

This module is not affiliated with or endorsed by CCP Games or EVE Online.

Some docstrings generated by ChatGPT, a large language model trained by OpenAI.

(c) 2023 by Hanbo Guo
"""

import asyncio
from typing import List, Union

import aiohttp
from tqdm.asyncio import tqdm_asyncio

from .checker import ESIRequestChecker, FakeResponseGenerator
from .data.etag_cache import ETagCache
from .models import ESIRequest, ESIResponse, PreparedESIRequest
from .parser import ESIRequestParser


class Session:
    """
    A session class for making requests to ESI API using asyncio.

    Usage:
    ```python
    async with Session() as session:
        response = await session.request("get", "/markets/{region_id}/orders/", region_id=10000002)
        # Use ESIResponse object
    ```

    The session automatically handles request validation, ETag caching, and retrying failed requests.

    Args:
        None.

    Attributes:
        None.
    """

    def __init__(self) -> None:
        self.__request_checker = ESIRequestChecker()
        self.__request_parser = ESIRequestParser()
        self.__etag_cache = ETagCache()
        self.__request_generator = FakeResponseGenerator()

        self.__async_session = None
        # self.__async_event_loop = asyncio.get_event_loop()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def request(
        self, method: str, endpoint: str, **kwargs
    ) -> Union["ESIResponse", List["ESIResponse"]]:
        """Sends a request to the ESI API and returns one or more ``ESIResponse`` objects.

        Args:
            method (str): The HTTP method to use (e.g. "GET", "POST").
            endpoint (str): The endpoint to send the request to (e.g. "/characters/{character_id}/assets/").
            **kwargs: Optional arguments to pass to the request, such as query parameters.

        Returns:
            Either a single ``ESIResponse`` object, or a list of ``ESIResponse`` objects if multiple requests were issued.

        Note:
            This method automatically handles request validation, ETag caching, and retrying failed requests.
        """
        headers = kwargs.pop("headers", {})
        params = kwargs.pop("params", {})

        request = ESIRequest(endpoint=endpoint, method=method, params=params, headers=headers, **kwargs)

        preps = self.prepare_request(request)

        result = await self.issue(preps)
        if len(result) == 1:
            return result[0]
        return result

    def prepare_request(self, request: "ESIRequest") -> List["PreparedESIRequest"]:
        """Prepares an ``ESIRequest`` for sending to the ESI API.

        Returns:
            A list of ``PreparedESIRequest`` objects, which can be passed to ``Session.issue()``.
        """
        requests = self.__request_parser(request)

        # Update ETag headers
        # see https://developers.eveonline.com/blog/article/esi-etag-best-practices.
        for req in requests:
            if "If-None-Match" not in req.headers:
                req.headers["If-None-Match"] = self.__etag_cache.get(req.url, "")

        request.prepared = True
        return requests

    async def issue(self, requests: List["PreparedESIRequest"]) -> List["ESIResponse"]:
        """Issues one or more prepared requests to the ESI API.

        Returns:
            List[ESIResponse]: A list of ``ESIResponse`` objects corresponding to each prepared request.

        Raises:
            ValueError: If a request in the provided list is not an instance of PreparedESIRequest.
        """
        if not isinstance(requests[0], PreparedESIRequest):
            raise ValueError(
                "You can only issue PreparedESIRequest. Use Session.prepare_request() before issue."
            )

        if self.__async_session is None:
            self.__async_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))

        tasks = [asyncio.create_task(self.issue_one_request(request)) for request in requests]
        await tqdm_asyncio.gather(*tasks)  # progress bar

        return [task.result() for task in tasks]

    async def issue_one_request(self, request: "PreparedESIRequest") -> "ESIResponse":
        """Issues a single prepared request to the ESI API and returns the corresponding ``ESIResponse``.

        Args:
            request (``PreparedESIRequest``): The prepared request to send to the ESI API.

        Returns:
            ESIResponse: The ``ESIResponse`` corresponding to the request.

        This asynchronous method first checks if the request would cause an error on the ESI side.
        If the request would cause an error, an ``ESIResponse`` object will be generated as if the request is sent to ESI.
        Otherwise, the method will send the request, and cache the ``ESIResponse`` if necessary.
        """
        # Checks if request would cause error on ESI side
        valid = await self.__request_checker(request)

        if not valid and self.__request_generator.ready(request):
            # If the request would cause error, the request would not be sent to ESI.
            # But still, a ESIResponse will be generated, and this request "appears" to be sent to ESI.
            resp: "ESIResponse" = self.__request_generator.generate()

        else:
            # First send the request, then check for ETag
            r = await self.send(request)

            if r.status == 304:
                cache_line = self.__etag_cache.get(request.url)

                if cache_line is None:
                    # TODO: I don't know how to handle cache miss when 304,
                    # if someone could help it would be appreciated
                    raise NotImplementedError(
                        "Not sure how to handle this situation... Please create a Issue on GitHub."
                    )

                resp = cache_line.response

            else:
                resp = await self.build_response(request, r)
                if resp.ok:
                    self.cache_response(resp)

        return resp

    async def send(self, request: "PreparedESIRequest", **kwargs):
        """Sends a prepared request to the ESI API."""
        if isinstance(request, ESIRequest):
            raise ValueError("You can only send ESIRequest that is prepared by Session.prepare.")

        async with self.__async_session.request(
            request.method, request.url, headers=request.headers
        ) as resp:
            await resp.read()
            return resp

    async def build_response(self, request: "ESIRequest", r: "aiohttp.ClientResponse") -> "ESIResponse":
        """Builds an ``ESIResponse`` object from an ``aiohttp.ClientResponse`` object."""
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
        """Caches an ``ESIResponse`` object in the ETag cache."""
        etag = resp.headers.get("ETag", "*")
        expires = resp.headers.get("Expires", 24 * 3600)  # 1 day
        self.__etag_cache.set(resp.url, etag=etag, response=resp, expires=expires)

    async def close(self):
        if self.__async_session is None:
            return

        await self.__async_session.close()

        # if not self.__async_session.closed:
        #     if self.__async_session._connector_owner:
        #         self.__async_session._connector._close()  # silence deprecation warning
        #     self.__async_session._connector = None

        # if not self.__async_event_loop.is_closed():
        #     self.__async_event_loop.run_until_complete(asyncio.sleep(0))
        #     self.__async_event_loop.close()
