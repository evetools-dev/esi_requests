import json
import os
from dataclasses import dataclass
from typing import List

import requests

from esi_requests.log import getLogger

from .utils import ESIParams, Param

logger = getLogger(__name__)


@dataclass
class EndpointMetadata:
    endpoint: str = None

    # List of methods that this endpoint supports: ["get", "head", ...]
    supported_methods: List[str] = None

    # List of parameters accepted by the endpoint
    parameters: ESIParams = None

    # Required OAuth scope for this endpoint
    sso_scope: List[str] = None


class ESIMetadata:
    """Parses Swagger matadata of ESI."""

    PARAM_DEFAULT = {"order_type": "all"}
    METADATA_PATH = os.path.join(os.path.dirname(__file__), "metadata.json")

    def __init__(self):
        # Swagger field: defines endpoints
        self.paths = None

        # Swagger field: defines OAuth scopes
        self.securityDefinitions = None

        # Swagger field: defines accepted parameters
        self.parameters = None

        self.__load_metadata()

    def __call__(self, endpoint: str) -> EndpointMetadata:
        """Extract information of an endpoint from Swagger metadata.

        Parse and find metadata entry with the given key.
        Assume each metadata entry is ONE of GET, POST.
        Some ESI API has DELETE or PUT, such as /characters/{character_id}/contacts/,
        but they tend to be trivial in market data analysis, so they are not supported.

        Returns:
            An ESIRequest instance with request_key = key.

        Raises:
            KeyError: key is not a valid request type.
        """
        if not endpoint in self.paths.keys():
            raise KeyError(f"{endpoint} is not a valid request type.")

        accepted_methods = list(self.paths[endpoint].keys())
        
        # Some endpoints have more than one accepted request methods,
        # and each request methods have its own parameters and sso scopes requirement.
        # But most useful endpoints have only one request method.
        # For simplicity, only take the information of the first request method.

        parameters = self.parse_parameters(endpoint)
        security = self.parse_security(endpoint)

        return EndpointMetadata(endpoint, accepted_methods, parameters, security)

    def parse_parameters(self, endpoint: str) -> ESIParams:
        """Extract what parameters are accepted for an endpoint.

        Returns:
            An ESIParams instance containing all accepted parameters for the endpoint.
        """
        accepted_methods = list(self.paths[endpoint].keys())
        body = self.paths[endpoint][accepted_methods[0]]

        parameters = body["parameters"]
        params = []
        for param in parameters:
            # check for {$ref : #/parameters/xxx} type param
            # Parameters defined in metadata["parameters"] has key pattern: "$ref/parameters/xxx".
            # Ignore parameters with $ref/parameters signature but not in metadata["parameters"] field.
            metaparam = param.get("$ref", "")  # $ref for meta parameters
            if metaparam:
                param_ = self.parameters[metaparam.split("/")[-1]]
                if param_:
                    params.append(param_)
                continue

            # Construct Param class
            # Param.default is only present in meta parameters.
            # Some params should have default value but absent in meta parameters,
            # which are further defined in PARAM_DEFAULT dictionary.
            params.append(
                Param(
                    name=param["name"],
                    _in=param["in"],
                    required=param.get("required", False),
                    dtype=param.get("type", ""),
                    default=self.PARAM_DEFAULT.get(param["name"]),
                )
            )

        return ESIParams(params)

    def parse_security(self, endpoint: str) -> List[str]:
        """Extra what OAuth scopes are needed for an endpoint.

        Each API request have either 0 or 1 scope required.

        Note:
            API request with multiple scopes will yield error in finding token in ``ESITokens``.

        Raises:
            ValueError: API request with multiple scopes is not supported.
        """
        accepted_methods = list(self.paths[endpoint].keys())
        body = self.paths[endpoint][accepted_methods[0]]

        security = body.get("security", None)

        if not security:
            # this endpoint does not require any scopes
            return []

        if len(security) > 1:
            # Only one OAuth method accepted: "evesso"
            # Maybe in the future this block will be reached.
            pass

        scope_ = security[0]["evesso"]
        if len(scope_) > 1:
            raise ValueError(f"endpoints with > 1 scopes are not supported.")
            
        return scope_

    def __load_metadata(self) -> None:
        """Loads Swagger metadata into this object.

        Raises:
            ValueError: Metadata is empty when loading from local file or EVE website.
        """
        metadata = None

        # TODO: retrieve from ESI every three(?) months
        if (
            not os.path.exists(self.METADATA_PATH)
            or os.stat(self.METADATA_PATH).st_size == 0
        ):
            # If no local copy, retrieve from ESI
            r = requests.get(
                "https://esi.evetech.net/latest/swagger.json?datasource=tranquility"
            )
            r.raise_for_status()
            metadata = r.json()

            with open(self.METADATA_PATH, "w") as metadata_file:
                json.dump(metadata, metadata_file)
                logger.debug("metadata retrieved from ESI")
        else:
            # If local copy exists, just read
            with open(self.METADATA_PATH, "r") as metadata_file:
                metadata = json.load(metadata_file)

        if not metadata or not metadata.keys():
            raise ValueError("Metadata is empty.")
        
        self.securityDefinitions = metadata["securityDefinitions"]
        self.paths = metadata["paths"]

        # Store metadata parameters in ESIParams
        params = [
            Param(
                name=v['name'],                         # param name
                _in=v["in"],                            # in headers, query, or path
                required=v.get("required", False),
                dtype=v["type"],                        # accepted data type
                default=v.get("default", None),         # default value if not given by user
            )
            for v in metadata["parameters"].values()
        ]
        self.parameters = ESIParams(params)
