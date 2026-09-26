"""排练踏板日志校对接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

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
    # 页 1 = 小节 0..1，页 2 = 小节 2..3；min_turn_time=2
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


def _base_rehearsal():
    # 演奏序列：0,1,2,1遍2,2遍2,3；每小节 2 秒；跨页边界 4、6(回翻)、8
    return {
        "beats_per_measure": [4, 4, 4, 4],
        "tempo_bpm": 120,
        "repeats": [{"start_measure": 1, "end_measure": 2, "play_count": 2}],
    }


def _audit(client, plan_id, pedal_events, tolerance=0.5, **overrides):
    payload = _base_rehearsal()
    payload["pedal_events"] = pedal_events
    payload["tolerance_seconds"] = tolerance
    payload.update(overrides)
    return client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit", json=payload
    )


def test_pedal_audit_happy_path_all_matched(client):
    plan_id = _create_plan(client)
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 4.1, "page_after": 2},  # 对提示 4→2
            {"seconds": 6.0, "page_after": 1},  # 对回翻提示 6→1
            {"seconds": 8.2, "page_after": 2},  # 对提示 8→2
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["total_duration"] == pytest.approx(12.0)
    assert body["request"]["tolerance_seconds"] == 0.5

    assert len(body["cue_audits"]) == 3
    assert [c["to_page"] for c in body["cue_audits"]] == [2, 1, 2]
    assert [c["from_page"] for c in body["cue_audits"]] == [1, 2, 1]
    assert [c["cue_index"] for c in body["cue_audits"]] == [0, 1, 2]
    assert [c["pedal_event_index"] for c in body["cue_audits"]] == [0, 1, 2]
    assert all(c["matched"] for c in body["cue_audits"])
    assert body["cue_audits"][0]["time_diff_seconds"] == pytest.approx(0.1)
    assert body["cue_audits"][1]["pedal_seconds"] == pytest.approx(6.0)

    assert body["extra_turns"] == []
    assert body["missed_count"] == 0
    assert body["extra_count"] == 0
    assert body["total_error_count"] == 0
    assert body["total_time_diff_seconds"] == pytest.approx(0.3)


def test_pedal_audit_missed_and_extra(client):
    plan_id = _create_plan(client)
    # 提示 4→2、6→1、8→2；日志只在 4 秒翻到 2（匹配第一条），
    # 另在 5 秒翻到 2（无对应目标页的多翻——下一条提示要回到 1）
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 4.0, "page_after": 2},
            {"seconds": 5.0, "page_after": 2},
        ],
        tolerance=0.5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cue_audits"][0]["matched"] is True
    assert body["cue_audits"][0]["pedal_event_index"] == 0
    # 提示 1（要回翻到 1）与提示 2（要到 2）均无页码匹配事件
    assert body["cue_audits"][1]["matched"] is False
    assert body["cue_audits"][1]["pedal_event_index"] is None
    assert body["cue_audits"][1]["time_diff_seconds"] is None
    assert body["cue_audits"][2]["matched"] is False
    assert body["missed_count"] == 2
    assert body["extra_turns"] == [1]
    assert body["extra_count"] == 1
    assert body["total_error_count"] == 3


def test_pedal_audit_empty_events(client):
    plan_id = _create_plan(client)
    resp = _audit(client, plan_id, [], tolerance=0.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["cue_audits"]) == 3
    assert all(not c["matched"] for c in body["cue_audits"])
    assert body["missed_count"] == 3
    assert body["extra_turns"] == []
    assert body["total_error_count"] == 3
    assert body["total_time_diff_seconds"] == 0.0


def test_pedal_audit_single_page_all_events_extra(client):
    # 单页方案（无任何跨页提示）：全部踏板事件都算多翻
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1, 1],
            "silence_seconds": [0, 0],
            "page_height": 10,
            "min_turn_time": 0,
        },
    )
    plan_id = resp.json()["id"]
    payload = {
        "beats_per_measure": [4, 4],
        "tempo_bpm": 120,
        "repeats": [],
        "pedal_events": [
            {"seconds": 1.0, "page_after": 1},
            {"seconds": 2.0, "page_after": 1},
        ],
        "tolerance_seconds": 1.0,
    }
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit", json=payload
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cue_audits"] == []
    assert body["extra_turns"] == [0, 1]
    assert body["extra_count"] == 2
    assert body["total_error_count"] == 2


def test_pedal_audit_plan_not_found(client):
    resp = _audit(client, "does-not-exist", [])
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_pedal_audit_event_at_total_duration_rejected(client):
    plan_id = _create_plan(client)
    # 总时长 12 秒；恰等于总时长视为越出演奏时长
    resp = _audit(client, plan_id, [{"seconds": 12.0, "page_after": 2}])
    assert resp.status_code == 422
    assert "越出演奏时长" in resp.json()["detail"]


def test_pedal_audit_event_beyond_duration_rejected(client):
    plan_id = _create_plan(client)
    resp = _audit(client, plan_id, [{"seconds": 13.5, "page_after": 2}])
    assert resp.status_code == 422
    assert "越出演奏时长" in resp.json()["detail"]


def test_pedal_audit_page_out_of_range_rejected(client):
    plan_id = _create_plan(client)
    # 方案仅 2 页
    resp = _audit(client, plan_id, [{"seconds": 4.0, "page_after": 3}])
    assert resp.status_code == 422
    assert "越出页码范围" in resp.json()["detail"]
    resp = _audit(client, plan_id, [{"seconds": 4.0, "page_after": 0}])
    assert resp.status_code == 422


def test_pedal_audit_non_integer_page_rejected(client):
    plan_id = _create_plan(client)
    resp = _audit(client, plan_id, [{"seconds": 4.0, "page_after": 2.0}])
    assert resp.status_code == 422
    resp = _audit(client, plan_id, [{"seconds": 4.0, "page_after": True}])
    assert resp.status_code == 422


def test_pedal_audit_descending_events_rejected(client):
    plan_id = _create_plan(client)
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 5.0, "page_after": 2},
            {"seconds": 4.0, "page_after": 2},
        ],
    )
    assert resp.status_code == 422
    assert "非降序" in resp.text


def test_pedal_audit_equal_times_allowed(client):
    plan_id = _create_plan(client)
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 4.0, "page_after": 2},
            {"seconds": 4.0, "page_after": 2},
        ],
        tolerance=0.0,
    )
    assert resp.status_code == 200, resp.text


def test_pedal_audit_negative_seconds_rejected(client):
    plan_id = _create_plan(client)
    resp = _audit(client, plan_id, [{"seconds": -0.1, "page_after": 2}])
    assert resp.status_code == 422
    assert "不能为负数" in resp.text


def test_pedal_audit_negative_tolerance_rejected(client):
    plan_id = _create_plan(client)
    resp = _audit(
        client, plan_id, [], tolerance=-0.01
    )
    assert resp.status_code == 422
    assert "不能为负数" in resp.text


def test_pedal_audit_rehearsal_params_still_validated(client):
    plan_id = _create_plan(client)
    # beats_per_measure 长度不符
    resp = _audit(
        client,
        plan_id,
        [],
        beats_per_measure=[4, 4, 4],
    )
    assert resp.status_code == 422
    assert "数组长度" in resp.json()["detail"]

    # 重复段重叠
    resp = _audit(
        client,
        plan_id,
        [],
        repeats=[
            {"start_measure": 0, "end_measure": 2, "play_count": 2},
            {"start_measure": 1, "end_measure": 3, "play_count": 2},
        ],
    )
    assert resp.status_code == 422
    assert "重叠或嵌套" in resp.json()["detail"]


def test_pedal_audit_extra_and_missing_fields_rejected(client):
    plan_id = _create_plan(client)
    base = _base_rehearsal()
    # 缺字段
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
        json={**base, "pedal_events": []},
    )
    assert r.status_code == 422
    # 多余字段
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
        json={
            **base,
            "pedal_events": [],
            "tolerance_seconds": 0.0,
            "unexpected": 1,
        },
    )
    assert r.status_code == 422


def test_pedal_audit_does_not_modify_saved_plan(client):
    plan_id = _create_plan(client)
    before = client.get(f"/api/v1/plans/{plan_id}").json()
    assert _audit(client, plan_id, []).status_code == 200
    after = client.get(f"/api/v1/plans/{plan_id}").json()
    assert before == after


def test_pedal_audit_global_alignment_one_to_one(client):
    plan_id = _create_plan(client)
    # 提示 4→2、6→1、8→2；容差 2 秒，多条事件可能同时在两条提示容差内，
    # 但一对一全局对齐下漏翻+多翻应最少（这里三条都能配上）
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 4.5, "page_after": 2},
            {"seconds": 6.2, "page_after": 1},
            {"seconds": 7.6, "page_after": 2},
        ],
        tolerance=2.0,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_error_count"] == 0
    assert [c["pedal_event_index"] for c in body["cue_audits"]] == [0, 1, 2]


def test_pedal_audit_exact_cue_time_zero_tolerance(client):
    # 提示时刻由 0.1 + 0.2 拍精确重建为 0.3 秒（不经浮点累加）；
    # 容差 0 时事件 0.3000000005 不得误匹配（无 epsilon 放行）
    # 页 1 = 小节 0..1、页 2 = 小节 2（高度 1+1=2 满页，小节 2 独占一页）
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1, 1, 2],
            "silence_seconds": [0, 0, 0],
            "page_height": 2,
            "min_turn_time": 0,
        },
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]
    payload = {
        "beats_per_measure": [0.1, 0.2, 0.7],
        "tempo_bpm": 60,
        "repeats": [],
        "tolerance_seconds": 0.0,
    }
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
        json={
            **payload,
            "pedal_events": [{"seconds": 0.3000000005, "page_after": 2}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cue_audits"][0]["expected_seconds"] == pytest.approx(0.3)
    assert body["cue_audits"][0]["matched"] is False
    assert body["extra_turns"] == [0]
    assert body["total_error_count"] == 2
    # 恰好 0.3 才匹配，时间差精确为 0
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
        json={**payload, "pedal_events": [{"seconds": 0.3, "page_after": 2}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cue_audits"][0]["matched"] is True
    assert body["cue_audits"][0]["time_diff_seconds"] == 0.0
    assert body["total_error_count"] == 0


def test_pedal_audit_equal_diff_tie_picks_earliest_event(client):
    # 提示 0.4 秒；同目标页事件 0.3 与 0.5、容差 0.1：两侧时间差精确相等
    # （均为 0.1），等成本取事件序号字典序最小 → 匹配 0.3 的事件，0.5 为多翻
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1, 1],
            "silence_seconds": [0, 0],
            "page_height": 1,
            "min_turn_time": 0,
        },
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
        json={
            "beats_per_measure": [0.4, 0.6],
            "tempo_bpm": 60,
            "repeats": [],
            "pedal_events": [
                {"seconds": 0.3, "page_after": 2},
                {"seconds": 0.5, "page_after": 2},
            ],
            "tolerance_seconds": 0.1,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cue_audits"][0]["matched"] is True
    assert body["cue_audits"][0]["pedal_event_index"] == 0
    assert body["cue_audits"][0]["time_diff_seconds"] == pytest.approx(0.1)
    assert body["extra_turns"] == [1]
    assert body["total_error_count"] == 1
    assert body["total_time_diff_seconds"] == pytest.approx(0.1)


def test_pedal_audit_event_slightly_before_end_accepted(client):
    plan_id = _create_plan(client)
    # 总时长 12 秒；事件 11.9999999999 略早于终点（差 1e-10）可接受
    resp = _audit(client, plan_id, [{"seconds": 11.9999999999, "page_after": 2}])
    assert resp.status_code == 200, resp.text
    assert resp.json()["extra_turns"] == [0]


def test_pedal_audit_event_slightly_out_of_order_rejected(client):
    plan_id = _create_plan(client)
    # 略逆序（1e-10）即拒绝：非降序按精确比较，无 epsilon 放行
    resp = _audit(
        client,
        plan_id,
        [
            {"seconds": 4.0000000001, "page_after": 2},
            {"seconds": 4.0, "page_after": 2},
        ],
    )
    assert resp.status_code == 422
    assert "非降序" in resp.text
