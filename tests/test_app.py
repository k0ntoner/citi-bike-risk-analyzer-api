"""Application startup test, the counterpart of Spring's ``contextLoads()``.

Fails if a module does not import, a service constructor does not match how
``create_app`` builds it, or the app cannot start. Needs no database: the engine
connects lazily and ``/health`` does not query it.
"""

from fastapi.testclient import TestClient

from bikeflow.api.app import create_app


def test_app_starts_and_reports_health():
    with TestClient(create_app()) as client:  # runs the lifespan, like starting the context
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
