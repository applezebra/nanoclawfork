"""Test configuration.

Pin pytest-anyio to the asyncio backend only — trio is not a project
dependency and adding it just to satisfy the parametrize-over-backends
default would be scope creep.
"""
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
