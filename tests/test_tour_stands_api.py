"""巡演曲目单谱架换册接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _create_plan(client, pages: int = 1) -> str:
    """创建并保存一个恰好 pages 页的方案（每小节高度 1、页容量 1）。"""
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1] * pages,
            "silence_seconds": [5] * pages,
            "page_height": 1,
            "min_turn_time": 1,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_stand_changes_basic_walkthrough(client):
    ids = sorted(_create_plan(client) for _ in range(4))
    a, b, c, d = ids
    seq = [a, b, a, c, b, d]
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": seq, "slot_count": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot_count"] == 2
    assert body["show_count"] == 6
    assert body["total_loads"] == 4
    assert body["request"] == {"plan_ids": seq, "slot_count": 2}

    shows = body["shows"]
    assert [s["show_index"] for s in shows] == [1, 2, 3, 4, 5, 6]
    assert [s["plan_id"] for s in shows] == seq
    # 空槽上架：各占编号最小的空槽
    assert shows[0]["load"] == {"plan_id": a, "slot": 1}
    assert shows[0]["unload"] is None
    assert shows[0]["slots_after"] == [a, None]
    assert shows[1]["load"] == {"plan_id": b, "slot": 2}
    assert shows[1]["slots_after"] == [a, b]
    # 已在架上：无动作
    assert shows[2]["load"] is None and shows[2]["unload"] is None
    assert shows[2]["slots_after"] == [a, b]
    # 架满：a 永不再用（视为最晚），撤 a 上 c
    assert shows[3]["unload"] == {"plan_id": a, "slot": 1}
    assert shows[3]["load"] == {"plan_id": c, "slot": 1}
    assert shows[3]["slots_after"] == [c, b]
    assert shows[4]["load"] is None
    # 架满且都永不再用：并列撤字典序较小的 b
    assert shows[5]["unload"] == {"plan_id": b, "slot": 2}
    assert shows[5]["load"] == {"plan_id": d, "slot": 2}
    assert shows[5]["slots_after"] == [c, d]


def test_stand_changes_duplicate_ids_allowed(client):
    # 与装箱接口不同：曲目单允许同一方案重复出现
    plan_id = _create_plan(client)
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [plan_id, plan_id, plan_id], "slot_count": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_loads"] == 1  # 首次上架后一直在架上
    assert [s["load"] for s in body["shows"]] == [
        {"plan_id": plan_id, "slot": 1},
        None,
        None,
    ]
    assert all(s["slots_after"] == [plan_id] for s in body["shows"])


def test_stand_changes_single_show_single_slot(client):
    plan_id = _create_plan(client)
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [plan_id], "slot_count": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["show_count"] == 1
    assert body["total_loads"] == 1
    assert body["shows"][0]["slots_after"] == [plan_id]


def test_stand_changes_response_structure_invariants(client):
    ids = [_create_plan(client) for _ in range(3)]
    seq = [ids[0], ids[1], ids[2], ids[0], ids[2], ids[1], ids[0]]
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": seq, "slot_count": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    shows = body["shows"]
    assert len(shows) == len(seq)
    assert body["total_loads"] == sum(1 for s in shows if s["load"] is not None)
    prev = [None, None]
    for i, show in enumerate(shows):
        assert show["show_index"] == i + 1
        assert show["plan_id"] == seq[i]
        assert len(show["slots_after"]) == 2
        # 每场所需册须在架上
        assert seq[i] in show["slots_after"]
        if show["load"] is None:
            assert show["unload"] is None
            assert show["slots_after"] == prev
        else:
            if show["unload"] is not None:
                # 撤下与上架同槽（腾出即用），被撤册此前在架上
                assert show["unload"]["slot"] == show["load"]["slot"]
                assert show["unload"]["plan_id"] in prev
            assert show["slots_after"][show["load"]["slot"] - 1] == seq[i]
        prev = show["slots_after"]


def test_stand_changes_missing_plan_returns_404(client):
    existing = _create_plan(client)
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [existing, "no-such-plan", existing], "slot_count": 2},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "不存在" in detail
    assert "no-such-plan" in detail


def test_stand_changes_all_missing_ids_listed_once(client):
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": ["aaa", "bbb", "aaa"], "slot_count": 2},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "aaa" in detail and "bbb" in detail
    # 重复的缺失 ID 去重后只列出一次
    assert detail.count("aaa") == 1


def test_stand_changes_empty_string_id_returns_404(client):
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [""], "slot_count": 1},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "payload,fragment",
    [
        # 场次数不足 / 超上限
        ({"plan_ids": [], "slot_count": 2}, "少于下限 1"),
        ({"plan_ids": ["a"] * 501, "slot_count": 2}, "超过上限 500"),
        # 槽位数越界
        ({"plan_ids": ["a"], "slot_count": 0}, "1~20"),
        ({"plan_ids": ["a"], "slot_count": 21}, "1~20"),
        ({"plan_ids": ["a"], "slot_count": -3}, "1~20"),
        # 槽位数非严格整数（pydantic before 校验器拒绝）
        ({"plan_ids": ["a"], "slot_count": 2.5}, "整数"),
        ({"plan_ids": ["a"], "slot_count": 2.0}, "整数"),
        ({"plan_ids": ["a"], "slot_count": True}, "整数"),
        ({"plan_ids": ["a"], "slot_count": "2"}, "整数"),
        ({"plan_ids": ["a"], "slot_count": None}, "整数"),
        # ID 非字符串
        ({"plan_ids": [1], "slot_count": 2}, "字符串"),
        ({"plan_ids": [None], "slot_count": 2}, "字符串"),
        ({"plan_ids": [True], "slot_count": 2}, "字符串"),
        ({"plan_ids": [["a"]], "slot_count": 2}, "字符串"),
    ],
)
def test_stand_changes_invalid_payloads_return_422(client, payload, fragment):
    resp = client.post("/api/v1/tour/stand-changes", json=payload)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


def test_stand_changes_missing_and_extra_fields_rejected(client):
    missing_slots = client.post(
        "/api/v1/tour/stand-changes", json={"plan_ids": ["a"]}
    )
    assert missing_slots.status_code == 422
    missing_ids = client.post(
        "/api/v1/tour/stand-changes", json={"slot_count": 2}
    )
    assert missing_ids.status_code == 422
    extra = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": ["a"], "slot_count": 2, "note": "x"},
    )
    assert extra.status_code == 422


def test_stand_changes_boundary_counts_accepted(client):
    ids = [_create_plan(client) for _ in range(3)]
    # 1 场 1 槽与 500 场 20 槽均合法
    one = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [ids[0]], "slot_count": 1},
    )
    assert one.status_code == 200, one.text

    seq = [ids[i % 3] for i in range(500)]
    many = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": seq, "slot_count": 20},
    )
    assert many.status_code == 200, many.text
    body = many.json()
    assert body["show_count"] == 500
    # 槽位数不少于册种数：每册只上架一次
    assert body["total_loads"] == 3


def test_stand_changes_request_echoed_back(client):
    ids = [_create_plan(client) for _ in range(2)]
    payload = {"plan_ids": [ids[0], ids[1], ids[0]], "slot_count": 2}
    resp = client.post("/api/v1/tour/stand-changes", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"] == payload


def test_stand_changes_does_not_create_or_modify_plans(client):
    ids = [_create_plan(client) for _ in range(2)]
    snapshots = {pid: client.get(f"/api/v1/plans/{pid}").json() for pid in ids}
    resp = client.post(
        "/api/v1/tour/stand-changes",
        json={"plan_ids": [ids[0], ids[1], ids[0]], "slot_count": 1},
    )
    assert resp.status_code == 200, resp.text
    # 返回的 ID 全部是输入 ID，没有新增方案
    returned = {
        s["load"]["plan_id"] for s in resp.json()["shows"] if s["load"]
    }
    assert returned <= set(ids)
    # 原方案仍可查询且内容不变
    for pid in ids:
        assert client.get(f"/api/v1/plans/{pid}").json() == snapshots[pid]
