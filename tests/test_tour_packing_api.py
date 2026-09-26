"""巡演装运乐谱册装箱接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _create_plan(client, pages: int) -> str:
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


def _create_many(client, pages_list):
    return [_create_plan(client, p) for p in pages_list]


def test_pack_basic_min_boxes_then_balanced(client):
    ids = _create_many(client, [2, 3, 4, 5])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 6},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["box_page_capacity"] == 6
    assert body["total_pages"] == 14
    assert body["box_count"] == 3
    assert body["max_loaded_pages"] == 5
    # 返回的 books 按 ID 升序，页数与请求方案对应
    assert [b["id"] for b in body["books"]] == sorted(ids)
    page_of = dict(zip(ids, [2, 3, 4, 5]))
    assert {b["id"]: b["pages"] for b in body["books"]} == page_of
    assert len(body["boxes"]) == 3
    for idx, box in enumerate(body["boxes"], start=1):
        assert box["box_index"] == idx
        assert box["plan_ids"] == sorted(box["plan_ids"])
        assert box["loaded_pages"] <= 6
        assert box["remaining_capacity"] == 6 - box["loaded_pages"]
        for pid in box["plan_ids"]:
            assert pid in page_of
    # 每个 ID 恰好出现一次
    all_ids = [pid for box in body["boxes"] for pid in box["plan_ids"]]
    assert sorted(all_ids) == sorted(ids)
    # 箱列表按 ID 元组字典序
    tuples = [tuple(box["plan_ids"]) for box in body["boxes"]]
    assert tuples == sorted(tuples)


def test_pack_single_box(client):
    ids = _create_many(client, [1, 2])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["box_count"] == 1
    assert body["max_loaded_pages"] == 3
    box = body["boxes"][0]
    assert box["plan_ids"] == sorted(ids)
    assert box["loaded_pages"] == 3
    assert box["remaining_capacity"] == 7


def test_pack_lexicographic_tie_break(client):
    # 四册各 2 页、容量 4：2 箱，规范字典序最小为前两个 ID 一箱
    ids = _create_many(client, [2, 2, 2, 2])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 4},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["box_count"] == 2
    assert body["max_loaded_pages"] == 4
    ordered = sorted(ids)
    assert [box["plan_ids"] for box in body["boxes"]] == [
        ordered[:2],
        ordered[2:],
    ]


def test_pack_missing_plan_returns_404(client):
    existing = _create_plan(client, 3)
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": [existing, "no-such-plan"], "box_page_capacity": 10},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "不存在" in detail
    assert "no-such-plan" in detail


def test_pack_all_ids_missing_returns_404(client):
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ["aaa", "bbb"], "box_page_capacity": 10},
    )
    assert resp.status_code == 404
    assert "aaa" in resp.json()["detail"]
    assert "bbb" in resp.json()["detail"]


def test_pack_single_book_over_capacity_returns_422(client):
    ids = _create_many(client, [8, 2])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 6},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "超过箱体页容量" in detail
    assert "整本" in detail
    # 指出超容的具体方案 ID 与页数
    page_of = dict(zip(ids, [8, 2]))
    over_id = next(pid for pid, p in page_of.items() if p > 6)
    assert over_id in detail


def test_pack_request_order_independent(client):
    ids = _create_many(client, [2, 3, 4, 5])
    payload = {"plan_ids": ids, "box_page_capacity": 6}
    r1 = client.post("/api/v1/tour/packing", json=payload).json()
    r2 = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": list(reversed(ids)), "box_page_capacity": 6},
    ).json()
    assert r1["boxes"] == r2["boxes"]
    assert r1["books"] == r2["books"]


@pytest.mark.parametrize(
    "payload,fragment",
    [
        # 方案数少于 2
        ({"plan_ids": [], "box_page_capacity": 5}, "2~12"),
        (
            {"plan_ids": ["only-one"], "box_page_capacity": 5},
            "下限 2",
        ),
        # 方案数超过 12
        (
            {"plan_ids": [f"id{i}" for i in range(13)], "box_page_capacity": 5},
            "上限 12",
        ),
        # 重复 ID
        (
            {"plan_ids": ["a", "a"], "box_page_capacity": 5},
            "重复",
        ),
        # 容量非正（请求层即在存在性检查之前拦截）
        ({"plan_ids": ["a", "b"], "box_page_capacity": 0}, "正整数"),
        ({"plan_ids": ["a", "b"], "box_page_capacity": -2}, "正整数"),
        # 容量非整数（pydantic before 校验器拒绝）
        ({"plan_ids": ["a", "b"], "box_page_capacity": 2.5}, "整数"),
        ({"plan_ids": ["a", "b"], "box_page_capacity": True}, "整数"),
        ({"plan_ids": ["a", "b"], "box_page_capacity": "5"}, "整数"),
        # ID 非字符串
        ({"plan_ids": [1, 2], "box_page_capacity": 5}, "字符串"),
        ({"plan_ids": [None, "b"], "box_page_capacity": 5}, "字符串"),
        ({"plan_ids": [True, "b"], "box_page_capacity": 5}, "字符串"),
    ],
)
def test_pack_invalid_payloads_return_422(client, payload, fragment):
    resp = client.post("/api/v1/tour/packing", json=payload)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


def test_pack_missing_and_extra_fields_rejected(client):
    missing = client.post(
        "/api/v1/tour/packing", json={"plan_ids": ["a", "b"]}
    )
    assert missing.status_code == 422
    extra = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ["a", "b"], "box_page_capacity": 5, "note": "x"},
    )
    assert extra.status_code == 422
    no_ids = client.post("/api/v1/tour/packing", json={"box_page_capacity": 5})
    assert no_ids.status_code == 422


def test_pack_request_echoed_back(client):
    ids = _create_many(client, [1, 2])
    payload = {"plan_ids": ids, "box_page_capacity": 10}
    resp = client.post("/api/v1/tour/packing", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"] == payload


def test_pack_does_not_create_or_modify_plans(client):
    ids = _create_many(client, [2, 3])
    snapshots = {pid: client.get(f"/api/v1/plans/{pid}").json() for pid in ids}
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 6},
    )
    assert resp.status_code == 200
    # 装箱返回的 ID 全部是输入 ID，没有新增方案
    returned = {pid for box in resp.json()["boxes"] for pid in box["plan_ids"]}
    assert returned == set(ids)
    # 原方案仍可查询且内容不变
    for pid in ids:
        assert client.get(f"/api/v1/plans/{pid}").json() == snapshots[pid]


def test_pack_two_to_twelve_boundary_counts(client):
    # 恰好 2 个与 12 个方案均合法
    ids2 = _create_many(client, [1, 1])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids2, "box_page_capacity": 100},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["box_count"] == 1

    ids12 = _create_many(client, [1] * 12)
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids12, "box_page_capacity": 100},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["box_count"] == 1
    assert resp.json()["max_loaded_pages"] == 12


def test_pack_uses_saved_plan_page_count(client):
    # 创建 5 页与 3 页两个方案，容量 5：5+3 超容量 -> 2 箱；
    # 若误用小节数以外值会导致不同箱数
    ids = _create_many(client, [5, 3])
    resp = client.post(
        "/api/v1/tour/packing",
        json={"plan_ids": ids, "box_page_capacity": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["box_count"] == 2
    assert body["max_loaded_pages"] == 5
    by_id = {b["id"]: b["pages"] for b in body["books"]}
    assert by_id == {ids[0]: 5, ids[1]: 3}
