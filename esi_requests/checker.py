import json
import os
from functools import wraps
from time import time
from typing import TYPE_CHECKING, Coroutine, Dict

import aiohttp
import pandas as pd
import requests

from .data import CacheDB, SqliteCache
from .data.utils import make_cache_key
from .exceptions import EndpointDownError, InvalidRequestError
from .log import getLogger
from .metadata import ESIMetadata
from .models import ESIRequest

if TYPE_CHECKING:
    from .models import PreparedESIRequest, ESIResponse
    

logger = getLogger(__name__)


def cache_check_request(func: Coroutine):
    """Caches various parameter checkings, such as check_type_id."""
    # func has signature: async def check_{xxx_id}(self, request) -> bool
    @wraps(func)
    async def cache_check_request_wrapped(_self: "ESIRequestChecker", *args, **kwd):
        # Caches _RequestChecker methods
        key = make_cache_key(func, *args, **kwd)
        value = _self.cache.get(key)
        if value is not None:  # cache hit
            return value

        ret = await func(_self, *args, **kwd)  # exec

        expires = 24 * 3600 * 30  # one month
        _self.cache.set(key, ret, expires)
        return ret

    return cache_check_request_wrapped


class _NonOverridable(type):
    """Prevents subclass overriding some methods."""

    __final__ = ["__call__", "__check_request"]  # methods not overridable

    def __new__(cls, __name: str, __bases, __namespace):
        if __bases:
            for finals in cls.__final__:
                if finals in __namespace:
                    raise SyntaxError(f"Overriding {finals} is not allowed")
        return type.__new__(cls, __name, __bases, __namespace)


class ESIRequestChecker(metaclass=_NonOverridable):
    """Checks a request for validity.

    Checks various parameters to avoid errors from ESI.
    The expectation is to completely eliminate 400 and 404 errors in stable state.
    All checkers only check according to some rules, having no feedback loop from ``ESIResponse``.

    User could override individual check methods to customize checking rules.
    ``__call__`` and ``__check_request`` methods are not allowed to override.

    Attributes:
        cache: SqliteCache
            A cache instance to store the check result. If not given, default ``checker_cache`` under ``eve_tools/data/cache.db``.

    Note:
        Individual check methods should be async functions, and should be decorated by ``cache_check_request`` from ``eve_tools.ESI``.
    """
    # It seems like if the parameter to be checked is in path (appears as {xxx_id} in endpoint name),
    # incorrect value would cause a 404 error.
    # Instead, if the parameter is in query, an empty response body would be given (probably  database select returns nothing).
    def __init__(self, cache: SqliteCache = ...) -> None:
        self.enabled = True
        self.raise_flag = False
        self.requests = 0  # just for fun
        self.endpoints_checker = ESIEndpointChecker()

        if cache is Ellipsis:
            self.cache = SqliteCache(CacheDB, "checker_cache")
        else:
            self.cache = cache

        # Reading a .csv.bz2 is costly. Takes 15MB memory and a long time (~0.x second)
        # Retrieve from Fuzzwork if local copy not exists
        static_path = os.path.join(os.path.dirname(__file__), "data", "static")
        if not os.path.isdir(static_path):
            try:
                os.mkdir(static_path)
            except FileExistsError:
                pass
        invTypes_path = os.path.join(static_path, "invTypes.csv.bz2")
        if not os.path.exists(invTypes_path):
            resp = requests.get("https://www.fuzzwork.co.uk/dump/latest/invTypes.csv.bz2")
            with open(invTypes_path, "wb") as f:
                f.write(resp.content)
            
        self.invTypes = pd.read_csv(invTypes_path)

        self.metadata_parser = ESIMetadata()

    async def __call__(self, request: "PreparedESIRequest", raise_flag: bool = False) -> bool:
        if not self.enabled:
            return True

        self.raise_flag = raise_flag
        return await self.__check_request(request)

    async def __check_request(self, request: "PreparedESIRequest") -> bool:
        """Checks if an ESIRequest is valid.

        Checks parameters of an ESIRequest, and predicts if the request is valid.
        Currently, the ESI._check_* family only checks parameters following some rules.
        This means there is no feedback loop from responses.

        Raises:
            InvalidRequestError: raised when request is blocked and ESI.request family sets keyword ``raises = True``.
            EndpointDownError: raised when requested endpoint is down.

        Note:
            This method is not cached, but individual checks might be cached for one month.
        """
        valid = True
        error = None

        metadata = self.metadata_parser(request.endpoint)

        # Check endpoint status
        if valid and self.endpoints_checker.enabled:
            valid = self.endpoints_checker(request.endpoint)
            if not valid:
                error = EndpointDownError(request.endpoint)

        # Check type_id in query
        if valid:
            if "type_id" in request.kwargs:
                type_id = request.kwargs.get("type_id")
            elif "type_id" in request.params:
                type_id = request.params.get("type_id")
            else:
                type_id = None

            # If "type_id" not required by this endpoint, just ignore checking
            if "type_id" in metadata.parameters:  
                if type_id is None and not metadata.parameters["type_id"].required:
                    # sometimes type_id = None is valid, so no check
                    valid = True
                else:  # check
                    valid = await self.check_type_id(type_id)
            if not valid:
                error = InvalidRequestError("type_id", type_id)

        # other tests: if valid and "xxx" in api_request.params:
        if not valid:
            self.__log(request)
            # request.blocked = True
            if self.raise_flag is True and error is not None:
                raise error from None
            else:
                return self.raise_flag

        return valid

    @cache_check_request
    async def check_type_id(self, type_id: int) -> bool:
        """Checks if a type_id is valid.

        Uses type_id from api_request.kwd.
        First checks using SDE, then checks using ESI endpoint if SDE passed.
        This method is independent from api/check and api/search.

        Note:
            This method is cached for one month.
        """
        valid = type_id in self.invTypes["typeID"].values

        if valid is True:
            invType = self.invTypes.loc[self.invTypes["typeID"] == type_id]
            valid = bool(int(invType["published"]))

        if valid is True:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                success = False
                attempts = 3
                while not success and attempts > 0:
                    async with session.get(
                        f"https://esi.evetech.net/latest/universe/types/{type_id}/?datasource=tranquility&language=en",
                    ) as resp:
                        if resp.status == 502:
                            attempts -= 1
                            continue
                        if resp.status == 200:
                            success = True
                        data: dict = await resp.json()
                        self.requests += 1
                        valid = data.get("published")

        return valid

    def __log(self, api_request: ESIRequest):
        logger.warning(
            'BLOCKED - endpoint_"%s": %s',
            api_request.endpoint,
            api_request.kwargs,
        )


