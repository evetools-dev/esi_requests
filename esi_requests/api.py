"""
esi_requests.api
~~~~~~~~~~~~~~~~

This module provides simple interface for user.

:copyright: 2023 by Hanbo Guo
"""

from .sessions import Session


def request(method: str, endpoint: str, **kwargs):
    with Session() as session:
        return session.request(method, endpoint, **kwargs)

def get(endpoint: str, params=None, **kwargs):
    return request("get", endpoint, params=params, **kwargs)

def head(endpoint: str, params=None, **kwargs):
    return request("head", endpoint, params=params, **kwargs)