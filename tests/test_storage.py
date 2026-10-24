"""Milestone 3: the database remembers inspections, decisions and readings."""

from datetime import date

import pytest

from packcheck import storage
from packcheck.pipeline import inspect_photo

TODAY = date(2026, 9, 17)


@pytest.fixture(scope="module")
def bad_result(product, tmp_path_factory):
    return inspect_photo(product.reference_path.parent / "photo_bad.jpg", product, tmp_path_factory.mktemp("run"), today=TODAY)


@pytest.fixture
def connection(tmp_path):
    connection = storage.connect(tmp_path / "test.sqlite")
    yield connection
    connection.close()


def test_inspection_round_trip(connection, bad_result):
    result = dict(bad_result, differences=[dict(d) for d in bad_result["differences"]])
    inspection_id = storage.save_inspection(connection, result, run="20260917-101500-000000_demo", work_order="WO-1", inspector="Sam")

    assert all("id" in difference for difference in result["differences"])   # ids handed back for the buttons
    (stored,) = storage.list_inspections(connection, day=result["created_at"][:10])
    assert stored["id"] == inspection_id
    assert stored["work_order"] == "WO-1" and stored["inspector"] == "Sam"
    assert stored["status"] == "differences_found" and stored["readings_status"] == "ok"
    assert [d["number"] for d in stored["differences"]] == [1, 2, 3]
    assert all(d["decision"] is None for d in stored["differences"])
    named = {r["name"]: r for r in stored["readings"] if r["name"]}
    assert named["best_before"]["text"] == "2030-03-15" and named["best_before"]["valid"] is True
    assert storage.list_inspections(connection, work_order="WO-1") == [stored]
    assert storage.list_inspections(connection, work_order="WO-2") == []


def test_latest_decision_wins_but_all_are_kept(connection, bad_result):
    result = dict(bad_result, differences=[dict(d) for d in bad_result["differences"]])
    storage.save_inspection(connection, result, run="run-a")
    difference_id = result["differences"][0]["id"]

    storage.save_decision(connection, difference_id, "dismiss", note="just glare", decided_by="Sam")
    latest = storage.save_decision(connection, difference_id, "confirm", note="no, it is real", decided_by="Alex")

    assert latest["decision"] == "confirm" and latest["decided_by"] == "Alex"
    (stored,) = storage.list_inspections(connection, day=result["created_at"][:10])
    assert stored["differences"][0]["decision"]["decision"] == "confirm"
    assert len(storage.list_decisions(connection)) == 2          # the audit trail keeps both
    with pytest.raises(ValueError):
        storage.save_decision(connection, difference_id, "maybe")
    with pytest.raises(KeyError):
        storage.save_decision(connection, 999999, "confirm")


def test_users_and_login(connection):
    assert storage.login_required(connection) is False            # nobody created: open access
    storage.add_user(connection, "Sam", "1234")
    assert storage.login_required(connection) is True
    assert storage.list_users(connection) == ["Sam"]

    assert storage.login(connection, "Sam", "9999") is None
    token = storage.login(connection, "Sam", "1234")
    assert storage.user_for_token(connection, token) == "Sam"
    storage.logout(connection, token)
    assert storage.user_for_token(connection, token) is None

    with pytest.raises(ValueError):
        storage.add_user(connection, "Short", "12")


def test_sessions(connection):
    session_id = storage.create_session(connection)
    assert storage.session_exists(connection, session_id)
    assert not storage.session_exists(connection, "nope")
