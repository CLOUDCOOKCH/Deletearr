import pytest
from conftest import add_movie, add_rule, candidate

from deletarr.db import encode, now, uid


def test_movie_candidate_includes_known_watch_date_and_proxied_poster(system):
    media_id, _, item = add_movie(system)
    item["thumb"] = "/library/metadata/1/thumb/123"
    system.db.execute("UPDATE media SET metadata=? WHERE id=?", (encode(item), media_id))
    candidate(system)
    response = system.client.get("/api/candidates")
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["last_watched"] == item["lastViewedAt"]
    assert row["history_visible"] is True
    assert row["poster"] == f"/api/media/{media_id}/poster"
    system.db.execute("UPDATE media SET metadata='{}',last_watched=NULL,history_visible=0 WHERE id=?", (media_id,))
    row = system.client.get("/api/candidates").json()["items"][0]
    assert row["last_watched"] is None and row["poster"] is None
    assert row["history_visible"] is False


@pytest.mark.parametrize("scope", ["show", "season", "episode"])
def test_tv_candidates_use_series_poster_and_latest_affected_episode_watch(system, scope):
    for ident, kind, parent, watched, visible in [
        ("show", "show", None, None, 0),
        ("season", "season", "show", None, 0),
        ("episode", "episode", "season", 100, 1),
        ("episode-2", "episode", "season", 200, 0),
    ]:
        system.db.execute(
            """INSERT INTO media(id,plex_key,library_id,parent_id,kind,title,last_watched,history_visible,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                ident,
                ident,
                "lib",
                parent,
                kind,
                ident,
                watched,
                visible,
                encode({"thumb": f"/library/metadata/{ident}/thumb"}),
                now(),
            ),
        )
    rule = add_rule(system, kind=scope)
    system.db.execute(
        "INSERT INTO candidates(id,media_id,rule_id,rule_version,state,version,reason,eligible_at) VALUES(?,?,?,1,'PendingApproval',1,'{}',?)",
        (uid(), scope, rule["id"], now()),
    )
    row = system.client.get("/api/candidates").json()["items"][0]
    assert row["poster"] == "/api/media/show/poster"
    assert row["last_watched"] == (100 if scope == "episode" else 200)
    assert row["history_visible"] is (scope == "episode")
