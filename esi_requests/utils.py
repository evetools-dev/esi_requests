from dataclasses import dataclass
from itertools import product
from typing import Any, List, Optional

from .__version__ import __version__


def default_headers():
    return {
        "User-Agent": f"python-esi_requests/{__version__}",
    }


def cross_product(**kwargs):
    keys = kwargs.keys()
    vals = kwargs.values()
    for instance in product(*vals):
        yield dict(zip(keys, instance))


@dataclass
class Param:
    """Hold a Param for an API

    Attributes:
        name: A str for the name of the param. E.g. "character_id"
        _in: A str for the occurance of the param. One of ["path", "query", "header"]
        required: A bool for requirement.
        dtype: A str for data type of the param.
        default: Default value for the Param.
    """

    name: str  # characther_id
    _in: str  # "path" / "query" / "header" / "body"
    required: bool
    dtype: type  # "string" / "integer" / "array" / "boolean" / "" (schema)
    default: Optional[Any] = None


@dataclass
class ESIParams:
    """A list like datastructure of Param(s).

    Attributes:
        params: A list of Param instance.
    """

    # list of ESI pre-defined meta parameters, initialized at the start
    # Param.name: #/parameters/{actual_name}
    params: List[Param]

    def append(self, param: Param) -> None:
        """Append a Param similar to list append.
        No value checking. Used for testing.
        """
        self.params.append(param)

    def __getitem__(self, name: str) -> Param:
        """Returns a reference of Param with name.
        If no Param with name found, return None
        """
        for p in self.params:
            if p.name == name:
                return p

        return None

    def __contains__(self, name: str) -> bool:
        """Returns True if Param with ``name`` exists."""
        for p in self.params:
            if p.name == name:
                return True

        return False

    def __iter__(self):
        yield from self.params
