import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Use SQLite for tests
os.environ["DATABASE_URL"] = "sqlite:///./test_rag.db"

# Mock Keycloak auth for testing
os.environ["KEYCLOAK_URL"] = "http://localhost:8080"
os.environ["KEYCLOAK_PUBLIC_URL"] = "http://localhost:8080"
os.environ["KEYCLOAK_REALM"] = "rag"
os.environ["DATA_DIR"] = "/tmp/rag-test-data"

# ---- Mock heavy third-party modules that are not installed in test env ----
_MOCK_MODULES = [
    "firecrawl",
    "easyocr",
    "qdrant_client",
    "qdrant_client.http",
    "qdrant_client.http.models",
    "langchain_community",
    "langchain_community.document_loaders",
    "langchain_community.embeddings",
    "langchain_text_splitters",
    "langchain_qdrant",
    "langchain_core",
    "sentence_transformers",
    "torch",
    "transformers",
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.background",
]

for mod_name in _MOCK_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = MagicMock


# Import auth early (before any patching) to get original function references
from auth import UserInfo, get_current_user, get_current_admin

_test_admin = UserInfo(id="test-admin-id", username="testadmin", role="admin")


@pytest.fixture(autouse=True)
def mock_auth():
    """Provide a test admin user for all tests."""
    yield _test_admin


@pytest.fixture
def client(mock_auth):
    """FastAPI test client with mocked auth."""
    from fastapi.testclient import TestClient

    with patch("main.start_scheduler"), \
         patch("main.stop_scheduler"):
        from main import app
        from models import Base, engine

        Base.metadata.create_all(bind=engine)

        # Override FastAPI deps with the ORIGINAL function objects
        app.dependency_overrides[get_current_user] = lambda: _test_admin
        app.dependency_overrides[get_current_admin] = lambda: _test_admin

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    """Direct DB session for tests."""
    from models import Base, engine, SessionLocal

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True, scope="session")
def cleanup():
    """Clean up test database after all tests."""
    yield
    if os.path.exists("test_rag.db"):
        os.remove("test_rag.db")
