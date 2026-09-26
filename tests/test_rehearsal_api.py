"""排练定位接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _create_plan(client) -> str:
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [3, 3, 4, 2],
            "silence_seconds": [2, 5, 1, 0],
            "page_height": 6,
            "min_turn_time": 2,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_locate_happy_path(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [
                {"start_measure": 1, "end_measure": 2, "play_count": 2}
            ],
            "timestamps": [0, 2, 6, 8, 10],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["total_duration"] == pytest.approx(12.0)
    # 演奏序列：0, 1, 2, 1(遍2), 2(遍2), 3
    assert body["locations"] == [
        {"measure": 0, "playthrough": 1, "beat_offset": 0.0, "page_index": 1},
        {"measure": 1, "playthrough": 1, "beat_offset": 0.0, "page_index": 1},
        {"measure": 1, "playthrough": 2, "beat_offset": 0.0, "page_index": 1},
        {"measure": 2, "playthrough": 2, "beat_offset": 0.0, "page_index": 2},
        {"measure": 3, "playthrough": 1, "beat_offset": 0.0, "page_index": 2},
    ]


def test_locate_no_repeats(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 60,  # 每小节 4 秒
            "timestamps": [0, 3.99, 4, 15.99],
        },
    )
    assert resp.status_code == 200, resp.text
    measures = [loc["measure"] for loc in resp.json()["locations"]]
    assert measures == [0, 0, 1, 3]
    pages = [loc["page_index"] for loc in resp.json()["locations"]]
    assert pages == [1, 1, 1, 2]


def test_locate_plan_not_found(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/rehearsal/locate",
        json={
            "beats_per_measure": [4],
            "tempo_bpm": 120,
            "timestamps": [0],
        },
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_locate_beats_length_mismatch(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [4, 4, 4],  # 规划有 4 小节
            "tempo_bpm": 120,
            "timestamps": [0],
        },
    )
    assert resp.status_code == 422
    assert "数组长度" in resp.json()["detail"]


def test_locate_overlapping_repeats(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [
                {"start_measure": 0, "end_measure": 2, "play_count": 2},
                {"start_measure": 1, "end_measure": 3, "play_count": 2},
            ],
            "timestamps": [0],
        },
    )
    assert resp.status_code == 422
    assert "重叠或嵌套" in resp.json()["detail"]


def test_locate_play_count_out_of_range(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [{"start_measure": 0, "end_measure": 1, "play_count": 5}],
            "timestamps": [0],
        },
    )
    assert resp.status_code == 422
    assert "2~4" in resp.text


def test_locate_timestamp_beyond_total(client):
    plan_id = _create_plan(client)
    # 4 小节 × 2 秒 = 8 秒，8.0 是右端点同样不可定位
    for bad_ts in (8.0, 10.0):
        resp = client.post(
            f"/api/v1/plans/{plan_id}/rehearsal/locate",
            json={
                "beats_per_measure": [4, 4, 4, 4],
                "tempo_bpm": 120,
                "timestamps": [bad_ts],
            },
        )
        assert resp.status_code == 422
        assert "超出演奏总时长" in resp.json()["detail"]


def test_locate_exact_decimal_boundaries(client):
    """每小节恰 1 秒：边界判定走精确有理数时间轴，不用浮点容差。"""
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={
            "beats_per_measure": [1, 1, 1, 1],
            "tempo_bpm": 60,  # 每小节恰 1 秒，总时长恰 4 秒
            "timestamps": [0.9999999995, 1, 1.9999999995, 3.9999999995],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_duration"] == 4.0
    assert body["locations"] == [
        # 严格小于边界 1：仍归第一小节，拍偏移精确、不为负
        {"measure": 0, "playthrough": 1, "beat_offset": 0.9999999995, "page_index": 1},
        # 恰在边界才进入下一小节
        {"measure": 1, "playthrough": 1, "beat_offset": 0.0, "page_index": 1},
        # 严格小于总时长：正常定位，不误判越界
        {"measure": 1, "playthrough": 1, "beat_offset": 0.9999999995, "page_index": 1},
        {"measure": 3, "playthrough": 1, "beat_offset": 0.9999999995, "page_index": 2},
    ]

    # 等于或超过总时长：422
    for bad_ts in (4.0, 4.0000000001):
        resp = client.post(
            f"/api/v1/plans/{plan_id}/rehearsal/locate",
            json={
                "beats_per_measure": [1, 1, 1, 1],
                "tempo_bpm": 60,
                "timestamps": [bad_ts],
            },
        )
        assert resp.status_code == 422
        assert "超出演奏总时长" in resp.json()["detail"]


def test_locate_repeat_jumpback_mixed_beats(client):
    """重复回跳 + 长短小节混排：精确累计时长、区间归属与拍偏移。"""
    plan_id = _create_plan(client)
    # 小节 0: 1 拍(1s)，小节 1: 3 拍(3s)，小节 2/3: 4 拍(4s)；
    # 重复段 [0..1] 两遍 -> 序列 0,1,0,1,2,3，开始时刻 0,1,4,5,8,12，总时长 16
    payload = {
        "beats_per_measure": [1, 3, 4, 4],
        "tempo_bpm": 60,
        "repeats": [{"start_measure": 0, "end_measure": 1, "play_count": 2}],
    }
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={**payload, "timestamps": [3.9999999999, 4, 4.9999999999, 8, 15.9999999999]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_duration"] == 16.0
    assert body["locations"] == [
        {"measure": 1, "playthrough": 1, "beat_offset": 2.9999999999, "page_index": 1},
        # 恰在边界：回跳到小节 0 的第二遍
        {"measure": 0, "playthrough": 2, "beat_offset": 0.0, "page_index": 1},
        {"measure": 0, "playthrough": 2, "beat_offset": 0.9999999999, "page_index": 1},
        {"measure": 2, "playthrough": 1, "beat_offset": 0.0, "page_index": 2},
        # 严格早于总时长 16 秒：可定位
        {"measure": 3, "playthrough": 1, "beat_offset": 3.9999999999, "page_index": 2},
    ]

    # 恰等于总时长 16 秒：422
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/locate",
        json={**payload, "timestamps": [16]},
    )
    assert resp.status_code == 422
    assert "超出演奏总时长" in resp.json()["detail"]


def test_locate_rejects_basic_validation_errors(client):
    plan_id = _create_plan(client)
    base = {
        "beats_per_measure": [4, 4, 4, 4],
        "tempo_bpm": 120,
        "timestamps": [0],
    }

    bad = dict(base, tempo_bpm=0)
    r = client.post(f"/api/v1/plans/{plan_id}/rehearsal/locate", json=bad)
    assert r.status_code == 422

    bad = dict(base, timestamps=[-0.1])
    r = client.post(f"/api/v1/plans/{plan_id}/rehearsal/locate", json=bad)
    assert r.status_code == 422

    bad = dict(base, extra_field=1)
    r = client.post(f"/api/v1/plans/{plan_id}/rehearsal/locate", json=bad)
    assert r.status_code == 422


def test_saved_plan_can_be_relocated_later(client):
    """定位不改变已存方案，可基于同一规划重复提交不同排练数据。"""
    plan_id = _create_plan(client)
    for tempo in (60, 100, 180):
        resp = client.post(
            f"/api/v1/plans/{plan_id}/rehearsal/locate",
            json={
                "beats_per_measure": [4, 4, 4, 4],
                "tempo_bpm": tempo,
                "timestamps": [],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["locations"] == []
