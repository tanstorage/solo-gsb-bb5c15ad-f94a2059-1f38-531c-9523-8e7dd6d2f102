"""排练翻页提示接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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


def _cues(client, plan_id, **overrides):
    payload = {
        "beats_per_measure": [4, 4, 4, 4],
        "tempo_bpm": 120,
        "repeats": [{"start_measure": 1, "end_measure": 2, "play_count": 2}],
    }
    payload.update(overrides)
    return client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues", json=payload
    )


def test_cues_happy_path(client):
    plan_id = _create_plan(client)
    resp = _cues(client, plan_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["min_turn_time"] == 2
    assert body["total_duration"] == pytest.approx(12.0)

    # 演奏序列：0, 1, 2, 1(遍2), 2(遍2), 3
    assert body["cues"] == [
        {
            "boundary_seconds": 4.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "to_measure": 2,
            "from_playthrough": 1,
            "to_playthrough": 1,
            "direction": "forward",
            "silence_seconds": 5.0,
            "sufficient": True,
        },
        {
            "boundary_seconds": 6.0,
            "from_page": 2,
            "to_page": 1,
            "from_measure": 2,
            "to_measure": 1,
            "from_playthrough": 1,
            "to_playthrough": 2,
            "direction": "backward",
            "silence_seconds": 1.0,
            "sufficient": False,
        },
        {
            "boundary_seconds": 8.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "to_measure": 2,
            "from_playthrough": 2,
            "to_playthrough": 2,
            "direction": "forward",
            "silence_seconds": 5.0,
            "sufficient": True,
        },
    ]


def test_cues_no_repeats_and_no_cue_at_end(client):
    plan_id = _create_plan(client)
    resp = _cues(client, plan_id, repeats=[])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_duration"] == pytest.approx(8.0)
    # 仅 1->2 一次前进；末尾小节 3 之后不生成提示
    assert len(body["cues"]) == 1
    cue = body["cues"][0]
    assert cue["direction"] == "forward"
    assert (cue["from_measure"], cue["to_measure"]) == (1, 2)
    assert cue["boundary_seconds"] == pytest.approx(4.0)


def test_cues_plan_not_found(client):
    resp = _cues(client, "does-not-exist")
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_cues_beats_length_mismatch(client):
    plan_id = _create_plan(client)
    resp = _cues(client, plan_id, beats_per_measure=[4, 4, 4])
    assert resp.status_code == 422
    assert "数组长度" in resp.json()["detail"]


def test_cues_overlapping_repeats_rejected(client):
    plan_id = _create_plan(client)
    resp = _cues(
        client,
        plan_id,
        repeats=[
            {"start_measure": 0, "end_measure": 2, "play_count": 2},
            {"start_measure": 1, "end_measure": 3, "play_count": 2},
        ],
    )
    assert resp.status_code == 422
    assert "重叠或嵌套" in resp.json()["detail"]


def test_cues_play_count_out_of_range(client):
    plan_id = _create_plan(client)
    resp = _cues(
        client,
        plan_id,
        repeats=[{"start_measure": 0, "end_measure": 1, "play_count": 5}],
    )
    assert resp.status_code == 422
    assert "2~4" in resp.text


def test_cues_repeats_optional(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={"beats_per_measure": [4, 4, 4, 4], "tempo_bpm": 120},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"]["repeats"] == []


def test_cues_rejects_basic_validation_errors(client):
    plan_id = _create_plan(client)
    base = {"beats_per_measure": [4, 4, 4, 4], "tempo_bpm": 120}

    for bad in (
        dict(base, tempo_bpm=0),
        dict(base, tempo_bpm=-1),
        dict(base, beats_per_measure=[4, 4, 4, 0]),
        dict(base, extra_field=1),
    ):
        r = client.post(
            f"/api/v1/plans/{plan_id}/rehearsal/cues", json=bad
        )
        assert r.status_code == 422, bad


def test_cues_do_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    assert _cues(client, plan_id).status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after
