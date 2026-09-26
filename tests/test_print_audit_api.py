"""纸质备用谱页序核对接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """以临时 DATA_DIR 重新加载 app.main，得到隔离的服务实例。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        yield c


def _create_plan(client, measure_count=4) -> str:
    """创建并保存一个规划：每小节高度 1、容量 1，每小节一页。"""
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1] * measure_count,
            "silence_seconds": [5] * measure_count,
            "page_height": 1,
            "min_turn_time": 1,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_audit_perfect_order(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1, 2, 3, 4]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["page_count"] == 4
    assert body["operation_count"] == 0
    assert body["request"]["scanned_pages"] == [1, 2, 3, 4]
    assert [s["action"] for s in body["steps"]] == ["keep"] * 4
    assert [s["position"] for s in body["steps"]] == [0, 1, 2, 3]
    assert body["steps"][2] == {
        "action": "keep",
        "position": 2,
        "observed_page": 3,
        "target_page": 3,
    }


def test_audit_mixed_defects(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1, 1, 3, 3, 5]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operation_count"] == 3
    assert [(s["action"], s["position"]) for s in body["steps"]] == [
        ("keep", 0),
        ("replace", 1),
        ("keep", 2),
        ("replace", 3),
        ("remove", 4),
    ]
    assert body["steps"][1]["observed_page"] == 1
    assert body["steps"][1]["target_page"] == 2
    assert body["steps"][4]["target_page"] is None


def test_audit_empty_scanned_inserts_all_at_zero(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operation_count"] == 4
    assert [s["action"] for s in body["steps"]] == ["insert"] * 4
    assert [s["position"] for s in body["steps"]] == [0, 0, 0, 0]
    assert [s["target_page"] for s in body["steps"]] == [1, 2, 3, 4]
    assert all(s["observed_page"] is None for s in body["steps"])


def test_audit_missing_front_and_back_pages(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [2, 3]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operation_count"] == 2
    assert [s["action"] for s in body["steps"]] == [
        "insert",
        "keep",
        "keep",
        "insert",
    ]
    # 缺页 1 插在原输入位置 0 之前；缺页 4 补在末尾（位置 == 数组长度 2）
    assert body["steps"][0]["position"] == 0
    assert body["steps"][0]["target_page"] == 1
    assert body["steps"][-1]["position"] == 2
    assert body["steps"][-1]["target_page"] == 4


def test_audit_plan_not_found_returns_404(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/print-audit",
        json={"scanned_pages": [1]},
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


@pytest.mark.parametrize(
    "scanned,fragment",
    [
        ([0], "正整数"),
        ([-3], "正整数"),
        ([1, True], "正整数"),
        ([1, False], "正整数"),
        ([1.5], "正整数"),
        ([2.0], "正整数"),
        (["1"], "正整数"),
        ([None], "正整数"),
    ],
)
def test_audit_invalid_page_values_return_422(client, scanned, fragment):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": scanned},
    )
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


def test_audit_scanned_over_limit_returns_422(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1] * 501},
    )
    assert resp.status_code == 422, resp.text
    assert "实物页数" in resp.json()["detail"]
    assert "500" in resp.json()["detail"]


def test_audit_scanned_at_limit_accepted(client):
    plan_id = _create_plan(client, measure_count=1)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1] * 500},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operation_count"] == 499
    assert body["steps"][0]["action"] == "keep"
    assert all(s["action"] == "remove" for s in body["steps"][1:])


def test_audit_plan_page_count_over_limit_returns_422(client):
    # 501 小节的规划产生 501 页，超过核对上限 500
    plan_id = _create_plan(client, measure_count=501)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1]},
    )
    assert resp.status_code == 422, resp.text
    assert "规划页数" in resp.json()["detail"]


def test_audit_plan_page_count_at_limit_accepted(client):
    plan_id = _create_plan(client, measure_count=500)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": list(range(1, 501))},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["operation_count"] == 0


def test_audit_missing_and_extra_fields_rejected(client):
    plan_id = _create_plan(client)
    missing = client.post(f"/api/v1/plans/{plan_id}/print-audit", json={})
    assert missing.status_code == 422
    extra = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1], "notes": "x"},
    )
    assert extra.status_code == 422


def test_audit_does_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    resp = client.post(
        f"/api/v1/plans/{plan_id}/print-audit",
        json={"scanned_pages": [1, 9, 3]},
    )
    assert resp.status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
