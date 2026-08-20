from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(app_name="test-server", environment="test")
    with TestClient(create_app(settings)) as test_client:
        yield test_client
