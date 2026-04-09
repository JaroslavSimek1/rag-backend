#!/usr/bin/env python3
"""
Locust load test for RAG backend.
Tests concurrent access and response time (NFR-1.1: < 15s).

Usage:
    locust -f locustfile.py --host http://localhost:8000
    locust -f locustfile.py --host http://localhost:8000 --headless -u 10 -r 2 -t 60s
"""

from locust import HttpUser, task, between, tag


SAMPLE_QUERIES = [
    "Co je MENDELU?",
    "Jaké fakulty má Mendelova univerzita?",
    "Kde se nachází MENDELU?",
    "Jaké studijní programy nabízí MENDELU?",
    "Kde najdu informace pro uchazeče?",
    "Jaké jsou fakulty MU?",
    "Jak se přihlásit ke studiu?",
    "Kde najdu kontakty na univerzitu?",
    "Co nabízí zahradnická fakulta?",
    "Jaké jsou možnosti doktorského studia?",
]


class RAGUser(HttpUser):
    """Simulates a typical user querying the RAG system."""

    wait_time = between(1, 5)

    @tag("search")
    @task(10)
    def search_query(self):
        """Most common action: searching."""
        import random
        query = random.choice(SAMPLE_QUERIES)
        with self.client.post(
            "/api/search",
            json={"query": query, "k": 5},
            timeout=30,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("answer"):
                    resp.failure("Empty answer")
                elif resp.elapsed.total_seconds() > 15:
                    resp.failure(f"NFR-1.1: response took {resp.elapsed.total_seconds():.1f}s (>15s)")
                else:
                    resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @tag("browse")
    @task(3)
    def get_jobs(self):
        """Admin browsing jobs list."""
        self.client.get("/api/jobs")

    @tag("browse")
    @task(3)
    def get_sources(self):
        """Admin browsing sources list."""
        self.client.get("/api/sources")

    @tag("analytics")
    @task(2)
    def get_analytics(self):
        """Admin viewing analytics."""
        self.client.get("/api/analytics")

    @tag("health")
    @task(1)
    def health_check(self):
        """Basic health check."""
        self.client.get("/api/health")


class AdminUser(HttpUser):
    """Simulates an admin performing management tasks."""

    wait_time = between(3, 10)
    weight = 1  # fewer admin users than regular users

    @tag("admin")
    @task(5)
    def view_jobs(self):
        self.client.get("/api/jobs")

    @tag("admin")
    @task(3)
    def view_sources(self):
        self.client.get("/api/sources")

    @tag("admin")
    @task(2)
    def view_analytics(self):
        self.client.get("/api/analytics")

    @tag("admin", "search")
    @task(1)
    def admin_search(self):
        """Admin testing search quality."""
        self.client.post(
            "/api/search",
            json={"query": "Co je MENDELU?", "k": 5},
            timeout=30,
        )
