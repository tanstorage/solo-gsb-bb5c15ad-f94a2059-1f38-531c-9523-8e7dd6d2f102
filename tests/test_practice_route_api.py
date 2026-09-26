"""已保存方案练习路线规划接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def test_practice_route_basic(client):
    # 8 小节 / 每页 2 小节 => 4 页；常练小节 0、2、5、7，起始页 1，依赖 7→2。
    # 最优总距离 5 有两条：[0,7,5,2] 与 [0,5,7,2]，字典序最小为 [0,5,7,2]。
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={
            "practice_measures": [0, 2, 5, 7],
            "start_page": 1,
            "dependencies": [[7, 2]],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["page_count"] == 4
    assert body["order"] == [0, 5, 7, 2]
    assert body["order_pages"] == [1, 3, 4, 2]
    assert body["segment_distances"] == [0, 2, 1, 2]
    assert body["total_distance"] == 5


def test_practice_route_without_dependencies(client):
    # 全部小节同页：任何顺序距离都是 0，取小节序号升序
    plan_id = _create_plan(client, measure_count=4, page_height=4)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={"practice_measures": [3, 1, 2], "start_page": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["order"] == [1, 2, 3]
    assert body["order_pages"] == [1, 1, 1]
    assert body["segment_distances"] == [0, 0, 0]
    assert body["total_distance"] == 0


def test_practice_route_dependency_chain(client):
    # 每页 1 小节共 3 页，依赖 2→0 强制先练 2；[1,2,0] 与 [2,1,0] 同为
    # 最优 4，字典序最小为 [1,2,0]
    plan_id = _create_plan(client, measure_count=3, page_height=1)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={
            "practice_measures": [0, 1, 2],
            "start_page": 1,
            "dependencies": [[2, 0]],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["order"] == [1, 2, 0]
    assert body["order_pages"] == [2, 3, 1]
    assert body["segment_distances"] == [1, 1, 2]
    assert body["total_distance"] == 4


def test_practice_route_request_echoed(client):
    plan_id = _create_plan(client)
    payload = {
        "practice_measures": [0, 7],
        "start_page": 2,
        "dependencies": [[7, 0]],
    }
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route", json=payload
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"] == payload


def test_practice_route_dependencies_default_empty(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={"practice_measures": [1, 0], "start_page": 1},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"]["dependencies"] == []


def test_practice_route_plan_not_found_returns_404(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/practice-route",
        json={"practice_measures": [0], "start_page": 1},
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


@pytest.mark.parametrize(
    "payload,fragment",
    [
        # 空目标
        ({"practice_measures": [], "start_page": 1}, "不能为空"),
        # 常练小节数超过上限 15
        (
            {"practice_measures": list(range(16)), "start_page": 1},
            "超过上限",
        ),
        # 小节越界
        ({"practice_measures": [8], "start_page": 1}, "越界"),
        ({"practice_measures": [-1], "start_page": 1}, "越界"),
        # 小节重复
        ({"practice_measures": [1, 1], "start_page": 1}, "重复"),
        # 起始页码越界（方案共 4 页）
        ({"practice_measures": [0], "start_page": 0}, "越界"),
        ({"practice_measures": [0], "start_page": 5}, "越界"),
        ({"practice_measures": [0], "start_page": -2}, "越界"),
        # 依赖指向非目标小节
        (
            {
                "practice_measures": [0, 1],
                "start_page": 1,
                "dependencies": [[0, 2]],
            },
            "非目标小节",
        ),
        (
            {
                "practice_measures": [0, 1],
                "start_page": 1,
                "dependencies": [[3, 1]],
            },
            "非目标小节",
        ),
        # 依赖成环（含自环）
        (
            {
                "practice_measures": [0, 1],
                "start_page": 1,
                "dependencies": [[0, 1], [1, 0]],
            },
            "成环",
        ),
        (
            {
                "practice_measures": [0, 1],
                "start_page": 1,
                "dependencies": [[1, 1]],
            },
            "成环",
        ),
        (
            {
                "practice_measures": [0, 1, 2],
                "start_page": 1,
                "dependencies": [[0, 1], [1, 2], [2, 0]],
            },
            "成环",
        ),
    ],
)
def test_practice_route_semantic_errors_return_422(client, payload, fragment):
    plan_id = _create_plan(client)
    resp = client.post(f"/api/v1/plans/{plan_id}/practice-route", json=payload)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        # 小节序号为浮点、布尔、字符串、null（pydantic 严格整数校验）
        {"practice_measures": [1.5], "start_page": 1},
        {"practice_measures": [1.0], "start_page": 1},
        {"practice_measures": [True], "start_page": 1},
        {"practice_measures": ["1"], "start_page": 1},
        {"practice_measures": [None], "start_page": 1},
        # 起始页码非整数
        {"practice_measures": [0], "start_page": 1.5},
        {"practice_measures": [0], "start_page": True},
        {"practice_measures": [0], "start_page": "1"},
        # 依赖端点非整数或不是二元组
        {
            "practice_measures": [0, 1],
            "start_page": 1,
            "dependencies": [[0, 1.0]],
        },
        {
            "practice_measures": [0, 1],
            "start_page": 1,
            "dependencies": [[True, 1]],
        },
        {
            "practice_measures": [0, 1],
            "start_page": 1,
            "dependencies": [[0]],
        },
        {
            "practice_measures": [0, 1],
            "start_page": 1,
            "dependencies": [[0, 1, 0]],
        },
        {
            "practice_measures": [0, 1],
            "start_page": 1,
            "dependencies": [0],
        },
    ],
)
def test_practice_route_type_errors_return_422(client, payload):
    plan_id = _create_plan(client)
    resp = client.post(f"/api/v1/plans/{plan_id}/practice-route", json=payload)
    assert resp.status_code == 422, resp.text


def test_practice_route_missing_and_extra_fields_rejected(client):
    plan_id = _create_plan(client)
    missing_start = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={"practice_measures": [0]},
    )
    assert missing_start.status_code == 422
    missing_measures = client.post(
        f"/api/v1/plans/{plan_id}/practice-route", json={"start_page": 1}
    )
    assert missing_measures.status_code == 422
    extra = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={
            "practice_measures": [0],
            "start_page": 1,
            "note": "x",
        },
    )
    assert extra.status_code == 422


def test_practice_route_does_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    resp = client.post(
        f"/api/v1/plans/{plan_id}/practice-route",
        json={
            "practice_measures": [0, 5, 7],
            "start_page": 2,
            "dependencies": [[7, 5]],
        },
    )
    assert resp.status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
