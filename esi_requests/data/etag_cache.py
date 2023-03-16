from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union

from .cache import SqliteCache
from .db import ESIDBManager

if TYPE_CHECKING:
    from esi_requests.models import ESIResponse


@dataclass
class _ETagCacheEntry:
    etag: str
    response: "ESIResponse"

class ETagCache:

    def __init__(self) -> None:
        self.db = ESIDBManager("request_cache", schema="cache")
        self.cache = SqliteCache(self.db, table="etag")
    
    def get(self, url: str, default: Any = None) -> "_ETagCacheEntry":
        return self.cache.get(url, default)

    def set(self, url: str, etag: str, response: "ESIResponse", expires: Union[str, int]):
        entry = _ETagCacheEntry(etag, response)
        self.cache.set(url, entry, expires)
