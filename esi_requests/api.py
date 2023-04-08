"""
esi_requests.api
~~~~~~~~~~~~~~~~

This module provides simple interface for user.

(c) 2023 by Hanbo Guo
"""
import asyncio
import sys
from typing import TYPE_CHECKING, List, Union

from .sessions import Session

if TYPE_CHECKING:
    from .models import ESIResponse


async def arequest(method: str, endpoint: str, **kwargs):
    """Async version of `request`"""
    async with Session() as session:
        return await session.request(method, endpoint, **kwargs)


def request(method: str, endpoint: str, **kwargs) -> Union["ESIResponse", List["ESIResponse"]]:
    """Make a request to ESI API. 
    
    Uses asyncio.run to run the async function `arequest`.
    """
    # To get away with the error: RuntimeError('Event loop is closed')
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    r = asyncio.run(arequest(method, endpoint, **kwargs))
    return r


def get(endpoint: str, params=None, **kwargs):
    """Make a GET request to ESI API."""
    return request("get", endpoint, params=params, **kwargs)


def head(endpoint: str, params=None, **kwargs):
    """Make a HEAD request to ESI API."""
    return request("head", endpoint, params=params, **kwargs)
