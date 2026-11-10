"""TASK-BE-000 验收测试：健康检查与统一错误体。

覆盖：
- GET /healthz 返回 200 且含 status="ok" / version
- GET /api/healthz（契约路径，经 /api 前缀）同样 200
- X-Request-ID：未传时生成；传入时原样回写
- 未知路径返回 404 且响应体为 {code,message,request_id}
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ERROR_KEYS = {"code", "message", "request_id"}


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]


def test_healthz_under_api_prefix(client: TestClient) -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_request_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/healthz")
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    assert len(request_id) == 36  # uuid4 字符串长度


def test_request_id_echoed_when_supplied(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "req-abc-123"})
    assert response.headers["X-Request-ID"] == "req-abc-123"


def test_unknown_path_returns_unified_error(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404
    payload = response.json()
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "NOT_FOUND"
    assert isinstance(payload["message"], str)
    assert payload["message"]
    assert payload["request_id"] == response.headers["X-Request-ID"]


def test_unknown_path_keeps_supplied_request_id(client: TestClient) -> None:
    response = client.get("/no-such-route", headers={"X-Request-ID": "req-404-check"})
    assert response.status_code == 404
    assert response.json()["request_id"] == "req-404-check"
    assert response.headers["X-Request-ID"] == "req-404-check"
