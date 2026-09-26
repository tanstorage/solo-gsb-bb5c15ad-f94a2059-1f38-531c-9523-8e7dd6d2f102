"""必翻/禁翻约束接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


_BASE = {
    "measure_heights": [1] * 11,
    # 内部位置 1..10 的静默 = s[b-1]
    "silence_seconds": [1, 5, 9, 5, 9, 5, 5, 1, 5, 5, 0],
    "page_height": 3,
    "min_turn_time": 1,
}


def test_required_and_forbidden_echoed_saved_and_reloaded(client):
    payload = dict(_BASE, required_breaks=[5, 8], forbidden_breaks=[3])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["request"]["required_breaks"] == [5, 8]
    assert body["request"]["forbidden_breaks"] == [3]
    assert body["result"]["breaks"] == [2, 5, 8]
    assert 3 not in body["result"]["breaks"]
    assert body["result"]["page_count"] == 4
    assert body["result"]["shortest_turn_silence"] == 1

    plan_id = body["id"]
    again = client.get(f"/api/v1/plans/{plan_id}")
    assert again.status_code == 200
    saved_req = again.json()["request"]
    assert saved_req["required_breaks"] == [5, 8]
    assert saved_req["forbidden_breaks"] == [3]


def test_omitted_constraints_default_to_empty_and_match_baseline(client):
    # 不传约束字段：行为与既有规划一致，回显/保存的请求中包含空约束数组。
    resp = client.post("/api/v1/plans", json=_BASE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["request"]["required_breaks"] == []
    assert body["request"]["forbidden_breaks"] == []
    # 无约束全局最优：容量 3、11 小节 -> 4 页，b=3 取静默 9
    assert body["result"]["breaks"] == [3, 6, 9]
    assert body["result"]["shortest_turn_silence"] == 5

    plan_id = body["id"]
    saved = client.get(f"/api/v1/plans/{plan_id}").json()["request"]
    assert saved["required_breaks"] == []
    assert saved["forbidden_breaks"] == []


def test_empty_constraint_arrays_explicit(client):
    payload = dict(_BASE, required_breaks=[], forbidden_breaks=[])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["result"]["breaks"] == [3, 6, 9]


def test_required_break_changes_plan(client):
    payload = dict(_BASE, required_breaks=[2])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 201, resp.text
    breaks = resp.json()["result"]["breaks"]
    assert breaks[0] == 2 and 2 in breaks


def test_forbidden_break_changes_plan(client):
    payload = dict(_BASE, forbidden_breaks=[3])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 201, resp.text
    assert 3 not in resp.json()["result"]["breaks"]


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("required_breaks", [0], "越界序号 0"),
        ("required_breaks", [11], "越界序号 11"),  # 末尾序号 n
        ("forbidden_breaks", [0], "越界序号 0"),
        ("forbidden_breaks", [12], "越界序号 12"),  # 越界
        ("required_breaks", [-1], "越界序号 -1"),
        ("required_breaks", [5, 5], "重复序号 5"),
        ("forbidden_breaks", [2, 2], "重复序号 2"),
        ("required_breaks", [1.5], "必须为整数"),
        ("required_breaks", [True], "必须为整数"),
    ],
)
def test_invalid_indices_rejected_with_422(client, field, value, fragment):
    resp = client.post("/api/v1/plans", json=dict(_BASE, **{field: value}))
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


def test_cross_array_overlap_rejected_with_422(client):
    payload = dict(_BASE, required_breaks=[5, 8], forbidden_breaks=[8, 2])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 422
    # 请求体校验错误由 pydantic 生成（detail 为结构化数组），用文本断言
    assert "不能交叉" in resp.text
    assert "8" in resp.text


def test_required_silence_insufficient_returns_422(client):
    # 必翻位置 8 静默 1 达标；换成静默 0 的位置（s[7]=1 其实达标），
    # 取位置 4：s[3]=5 达标，位置 1：s[0]=1 恰达标。改用更严 min_turn_time。
    payload = dict(_BASE, min_turn_time=5, required_breaks=[8])
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 422
    assert "必翻位置 8" in resp.json()["detail"]
    assert "必翻点静默不足" in resp.json()["detail"]


def test_no_feasible_solution_under_constraints_returns_422(client):
    # 4 小节、容量 1 必须每节一页；位置 2 静默 0 不允许翻页，任何方案都不可行。
    payload = {
        "measure_heights": [1] * 4,
        "silence_seconds": [9, 0, 9, 0],
        "page_height": 1,
        "min_turn_time": 1,
        "required_breaks": [1],
    }
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 422
    assert "无合法翻页方案" in resp.json()["detail"]


def test_forbidden_only_breaks_makes_infeasible(client):
    payload = {
        "measure_heights": [5, 5],
        "silence_seconds": [9, 0],
        "page_height": 6,
        "min_turn_time": 1,
        "forbidden_breaks": [1],
    }
    resp = client.post("/api/v1/plans", json=payload)
    assert resp.status_code == 422
    assert "无合法翻页方案" in resp.json()["detail"]


def test_extra_constraint_field_still_forbidden(client):
    resp = client.post(
        "/api/v1/plans", json=dict(_BASE, preferred_breaks=[1])
    )
    assert resp.status_code == 422


def test_single_measure_requires_empty_constraints(client):
    payload = {
        "measure_heights": [3],
        "silence_seconds": [0],
        "page_height": 5,
        "min_turn_time": 1,
    }
    ok = client.post("/api/v1/plans", json=payload)
    assert ok.status_code == 201, ok.text
    assert ok.json()["result"]["breaks"] == []

    bad = client.post(
        "/api/v1/plans", json=dict(payload, required_breaks=[1])
    )
    assert bad.status_code == 422
    assert "越界序号 1" in bad.text


def test_saved_constrained_plan_supports_rehearsal_endpoints(client):
    """带约束保存的方案仍可被排练定位/提示接口读取。"""
    payload = dict(_BASE, required_breaks=[5, 8])
    body = client.post("/api/v1/plans", json=payload).json()
    plan_id = body["id"]

    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={
            "beats_per_measure": [4] * 11,
            "tempo_bpm": 120,
            "repeats": [],
        },
    )
    assert resp.status_code == 200, resp.text
    cue_seconds = [c["boundary_seconds"] for c in resp.json()["cues"]]
    # 断页 [2,5,8]，每小节 2 秒 -> 边界时刻 4、10、16
    assert cue_seconds == [4.0, 10.0, 16.0]
