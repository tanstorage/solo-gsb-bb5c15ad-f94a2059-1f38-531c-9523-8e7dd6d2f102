"""排练翻页空档接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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
    # 分页结果：页 1 = 小节 0..1（断页在小节 2，静默 5），页 2 = 小节 2..3
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


def _slots(client, plan_id, **overrides):
    payload = {
        "beats_per_measure": [4, 4, 4, 4],
        "tempo_bpm": 120,
        "repeats": [{"start_measure": 1, "end_measure": 2, "play_count": 2}],
        "turn_seconds": 1.0,
        "hands_busy": [
            {"left": [], "right": []},
            {"left": [[0, 3]], "right": [[1, 2]]},
            {"left": [], "right": [[0, 4]]},
            {"left": [], "right": []},
        ],
    }
    payload.update(overrides)
    return client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/turn-slots", json=payload
    )


def test_turn_slots_happy_path(client):
    plan_id = _create_plan(client)
    resp = _slots(client, plan_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["total_duration"] == pytest.approx(12.0)

    # 演奏序列：0, 1, 2, 1(遍2), 2(遍2), 3（每小节 2 秒）
    assert body["slots"] == [
        {
            "boundary_seconds": 4.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "to_measure": 2,
            "from_playthrough": 1,
            "to_playthrough": 1,
            "feasible": True,
            "hand": "right",
            "action_start_seconds": 3.0,
            "action_end_seconds": 4.0,
        },
        {
            "boundary_seconds": 6.0,
            "from_page": 2,
            "to_page": 1,
            "from_measure": 2,
            "to_measure": 1,
            "from_playthrough": 1,
            "to_playthrough": 2,
            "feasible": True,
            "hand": "left",
            "action_start_seconds": 5.0,
            "action_end_seconds": 6.0,
        },
        {
            "boundary_seconds": 8.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "to_measure": 2,
            "from_playthrough": 2,
            "to_playthrough": 2,
            "feasible": True,
            "hand": "right",
            "action_start_seconds": 7.0,
            "action_end_seconds": 8.0,
        },
    ]


def test_turn_slots_infeasible_boundary_kept(client):
    plan_id = _create_plan(client)
    busy_all = [{"left": [[0, 4]], "right": [[0, 4]]}] * 4
    resp = _slots(client, plan_id, hands_busy=busy_all)
    assert resp.status_code == 200, resp.text
    slots = resp.json()["slots"]
    assert len(slots) == 3  # 边界全部保留
    for slot in slots:
        assert slot["feasible"] is False
        assert slot["hand"] is None
        assert slot["action_start_seconds"] is None
        assert slot["action_end_seconds"] is None


def test_turn_slots_left_preferred_on_tie(client):
    plan_id = _create_plan(client)
    resp = _slots(client, plan_id, hands_busy=[{}] * 4, repeats=[])
    assert resp.status_code == 200, resp.text
    (slot,) = resp.json()["slots"]
    assert slot["hand"] == "left"
    assert slot["action_start_seconds"] == pytest.approx(3.0)
    assert slot["action_end_seconds"] == pytest.approx(4.0)


def test_turn_slots_plan_not_found(client):
    resp = _slots(client, "does-not-exist")
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_turn_slots_beats_length_mismatch(client):
    plan_id = _create_plan(client)
    resp = _slots(client, plan_id, beats_per_measure=[4, 4, 4])
    assert resp.status_code == 422
    assert "数组长度" in resp.json()["detail"]


def test_turn_slots_hands_busy_length_mismatch(client):
    plan_id = _create_plan(client)
    resp = _slots(client, plan_id, hands_busy=[{"left": [], "right": []}] * 3)
    assert resp.status_code == 422
    assert "hands_busy 数组长度" in resp.json()["detail"]


def test_turn_slots_invalid_turn_seconds(client):
    plan_id = _create_plan(client)
    for bad in (0, -0.5):
        resp = _slots(client, plan_id, turn_seconds=bad)
        assert resp.status_code == 422, bad
        assert "turn_seconds" in resp.text


def test_turn_slots_invalid_intervals(client):
    plan_id = _create_plan(client)
    base = [{"left": [], "right": []}] * 4

    bad = [dict(m) for m in base]
    bad[0] = {"left": [[-1, 2]], "right": []}  # 起拍为负
    assert _slots(client, plan_id, hands_busy=bad).status_code == 422

    bad = [dict(m) for m in base]
    bad[1] = {"left": [], "right": [[3, 3]]}  # 起拍不小于止拍
    assert _slots(client, plan_id, hands_busy=bad).status_code == 422

    bad = [dict(m) for m in base]
    bad[2] = {"left": [[0, 5]], "right": []}  # 止拍超过该小节拍数（4）
    resp = _slots(client, plan_id, hands_busy=bad)
    assert resp.status_code == 422
    assert "超过该小节拍数" in resp.text

    bad = [dict(m) for m in base]
    bad[3] = {"left": [[0, 1, 2]], "right": []}  # 区间不是二元组
    assert _slots(client, plan_id, hands_busy=bad).status_code == 422


def test_turn_slots_rejects_basic_validation_errors(client):
    plan_id = _create_plan(client)
    for overrides in (
        {"tempo_bpm": 0},
        {"beats_per_measure": [4, 4, 4, 0]},
        {"extra_field": 1},
        {"hands_busy": [{"left": [], "right": [], "middle": []}] * 4},
    ):
        resp = _slots(client, plan_id, **overrides)
        assert resp.status_code == 422, overrides


def test_turn_slots_overlapping_repeats_rejected(client):
    plan_id = _create_plan(client)
    resp = _slots(
        client,
        plan_id,
        repeats=[
            {"start_measure": 0, "end_measure": 2, "play_count": 2},
            {"start_measure": 1, "end_measure": 3, "play_count": 2},
        ],
    )
    assert resp.status_code == 422
    assert "重叠或嵌套" in resp.json()["detail"]


def test_turn_slots_request_echoed(client):
    plan_id = _create_plan(client)
    resp = _slots(client, plan_id)
    assert resp.status_code == 200, resp.text
    request = resp.json()["request"]
    assert request["turn_seconds"] == 1.0
    assert request["hands_busy"][1] == {"left": [[0, 3]], "right": [[1, 2]]}
    assert request["repeats"] == [
        {"start_measure": 1, "end_measure": 2, "play_count": 2}
    ]


def test_turn_slots_do_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    assert _slots(client, plan_id).status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
