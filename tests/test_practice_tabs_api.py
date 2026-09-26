"""纸质谱练习页签规划接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _create_plan(client, measure_count=8, page_height=2) -> str:
    """创建并保存一个规划：每小节高度 1，容量 page_height，静默充足。"""
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1] * measure_count,
            "silence_seconds": [5] * measure_count,
            "page_height": page_height,
            "min_turn_time": 1,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_practice_tabs_basic(client):
    # 8 小节 / 每页 2 小节 => 4 页，四页目标权重 3、1、2、3，K=2
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 3},
                {"measure": 2, "weight": 1},
                {"measure": 5, "weight": 2},
                {"measure": 7, "weight": 3},
            ],
            "K": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["page_count"] == 4
    assert body["target_page_count"] == 4
    # {1,4}: 0 + 1*1 + 2*1 + 0 = 3；{1,3}: 0+1+0+3 = 4；故 {1,4} 最优
    assert body["tab_pages"] == [1, 4]
    assert body["total_cost"] == 3
    assert [(t["page"], t["weight"]) for t in body["targets"]] == [
        (1, 3),
        (2, 1),
        (3, 2),
        (4, 3),
    ]
    # 页 3 距页签 4 为 1、距页签 1 为 2 => 归 4
    assert [t["nearest_tab"] for t in body["targets"]] == [1, 1, 4, 4]
    assert [t["distance"] for t in body["targets"]] == [0, 1, 1, 0]


def test_practice_tabs_weights_merged_on_same_page(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 3},
                {"measure": 1, "weight": 1},  # 与小节 0 同属页 1
                {"measure": 6, "weight": 2},
                {"measure": 7, "weight": 2},  # 同属页 4
            ],
            "K": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_page_count"] == 2
    assert [(t["page"], t["weight"]) for t in body["targets"]] == [(1, 4), (4, 4)]
    # 两页等重，K=1 取下中位数（较小页码），页签贴页 1
    assert body["tab_pages"] == [1]
    assert body["total_cost"] == 12  # 页 4 权重 4 × 距离 3
    # 距离 3 无并列，两页均归页签 1
    assert [t["nearest_tab"] for t in body["targets"]] == [1, 1]


def test_practice_tabs_distance_tie_smaller_page(client):
    # 每页 1 小节：3 页权重 [4,1,3]，K=2。最优页签 {1,3}（代价 1），
    # 页 2 距页签 1、3 等距：归属取较小页码 1。
    plan_id = _create_plan(client, measure_count=3, page_height=1)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 4},
                {"measure": 1, "weight": 1},
                {"measure": 2, "weight": 3},
            ],
            "K": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tab_pages"] == [1, 3]
    assert body["total_cost"] == 1
    middle = body["targets"][1]
    assert middle["page"] == 2
    assert middle["nearest_tab"] == 1  # 等距取较小页码
    assert middle["distance"] == 1


def test_practice_tabs_k_equals_target_pages(client):
    plan_id = _create_plan(client, measure_count=5, page_height=1)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": i, "weight": i + 1} for i in range(5)
            ],
            "K": 5,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tab_pages"] == [1, 2, 3, 4, 5]
    assert body["total_cost"] == 0
    assert all(t["distance"] == 0 for t in body["targets"])


def test_practice_tabs_tabs_only_on_target_pages(client):
    # 10 页，只在页 1 与页 10 有目标：K=2 时页签只能贴这两页
    plan_id = _create_plan(client, measure_count=10, page_height=1)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 1},
                {"measure": 9, "weight": 1},
            ],
            "K": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_page_count"] == 2
    assert body["tab_pages"] == [1, 10]
    assert body["total_cost"] == 0
    assert len(body["targets"]) == 2


def test_practice_tabs_request_echoed(client):
    plan_id = _create_plan(client)
    payload = {
        "practice_measures": [{"measure": 0, "weight": 7}],
        "K": 1,
    }
    resp = client.post(f"/api/v1/plans/{plan_id}/practice-tabs", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"] == payload


def test_practice_tabs_plan_not_found_returns_404(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/practice-tabs",
        json={"practice_measures": [{"measure": 0, "weight": 1}], "K": 1},
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


@pytest.mark.parametrize(
    "payload,fragment",
    [
        # 空目标
        ({"practice_measures": [], "K": 1}, "不能为空"),
        # 小节越界
        (
            {"practice_measures": [{"measure": 8, "weight": 1}], "K": 1},
            "越界",
        ),
        (
            {"practice_measures": [{"measure": -1, "weight": 1}], "K": 1},
            "越界",
        ),
        # 小节重复
        (
            {
                "practice_measures": [
                    {"measure": 0, "weight": 1},
                    {"measure": 0, "weight": 2},
                ],
                "K": 1,
            },
            "重复",
        ),
        # 权重非正
        (
            {"practice_measures": [{"measure": 0, "weight": 0}], "K": 1},
            "正整数",
        ),
        (
            {"practice_measures": [{"measure": 0, "weight": -2}], "K": 1},
            "正整数",
        ),
        # K 越界（目标页数为 1）
        (
            {"practice_measures": [{"measure": 0, "weight": 1}], "K": 0},
            "1 到目标页数",
        ),
        (
            {"practice_measures": [{"measure": 0, "weight": 1}], "K": 2},
            "1 到目标页数",
        ),
        (
            {"practice_measures": [{"measure": 0, "weight": 1}], "K": -3},
            "1 到目标页数",
        ),
    ],
)
def test_practice_tabs_semantic_errors_return_422(client, payload, fragment):
    plan_id = _create_plan(client)
    resp = client.post(f"/api/v1/plans/{plan_id}/practice-tabs", json=payload)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        # 权重 / 序号为浮点或布尔（pydantic 严格整数校验）
        {"practice_measures": [{"measure": 0, "weight": 1.5}], "K": 1},
        {"practice_measures": [{"measure": 1.0, "weight": 1}], "K": 1},
        {"practice_measures": [{"measure": True, "weight": 1}], "K": 1},
        {"practice_measures": [{"measure": 0, "weight": True}], "K": 1},
        {"practice_measures": [{"measure": "0", "weight": 1}], "K": 1},
        {"practice_measures": [{"measure": None, "weight": 1}], "K": 1},
        # K 非整数
        {"practice_measures": [{"measure": 0, "weight": 1}], "K": 1.5},
    ],
)
def test_practice_tabs_type_errors_return_422(client, payload):
    plan_id = _create_plan(client)
    resp = client.post(f"/api/v1/plans/{plan_id}/practice-tabs", json=payload)
    assert resp.status_code == 422, resp.text


def test_practice_tabs_bool_k_rejected(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={"practice_measures": [{"measure": 0, "weight": 1}], "K": True},
    )
    assert resp.status_code == 422, resp.text
    assert "整数" in resp.text


def test_practice_tabs_missing_and_extra_fields_rejected(client):
    plan_id = _create_plan(client)
    missing_k = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={"practice_measures": [{"measure": 0, "weight": 1}]},
    )
    assert missing_k.status_code == 422
    missing_measures = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs", json={"K": 1}
    )
    assert missing_measures.status_code == 422
    extra = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [{"measure": 0, "weight": 1}],
            "K": 1,
            "note": "x",
        },
    )
    assert extra.status_code == 422
    extra_inner = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 1, "label": "hard"}
            ],
            "K": 1,
        },
    )
    assert extra_inner.status_code == 422


def test_practice_tabs_does_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-tabs",
        json={
            "practice_measures": [
                {"measure": 0, "weight": 2},
                {"measure": 7, "weight": 5},
            ],
            "K": 1,
        },
    )
    assert resp.status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
