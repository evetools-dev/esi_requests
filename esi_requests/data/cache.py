"""Implementation ideas referenced from ESIPy under BSD-3-Clause License"""
import atexit
import inspect
import pickle
from email.utils import parsedate
from datetime import datetime, timedelta
from typing import Union

from .utils import _CacheRecordBaseClass, _CacheRecord, InsertBuffer, _DeleteHandler, hash_key
from .db import ESIDBManager
from esi_requests.log import getLogger

logger = getLogger(__name__)


class BaseCache(_CacheRecordBaseClass):
    """Specifies BaseCache object used by other caching implimentation.

    Requires set and get method for api(s) to access the cache.
    evict is useful for testing. Expired entries should be deleted in get or set,
    so user would not be required to call evict all the time.
    User of BaseCache should call super().__init__() to register instance under cache stats.
    """

    def __init__(self, esidb: ESIDBManager, table: str):
        self.__class__.instances.add(self)
        module = inspect.getmodule(inspect.stack(0)[2][0])
        if module is not None:
            module = module.__name__
        self._record = _CacheRecord(esidb.db_name, table, module)

    def set(self, key, value, expires):
        raise NotImplementedError

    def get(self, key, default):
        raise NotImplementedError

    def evict(self, key):
        raise NotImplementedError

    @property
    def record(self):
        return self._record

    @property
    def hits(self):
        return self._record.hits

    @property
    def miss(self):
        return self._record.miss

    @hits.setter
    def hits(self, val):
        self._record.hits = val

    @miss.setter
    def miss(self, val):
        self._record.miss = val


class SqliteCache(BaseCache):
    """A Sqlite implementation of cache.

    This cache has a key-value interface.
    Specifically, all table has:
    1. ``key``: any ``pickle``able object
    2. ``value``: an arbitrary object serialized by ``pickle.dumps``
    3. ``expires``: retrieved from ``Expires`` headers of HTTP response

    This cache provides a get/set interface.
    Each ``cache.set`` evicts expired cache lines.

    Note:
        This cache does not delete cache line immediately when ``expires`` times is reached.
        It deletes within 5 minutes after this ``expires`` time is reached.
    """

    def __init__(self, name: str, table: str):
        self.cache_name = name
        self.table = table

        self.db = ESIDBManager(name)
        self.buffer = InsertBuffer(self.db)
        self.deleter = _DeleteHandler(self.db, self.table)

        self.db.prepare_table(table, schema="cache")
        atexit.register(self.buffer.flush)
        atexit.register(self.deleter.save)

        self._last_used = None  # used for testing
        super().__init__(self.db, table)

        logger.info("SqliteCache initiated at database %s and table %s", self.db.db_name, table)

    def set(self, key, value, expires: Union[str, int] = 1200):
        """Sets k/v cache line.

        Args:
            key: any ``pickle``able object, e.g. "abcdefg"
            value: any ``pickle``able object
            expires: ``str`` or ``int``. Default expire in 1200 seconds.
                If a ``str`` is provided, it should be in RFC7231 format.
                E.g. "Tue, 11 Jan 2022 10:17:57 GMT"

                If an ``int`` is provided, it should be a number in seconds, e.g. 1200 for 20 minutes.                
        """
        if isinstance(expires, int):
            expires = datetime.utcnow().replace(microsecond=0) + timedelta(seconds=expires)
        else:
            expires = datetime(*parsedate(expires)[:6])

        _h = hash_key(key)
        entry = (_h, pickle.dumps(value), expires)
        self.buffer.insert(entry, self.table)
        self.deleter.update(expires)
        logger.debug("Cache entry set: %s", _h)

    def get(self, key, default=None):
        """Gets the value from cache.

        Attempts to return value with key from cache. A default value is returned if unsuccessful.
        If the selected cache line is expired, delete this cache line.

        Args:
            key: any Python pickle(able) object, e.g. string
            default: If cache returns nothing, returns a default value. Default None.
        """
        _h = hash_key(key)
        
        # First select from buffer, then from database
        row = self.buffer.select(_h)  # hash should be unique, so no need use table parameter
        if not row:
            # should use fetchall and select the newest
            row = self.db.execute(f"SELECT * FROM {self.table} WHERE key=?", (_h,)).fetchone()
        if not row:
            logger.debug("Cache MISS: %s", _h)
            self.miss += 1
            return default

        expires = row[2]
        if isinstance(expires, str):  # expires selected from DB is str, selected from buffer is datatime
            expires = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")

        if datetime.utcnow() > expires:
            logger.debug("Cache EXPIRED: %s", _h)
            self.miss += 1
            self.db.execute(f"DELETE FROM {self.table} WHERE key=?", (_h,))
            self.db.commit()
            return default  # expired
        else:
            self._last_used = _h
            logger.debug("Cache HIT: %s", _h)
            self.hits += 1
            return pickle.loads(row[1])  # value

    def evict(self, key):
        """Deletes cache entry with key. Useful in testing."""
        _h = hash_key(key)
        self.db.execute(f"DELETE FROM {self.table} WHERE key=?", (_h,))
        self.db.commit()
        logger.debug("Cache entry evicted: %s", _h)
