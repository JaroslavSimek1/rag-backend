"""Tests for API endpoints."""
import pytest
from unittest.mock import patch, MagicMock


def test_auth_me(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testadmin"
    assert data["role"] == "admin"


def test_ingest_creates_source(client):
    with patch("main.ingest_url"):
        response = client.post("/api/ingest", json={
            "url": "https://test-api.example.com",
            "source_name": "APITestSource",
            "deep_crawl": False,
            "max_depth": 1,
            "permission_type": "legitimate_interest",
        })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("started", "updated")
    assert data["source_id"] > 0


def test_ingest_with_schedule(client):
    with patch("main.ingest_url"):
        response = client.post("/api/ingest", json={
            "url": "https://schedule-test.example.com",
            "source_name": "ScheduleTestSource",
            "schedule": "daily",
        })
    assert response.status_code == 200


def test_ingest_invalid_schedule(client):
    with patch("main.ingest_url"):
        response = client.post("/api/ingest", json={
            "url": "https://bad-schedule.example.com",
            "source_name": "BadScheduleSource",
            "schedule": "every_5_minutes",
        })
    assert response.status_code == 400


def test_get_jobs(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert "jobs" in response.json()


def test_get_sources(client):
    response = client.get("/api/sources")
    assert response.status_code == 200
    assert "sources" in response.json()


def test_delete_nonexistent_job(client):
    with patch("main.delete_job_vectors"):
        response = client.delete("/api/jobs/999999")
    assert response.status_code == 404


def test_get_analytics(client):
    response = client.get("/api/analytics")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    assert "sources" in data
    assert "evidences" in data
    assert "strategies" in data


def test_search_endpoint(client):
    with patch("main.query_rag", return_value={
        "answer": "Test answer",
        "sources": [{"path": "https://example.com", "score": 0.95, "filename": "test.md", "fragment": "some text", "job_id": "1"}],
    }):
        response = client.post("/api/search", json={"query": "test query", "k": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Test answer"
    assert len(data["sources"]) == 1
    assert "fragment" in data["sources"][0]


def test_get_file_content(client, tmp_path):
    import os
    os.environ["DATA_DIR"] = str(tmp_path)
    test_file = tmp_path / "testfile.md"
    test_file.write_text("# Hello\nThis is test content.")

    response = client.get("/api/files/testfile.md")
    assert response.status_code == 200
    assert "Hello" in response.json()["content"]


def test_get_file_invalid_path(client):
    response = client.get("/api/files/..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)  # 400 if path traversal detected, 404 if normalized


def test_resolve_nonexistent_job(client):
    response = client.put("/api/jobs/999999/resolve")
    assert response.status_code == 404


def test_job_detail_nonexistent(client):
    response = client.get("/api/jobs/999999/detail")
    assert response.status_code == 404


def test_sources_include_permission_type(client):
    # First create a source via ingest
    with patch("main.ingest_url"):
        client.post("/api/ingest", json={
            "url": "https://perm-test.example.com",
            "source_name": "PermTestSource",
            "permission_type": "consent",
        })

    response = client.get("/api/sources")
    sources = response.json()["sources"]
    perm_source = next((s for s in sources if s["name"] == "PermTestSource"), None)
    assert perm_source is not None
    assert perm_source["permission_type"] == "consent"
