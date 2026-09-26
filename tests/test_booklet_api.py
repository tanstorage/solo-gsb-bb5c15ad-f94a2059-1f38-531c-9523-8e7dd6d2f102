"""纸质乐谱小册子拼版接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _create_plan(client, measure_count=8) -> str:
    """创建并保存一个规划：每小节高度 1、容量 1，每小节一页（页数=小节数）。"""
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


def test_impose_eight_page_booklet(client):
    plan_id = _create_plan(client, 8)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [8], "max_sheets_per_signature": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["page_count"] == 8
    assert body["signature_count"] == 1
    assert body["total_blank_pages"] == 0
    sig = body["signatures"][0]
    assert sig["start_page"] == 1
    assert sig["end_page"] == 8
    assert sig["content_pages"] == 8
    assert sig["blank_pages"] == 0
    assert len(sig["sheets"]) == 2
    assert sig["sheets"][0] == {
        "sheet_index": 1,
        "front_left": 8,
        "front_right": 1,
        "back_left": 2,
        "back_right": 7,
    }
    assert sig["sheets"][1] == {
        "sheet_index": 2,
        "front_left": 6,
        "front_right": 3,
        "back_left": 4,
        "back_right": 5,
    }


def test_impose_multi_signature_with_blanks(client):
    plan_id = _create_plan(client, 6)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [2, 4, 6], "max_sheets_per_signature": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_count"] == 6
    assert body["signature_count"] == 2
    assert body["total_blank_pages"] == 2
    first, second = body["signatures"]
    assert (first["start_page"], first["end_page"]) == (1, 2)
    assert first["blank_pages"] == 2
    assert first["sheets"][0]["front_left"] is None
    assert first["sheets"][0]["front_right"] == 1
    assert first["sheets"][0]["back_left"] == 2
    assert first["sheets"][0]["back_right"] is None
    assert (second["start_page"], second["end_page"]) == (3, 6)
    assert second["sheets"][0]["front_left"] == 6
    assert second["sheets"][0]["back_right"] == 5


def test_impose_lexicographic_tie_break(client):
    plan_id = _create_plan(client, 9)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [3, 6, 9], "max_sheets_per_signature": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_blank_pages"] == 3
    assert body["signature_count"] == 2
    assert [s["end_page"] for s in body["signatures"]] == [3, 9]


def test_impose_plan_not_found_returns_404(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/booklet-imposition",
        json={"segment_end_pages": [4], "max_sheets_per_signature": 1},
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_impose_single_oversized_segment_returns_422(client):
    plan_id = _create_plan(client, 8)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [8], "max_sheets_per_signature": 1},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "超过每帖容量" in detail
    assert "曲段不可跨帖" in detail


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"segment_end_pages": [4, 4], "max_sheets_per_signature": 1}, "严格递增"),
        ({"segment_end_pages": [3], "max_sheets_per_signature": 1}, "末项"),
        ({"segment_end_pages": [0, 8], "max_sheets_per_signature": 1}, "正整数"),
        ({"segment_end_pages": [-2, 8], "max_sheets_per_signature": 1}, "正整数"),
        (
            {"segment_end_pages": [4.0, 8], "max_sheets_per_signature": 1},
            "整数",
        ),
        (
            {"segment_end_pages": [True, 8], "max_sheets_per_signature": 1},
            "整数",
        ),
        (
            {"segment_end_pages": ["8"], "max_sheets_per_signature": 1},
            "整数",
        ),
        (
            {"segment_end_pages": [None, 8], "max_sheets_per_signature": 1},
            "整数",
        ),
        ({"segment_end_pages": [], "max_sheets_per_signature": 1}, "不能为空"),
        (
            {"segment_end_pages": [8], "max_sheets_per_signature": 0},
            "正整数",
        ),
        (
            {"segment_end_pages": [8], "max_sheets_per_signature": -2},
            "正整数",
        ),
        (
            {"segment_end_pages": [8], "max_sheets_per_signature": 1.5},
            "整数",
        ),
        (
            {"segment_end_pages": [8], "max_sheets_per_signature": True},
            "整数",
        ),
    ],
)
def test_impose_invalid_payloads_return_422(client, payload, fragment):
    plan_id = _create_plan(client, 8)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition", json=payload
    )
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


def test_impose_missing_and_extra_fields_rejected(client):
    plan_id = _create_plan(client, 8)
    missing = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [8]},
    )
    assert missing.status_code == 422
    extra = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={
            "segment_end_pages": [8],
            "max_sheets_per_signature": 2,
            "notes": "x",
        },
    )
    assert extra.status_code == 422


def test_impose_plan_over_page_limit_returns_422(client):
    plan_id = _create_plan(client, 501)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [501], "max_sheets_per_signature": 200},
    )
    assert resp.status_code == 422, resp.text
    assert "纸质拼版上限" in resp.json()["detail"]


def test_impose_request_echoed_back(client):
    plan_id = _create_plan(client, 8)
    payload = {"segment_end_pages": [4, 8], "max_sheets_per_signature": 2}
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition", json=payload
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"] == payload


def test_impose_does_not_modify_saved_plan(client):
    plan_id = _create_plan(client, 8)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    resp = client.post(
        f"/api/v1/plans/{plan_id}/booklet-imposition",
        json={"segment_end_pages": [4, 8], "max_sheets_per_signature": 2},
    )
    assert resp.status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
