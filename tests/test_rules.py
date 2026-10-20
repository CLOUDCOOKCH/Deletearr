import json

import pytest
from conftest import add_movie, add_rule
from pydantic import ValidationError

from deletarr.db import encode, now, uid
from deletarr.rules import Condition, RuleInput, predicate


def test_unknown_is_not_inverted_into_permission():
    node = Condition.model_validate({"group": "not", "children": [{"field": "watched", "value": True}]})
    assert predicate(node, {"watched": None})[0] is None
    for group, children, expected in [
        ("and", [True, None], None),
        ("and", [False, None], False),
        ("or", [False, None], None),
        ("or", [True, None], True),
    ]:
        condition = Condition(
            group=group,
            children=[
                Condition(field="protected", value=v) if v is not None else Condition(field="watched", value=True)
                for v in children
            ],
        )
        assert predicate(condition, {"protected": True, "watched": None})[0] is expected


def test_never_watched_requires_complete_evidence(system):
    ident, _, _ = add_movie(system, watched=False)
    rule = add_rule(system, conditions={"field": "watched", "value": False})
    item = system.db.one("SELECT * FROM media WHERE id=?", (ident,))
    result = system.app.state.engine.evaluate(item, rule)
    assert not result["eligible"]
    system.db.execute("UPDATE media SET history_complete=1 WHERE id=?", (ident,))
    item = system.db.one("SELECT * FROM media WHERE id=?", (ident,))
    assert system.app.state.engine.evaluate(item, rule)["eligible"]


def test_protections_always_win_and_simulation_is_read_only(system):
    ident, physical, _ = add_movie(system)
    rule = add_rule(system)
    response = system.client.post("/api/rules/" + rule["id"] + "/simulate", json={})
    assert response.json()["eligible"] == 1
    assert not system.db.all("SELECT * FROM candidates") and physical.exists()
    system.db.execute("UPDATE media SET protected=1 WHERE id=?", (ident,))
    assert system.client.post("/api/rules/" + rule["id"] + "/simulate", json={}).json()["eligible"] == 0
    system.db.execute("UPDATE media SET protected=0 WHERE id=?", (ident,))
    system.db.execute("UPDATE libraries SET enabled=0")
    assert not system.app.state.engine.evaluate(system.db.one("SELECT * FROM media WHERE id=?", (ident,)), rule)[
        "eligible"
    ]


def test_rule_update_invalidates_approval(system):
    from conftest import candidate

    add_movie(system)
    rule = add_rule(system)
    row = candidate(system, rule)
    data = RuleInput.model_validate(
        {
            **{k: v for k, v in rule.items() if k not in {"id", "created_at", "updated_at"}},
            "conditions": json.loads(rule["conditions"]),
        }
    ).model_dump()
    data["name"] = "Changed rule"
    assert system.client.put("/api/rules/" + rule["id"], json=data).status_code == 200
    assert system.db.one("SELECT * FROM candidates WHERE id=?", (row["id"],))["state"] == "Protected"
    assert (
        system.client.post(
            "/api/candidates/" + row["id"] + "/action", json={"version": row["version"], "action": "approve"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "__import__", "value": 1},
        {"field": "watched", "operator": "gt", "value": True},
        {"field": "size_bytes", "value": "anything"},
        {"group": "not", "children": []},
        {"field": "kind", "value": "arbitrary"},
    ],
)
def test_allowlisted_condition_schema(condition):
    with pytest.raises(ValidationError):
        Condition.model_validate(condition)


def test_recent_request_season_scope(system):
    db = system.db
    for ident, kind, parent, metadata in [
        ("show", "show", None, {}),
        ("s1", "season", "show", {"index": 1}),
        ("s2", "season", "show", {"index": 2}),
    ]:
        db.execute(
            "INSERT INTO media(id,plex_key,library_id,parent_id,kind,title,added_at,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ident, ident, "lib", parent, kind, ident, now() - 400 * 86400, encode(metadata), now()),
        )
    db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?)", (uid(), "show", 44, 1, now(), 2, "{}"))
    rule = add_rule(system, kind="season", conditions={"field": "added_days", "operator": "gte", "value": 30})
    engine = system.app.state.engine
    assert "Recent Seerr request protection" in engine.protection(
        db.one("SELECT * FROM media WHERE id='s1'"), rule, now()
    )
    assert "Recent Seerr request protection" not in engine.protection(
        db.one("SELECT * FROM media WHERE id='s2'"), rule, now()
    )


def test_simulation_explains_uncertain_history_instead_of_hiding_it(system):
    add_movie(system, watched=False)
    rule = add_rule(system, conditions={"field": "watched", "value": False})
    response = system.client.post("/api/rules/" + rule["id"] + "/simulate", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["eligible"] == 0 and len(data["items"]) == 1
    assert any("uncertain" in reason for reason in data["items"][0]["protections"])
