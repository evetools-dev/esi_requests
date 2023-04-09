import os
import sqlite3
import time
import yaml
from dataclasses import dataclass

from esi_requests.log import getLogger

logger = getLogger(__name__)


@dataclass(repr=False)
class CMDInfo:
    """Stores statistics for a database keyword, such as SELECT, DELETE, etc."""

    _cmd: str
    _cnt: int = 0
    _t: float = 0.0  # unit: nanosecond

    def __repr__(self) -> str:
        return f"(calls={self._cnt}, time={self._t / 1e9:.7f})"  # 7f because windows time has max 1e-07 resolution


@dataclass(init=False)
class _ESIDBStats:
    """Stores statistics for a database instance."""

    def __init__(self, db_name: str) -> None:
        self.db_name = db_name
        self.calls: int = 0

    def increment(self, cmd: str, _t: float):
        """Increments statistics of the database instance.

        Args:
            cmd: str
                A SQL keyword, such as SELECT or INSERT.
            _t: float
                Time spent for this db command. Unit in nanosecond.
        """
        self.calls += 1

        # If SQL query involves transaction, stored procedure, etc., they probably start with "BEGIN".
        # This is beyond the scope of this class design. You might see something like BEGIN=(calls=1, time=xxx).
        info: CMDInfo = getattr(
            self, cmd, CMDInfo(cmd)
        )  # cmd_info = self.SELECT || CMDInfo("SELECT")
        info._cnt += 1
        info._t += _t
        if not hasattr(self, cmd):
            setattr(self, cmd, info)

    def __repr__(self) -> str:
        nodef_f_vals = ((f, getattr(self, f)) for f in self.__dict__)

        nodef_f_repr = ", ".join(f"{name}={value}" for name, value in nodef_f_vals)
        return f"{self.__class__.__name__}({nodef_f_repr})"


class ESIDBManager:
    """Manages an SQLite database.

    Parameters:
        db_name (str): name of the database. The database file will be named as ``db_name.db``.
    """

    def __init__(self, db_name):
        self.db_name = db_name

        self.db_path = os.path.join(os.path.dirname(__file__), db_name + ".db")
        self.conn = sqlite3.connect(self.db_path)  # can't use isolation_level=None
        self.cursor = self.conn.cursor()

        self.__init_tables()
        self.__init_stats()  # self.tables
        logger.info("Database %s initiated at path %s", db_name, self.db_path)

    def __del__(self):
        self.close()

    @property
    def stats(self) -> _ESIDBStats:
        """Get statistics on database query performance."""
        return self._stats

    def execute(self, __sql: str, __parameters=...) -> sqlite3.Cursor:
        """Executes an SQL statement.
        
        Wraps cursor.execute with custom add-ons.
        Usage is the same (or should be the same) as cursor.execute() method of sqlite3.Cursor class.
        """
        cmd = __sql.split()[0]
        _s = time.perf_counter_ns()  # perf_counter has 1e-07 resolution in win32, lowest in time methods
        if __parameters is Ellipsis:
            cursor = self.cursor.execute(__sql)
        else:
            cursor = self.cursor.execute(__sql, __parameters)
        _t = time.perf_counter_ns() - _s
        self._stats.increment(cmd, _t)
        return cursor

    def commit(self) -> None:
        """Same as connection.commit() from sqlite3.Connection class."""
        self.conn.commit()

    def prepare_table(self, table_name: str, schema: str):
        """Prepares a table by creating it if not exists."""
        if table_name not in self.tables:
            # Read schema from schema.yml
            with open(os.path.join(os.path.dirname(__file__), "schema.yml")) as f:
                schema = yaml.full_load(f).get(schema)
                if schema is None:
                    raise ValueError(f"Schema {schema} not found in schema.yml")

            self.cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({schema});")
            self.tables.append(table_name)
        else:
            logger.debug("Database %s already has table \"%s\"", self.db_name, table_name)

    def clear_table(self, table_name: str):
        """Clears a table using ``DELETE FROM table_name;``"""
        self.cursor.execute(f"DELETE FROM {table_name};")
        self.conn.commit()
        logger.debug("Clear table %s-%s successful", self.db_name, table_name)

    def drop_table(self, table_name: str):
        """Drops a table using ``DROP TABLE table_name;``"""
        self.cursor.execute(f"DROP TABLE IF EXISTS {table_name};")
        self.conn.commit()
        logger.debug("Drop table %s-%s successful", self.db_name, table_name)

    def clear_db(self):
        """Clears every table by calling ``clear_table()``."""
        for table in self.tables:
            self.clear_table(table)
        logger.debug("Clear DB %s successful", self.db_name)

    def close(self):
        """Close the database connection and cursor."""
        self.cursor.close()
        self.conn.close()
    
    def __init_tables(self):
        # with open(os.path.join(os.path.dirname(__file__), "schema.yml")) as f:
        #     dbconfig = yaml.full_load(f)
        # self._dbconfig = dbconfig.get(self.schema)
        # self.tables = self._dbconfig.get("tables")
        # for table in self.tables:
        #     table_config = self._dbconfig.get(table)
        #     schema = table_config.get("schema")
        #     self.cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} ({schema});")
        
        # Get existing table names
        cursor = self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        self.tables = [t[0] for t in cursor.fetchall()]

    def __init_stats(self):
        self._stats: _ESIDBStats = _ESIDBStats(self.db_name)