class ESIEndpointChecker:
    """Checks status of ESI endpoints.

    Note:
        This method does not follow the ``expires`` field in response header.
        This method retrieves ``status.json`` from ESI every 60 seconds (as ``expires`` headers specified).
    """

    def __init__(self) -> None:
        self.enabled = True
        self.target_url = "https://esi.evetech.net/status.json?version=latest"
        self.fd_path = os.path.join(os.path.dirname(__file__), "data", "status.json")

        if not os.path.exists(self.fd_path) or os.stat(self.fd_path).st_size == 0:
            self.fd = open(self.fd_path, "w")
            self.status_parsed = None
        else:
            self.fd = open(self.fd_path, "r")
            self.status_parsed = json.load(self.fd)

    def __del__(self):
        self.fd.close()

    @property
    def fd_expired(self) -> bool:
        return (self.status_parsed is None or len(self.status_parsed) == 0) or (
            os.path.exists(self.fd_path) and os.path.getmtime(self.fd_path) - time() > 60
        )

    def __call__(self, endpoint: str) -> bool:

        # If no local status.json, or local version expired, retrieve from ESI
        if self.fd_expired:
            resp = requests.get(self.target_url)
            status = resp.json()
            self.status_parsed = self._parse_status_json(status)
            json.dump(self.status_parsed, self.fd)
            self.fd.flush()

        # Now, self.status_parsed has a fresh **parsed** copy of ``status.json``
        return self.status_parsed.get(endpoint, False)

    @staticmethod
    def _parse_status_json(status) -> Dict:
        return {entry["route"]: True if entry["status"] == "green" else False for entry in status}


class FakeResponseGenerator:

    def __init__(self):
        self.cache: Dict[ESIRequest, ESIResponse] = {}

    def __call__(self, *args, **kwargs):
        return self.generate()

    def ready(self, request: "PreparedESIRequest") -> bool:
        return False

    def generate(self, request: "PreparedESIRequest") -> "ESIResponse":
        """Generates a fake response for a given PreparedESIRequest.

        Returns:
            ESIResponse: a fake response for a given request.
        """
        return ESIResponse()
