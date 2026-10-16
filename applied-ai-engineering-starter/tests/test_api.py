from fastapi.testclient import TestClient

from applied_ai_starter.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask() -> None:
    response = client.post("/ask", json={"question": "How do I reset my password?"})

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["title"] == "Password Reset"
    assert body["confidence"] > 0
