"""
esi_requests.parser
~~~~~~~~~~~~~~~~~~~

This module converts user-given parameters to an ESIRequest.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from .log import getLogger
from .metadata import ESIMetadata
from .models import PreparedESIRequest
from .sso.application import ESIApplications
from .sso.token import ESITokens
from .utils import cross_product, default_headers

if TYPE_CHECKING:
    from .metadata import EndpointMetadata
    from .models import ESIRequest
    from .utils import Param

logger = getLogger(__name__)


@dataclass
class ETagEntry:
    """Struct for storing etag and etag's payload."""

    etag: str
    payload: Optional[Any]


class ESIRequestParser:
    """Process an ``ESIRequest`` object to a ``PreparedESIRequest`` object."""

    SUPPORTED_METHODS = ["get", "head"]
    HOST = "https://esi.evetech.net/latest"  # also available on metadata

    def __init__(self) -> None:
        self.metadata_parser = ESIMetadata()
        self.apps = ESIApplications()

    def __call__(self, request: "ESIRequest") -> List["PreparedESIRequest"]:
        """Parses user input ``key``, ``method``, and keywords to a ``ESIRequest``.

        This method first consults with ``ESIMetadata`` to retrieve metadata information of a request endpoint.
        Then fills users input parameters to ``headers`` and ``params``.

        Args:
            key: str
                ESI request key, in "/.../.../" format, retrieved from ESI website.
            method: str
                One of ``get``, ``head``. ``post`` and other methods are not supported.

        Returns:
            An ``ESIRequest`` instance, with all relevent fields filled in.
        """
        metadata = self.metadata_parser(request.endpoint)  # metadata of endpoint

        # Make sure the user is using a correct request method
        self.__check_method(metadata, request.method)

        requests = self.parse(request, metadata)

        return requests
    
    def parse(self, request: "ESIRequest", metadata: "EndpointMetadata") -> List["PreparedESIRequest"]:
        if request.params is None:
            request.params = {}
        if request.headers is None:
            request.headers = {}

        # Unpack parameters, such as if given a request with (type_id=[1,2,3], region_id=[10, 11]),
        # we need to treat it as six seperate request: (type_id=1, region_id=10), (type_id=1, region_id=11), ...
        loop_through = self.__find_what_to_loop(request)
        loop_cross_product = cross_product(**loop_through)

        # Remove parameters in ``loop_through`` from request
        for param in loop_through:
            if param in request.headers:
                del request.headers[param]
            elif param in request.params:
                del request.params[param]
            elif param in request.kwargs:
                del request.kwargs[param]

        # Prepare shared headers, kwargs, and params
        headers = default_headers()
        headers.update(request.headers)
        # Add oauth to headers
        if metadata.sso_scope and "Authorization" not in headers:  # some method does not need oauth
            app = self.apps.search_scope(" ".join(metadata.sso_scope))  # find matching application
            with ESITokens(app) as tokens:  # find token for this scope
                cname = request.kwargs.pop("cname", "any")  # "any" for matching any token
                token = tokens.generate() if not tokens.exist(cname) else tokens[cname]
                headers["Authorization"] = "Bearer {}".format(token.access_token)
                if "character_id" not in request.kwargs:
                    # Tackle ESI's authenticated search endpoint, see https://github.com/esi/esi-issues/issues/1323.
                    # Use character_id of one of the authenticated user if character_id is not given by user.
                    request.kwargs["character_id"] = token.character_id
        request.headers = headers
  
        # Construct the list of PreparedESIRequest
        requests = []
        for loop_body in loop_cross_product:
            prep = PreparedESIRequest(endpoint=request.endpoint, method=request.method, params=deepcopy(request.params), headers=deepcopy(request.headers), kwargs=deepcopy(request.kwargs))
            
            # Since loop_body has same keys for all requests, this ``update`` could work.
            prep.kwargs.update(loop_body)

            self.parse_request_parameters(prep, metadata)

            self.build_req_headers(prep)
            self.build_req_url(prep)

            requests.append(prep)

        return requests

    def build_req_url(self, request: "PreparedESIRequest") -> None:
        """Builds url for a request from ``endpoint`` and ``params`` values.
        
        ``params`` should already be parsed and checked against ESI metadata.
        Use ``parse_request_keywords()`` before calling this function.
        """        
        params = [f"{k}={request.params[k]}" for k in request.params]
        path_param = request.kwargs.pop("path_params", None)

        url = self.HOST + request.endpoint.format(**path_param)
        url = f"{url}?{'&'.join(params)}"

        request.url = url

    def build_req_headers(self, request: "PreparedESIRequest") -> None:
        """Builds ``headers`` for a ``PreparedESIRequest``.

        Most ESI request needs no user-input headers, but some request headers are useful,
        such as ``If-None-Match`` and ``User-Agent``.
        This method defines default value of these useful HTTP request headers field.
        """

        # If-None-Match is updated in Session
        # User-Agent and other static headers are defined in ``default_headers()`` in utils

        pass

    def parse_request_parameters(self, request: "PreparedESIRequest", metadata: "EndpointMetadata") -> None:
        """Parses user input parameters according to endpoints metadata.

        Checks fields in ``keywords`` if necessary parameters are given.
        If given, fills in ``api_request`` with fields from keywords.
        If not, either raises errors if field is ``required``, or fill in default value if defined by ESI metadata.

        Args:
            api_request: ESIRequest
                Request info for a request. Retrieved from ``ESIMetadata.__getitem__``.
                Necessary info (url, params, headers) should be filled in by ``ESIMetadata``.
            keywords: dict
                Keywords provided by user, containing ``headers``, ``params``, and other necessary fields defined by ESI,
                such as ``character_id``, ``type_id``, etc.
                If ESI metadata marks fields as ``required``, missing these fields in ``keywords`` raises errors.

        Facts:
            _in: path
                1. Always marked as ``required``
                2. Appears as ``Param.name`` in url: https://.../characters/{character_id}/orders/
                3. Pass in with kwd argument, not params, headers, or data
                4. No default value defined by ESI
            _in: query
                1. Pass in with either ``kwd`` or ``params``, not ``headers``
                2. Appears as ``?query=value`` in url: https://.../?datasource=tranquility
            _in: header
                1. Request token has been updated to headers before calling this function
                2. ESI marks ``token`` field as optional

        Note:
            ``dtype`` field in ESI metadata is not checked nor enforced.
        """

        path_params = {}  # parameter for endpoint, such as region_id for /markets/{region_id}/orders/
        query_params = {}  # paramters for url/?{key1}={value1}?{key2}={value2}...
        kwargs = deepcopy(request.kwargs)

        for api_param_ in metadata.parameters:
            # Each param has a "in" field defined by metadata.
            if api_param_._in == "path":
                value = self.__parse_request_parameter_in_path(kwargs, api_param_)
                path_params.update({api_param_.name: value})

            elif api_param_._in == "query":
                default = api_param_.default
                value = self.__parse_request_parameter_in_query(kwargs, request.params, api_param_)
                if value is not None:
                    query_params.update({api_param_.name: value})  # update if value is given
                elif default is not None:
                    query_params.update({api_param_.name: default})  # else update if default is set

            elif api_param_._in == "header":  # not "headers"
                value = self.__parse_request_parameter_in_header(request.headers, api_param_)
                if value is not None:
                    request.headers.update({api_param_.name: value})

        request.kwargs.update({"path_params": path_params})
        request.params.update(query_params)

    @staticmethod
    def __parse_request_parameter_in_path(where: dict, param: "Param") -> str:
        # dtype is not checked yet. Checking it needs to parse "schema" field and integerate into "dtype",
        # and needs to find a way to fit user input to the dtype field.
        # No need to check Param.required because Param._in == "path" => Param.required == True
        value = where.pop(param.name, None)
        if value is None:
            raise ValueError(f'Required parameter "{param.name}" not given.')
        return value

    @staticmethod
    def __parse_request_parameter_in_query(kwargs: dict, params: dict, param: "Param") -> str:
        value = kwargs.pop(param.name, None)
        value2 = params.get(param.name)

        if value and value2:
            raise ValueError(f'Duplicated parameter "{param.name}" give in both params and kwargs.')
    
        if value is None and value2 is None and param.required and param.default is None:
            # If parameter not given, and no default value exists, but is required, raise error
            raise ValueError(f'Requred parameter "{param.name}" not given.')

        if value is None and value2 is None:
            return param.default
        if value is not None:
            return value
        if value2 is not None:
            return value2

    @staticmethod
    def __parse_request_parameter_in_header(headers: dict, param: "Param") -> str:
        if param.name not in headers:
            if param.required:
                raise KeyError(f'Required parameter "{param.name}" not given in keywords.')
            else:
                return param.default
    
    @staticmethod
    def __find_what_to_loop(request: "ESIRequest") -> Dict[str, List]:
        """Given a ``esi_requests.get("endpoint", type_id=[1,2,3,4,5], region_id=[2,3,4])``,
           this method captures ``"type_id"`` and ``"region_id"``."""
        kwargs = deepcopy(request.kwargs)
        if request.params:
            kwargs.update(request.params)  # combine kwargs and params
        if request.headers:
            kwargs.update(request.headers)  # combine with headers
        should_loop_or_not = lambda value: isinstance(value, Iterable) and not isinstance(value, str)

        loop_through = {param: kwargs[param] for param in kwargs if should_loop_or_not(kwargs[param])}

        return loop_through

    def __check_method(self, metadata: "EndpointMetadata", method: str) -> None:
        """Checks if request method is supported by ESI.

        Args:
            api_request: ESIRequest
                Request info for a request. Retrieved from ``ESIMetadata.__getitem__``.
            method: str
                User input request method.
        """
        if method not in self.SUPPORTED_METHODS:
            raise NotImplementedError(f"Request method {method} is not supported yet.")

        if method in metadata.supported_methods:
            return

        logger.warning("Invalid request method: %s for %s", method, metadata.endpoint)
        raise ValueError(
            f"Request method {method} is not supported by endpoint {metadata.endpoint}."
        )
