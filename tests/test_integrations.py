import json

import httpx
import pytest

from deletarr.integrations import HTTP, Plex, Radarr, Seerr, Sonarr
from deletarr.security import SafetyError


def client(handler):
    return HTTP("https://media.test", {"X-Api-Key": "not-for-logs"}, transport=httpx.MockTransport(handler))


async def test_plex_pagination_and_identity_contract():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-1"}})
        offset = int(request.url.params.get("X-Plex-Container-Start", "0"))
        return httpx.Response(200, json={"MediaContainer": {"totalSize": 3, "Metadata": [{"ratingKey": str(offset)}]}})

    plex = Plex(client(handle))
    assert (await plex.identity())["machineIdentifier"] == "server-1"
    items = [i async for page in plex.pages("/library/sections/1/all") for i in page]
    assert len(items) == 3 and len(calls) == 4


async def test_mutation_timeout_is_never_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("ambiguous", request=request)

    with pytest.raises(SafetyError, match="inspect remote state"):
        await Radarr(client(handle)).delete_file(2)
    assert len(calls) == 1


async def test_radarr_unmonitor_verifies_postcondition():
    monitored = True
    calls = []

    def handle(request):
        nonlocal monitored
        calls.append(request.method)
        if request.method == "PUT":
            monitored = json.loads(request.content)["monitored"]
        return httpx.Response(200, json={"id": 12, "monitored": monitored})

    await Radarr(client(handle)).unmonitor(12)
    assert calls == ["GET", "PUT", "GET"] and not monitored


async def test_sonarr_monitoring_uses_episode_ids():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 5, "monitored": False})

    await Sonarr(client(handle)).unmonitor([5, 6])
    assert calls[0].url.path == "/api/v3/episode/monitor"
    assert json.loads(calls[0].content) == {"episodeIds": [5, 6], "monitored": False}
    assert [r.url.path for r in calls[1:]] == ["/api/v3/episode/5", "/api/v3/episode/6"]


async def test_seerr_partial_is_not_deleted():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 10, "status": 4})

    await Seerr(client(handle)).availability(10, True)
    assert calls[0].url.path == "/api/v1/media/10/partial"


async def test_no_redirects_or_response_body_leaks():
    secret = "do-not-expose-api-token"

    def handle(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"}, text=secret)

    with pytest.raises(SafetyError) as error:
        await client(handle).request("GET", "/api/v3/system/status")
    assert secret not in str(error.value)


async def test_plex_current_history_visibility_requires_owned_server():
    def handle(request):
        if request.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "abc"}})
        return httpx.Response(200, json=[{"clientIdentifier": "abc", "owned": False}])

    assert not await Plex(client(handle)).history_visibility()


async def test_unconfirmed_playback_never_becomes_watched_evidence(system):
    from conftest import add_movie, fake_integrations

    from deletarr.db import now

    ident, _, item = add_movie(system, watched=False)
    integration = fake_integrations(system, {"1": item})

    async def pages(path, **params):
        yield [{"ratingKey": "1", "viewedAt": now(), "accountID": 2, "historyKey": "play-1"}]

    integration.plex.pages = pages
    await system.app.state.catalog.watch_history()
    row = system.db.one("SELECT * FROM media WHERE id=?", (ident,))
    assert row["watch_count"] == 0 and row["last_watched"] is None
    assert row["last_playback"] and not row["history_visible"]
    assert not system.db.one("SELECT * FROM watch_events")["completed"]


async def test_confirmed_history_uses_latest_user_and_keeps_local_events(system):
    from conftest import add_movie, fake_integrations

    from deletarr.db import now

    ident, _, item = add_movie(system, watched=False)
    integration = fake_integrations(system, {"1": item})

    async def pages(path, **params):
        yield [
            {"ratingKey": "1", "viewedAt": now() - 1000, "accountID": 2, "historyKey": "watch-1", "viewCount": 1},
            {"ratingKey": "1", "viewedAt": now() - 100, "accountID": 3, "historyKey": "watch-2", "viewCount": 1},
        ]

    integration.plex.pages = pages
    await system.app.state.catalog.watch_history()
    row = system.db.one("SELECT * FROM media WHERE id=?", (ident,))
    assert row["watch_count"] == 2 and row["last_watched"] == now() - 100 and row["history_visible"]

    async def empty(path, **params):
        yield []

    integration.plex.pages = empty
    await system.app.state.catalog.watch_history()
    assert len(system.db.all("SELECT * FROM watch_events")) == 2


async def test_seerr_tv_tmdb_fallback_does_not_collide_with_movie_ids(system):
    from conftest import FakeIntegrations, FakePlex, add_movie

    from deletarr.db import now

    movie, _, _ = add_movie(system)
    catalog = system.app.state.catalog
    library = system.db.one("SELECT * FROM libraries WHERE id='lib'")
    show = catalog.save(library, {"ratingKey": "show", "type": "show", "title": "Show", "addedAt": now() - 400 * 86400})
    for ident in [movie, show]:
        system.db.execute("INSERT INTO external_ids VALUES(?,?,?)", (ident, "tmdb", "123"))

    class SeerrFixture:
        async def requests(self):
            return [
                {
                    "id": 7,
                    "createdAt": "2026-10-19T12:00:00Z",
                    "status": 2,
                    "seasons": [{"seasonNumber": 1}],
                    "media": {"id": 55, "mediaType": "tv", "tmdbId": 123, "tvdbId": 456},
                }
            ]

        async def media(self):
            return [{"id": 55, "mediaType": "tv", "tmdbId": 123, "tvdbId": 456, "status": 5, "status4k": 1}]

    catalog.integrations = FakeIntegrations(FakePlex({}), {"seerr": SeerrFixture()})
    await catalog.requests()
    rows = system.db.all("SELECT * FROM requests")
    assert len(rows) == 2
    assert all(r["media_id"] == show for r in rows)
    assert any(r["season"] == 1 for r in rows)


@pytest.mark.parametrize("kind", ["plex", "seerr"])
async def test_connection_test_rejects_invalid_key_even_when_public_status_works(system, monkeypatch, kind):

    def handler(request):
        if request.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "server-1"}})
        if request.url.path == "/api/v1/status":
            return httpx.Response(200, json={"version": "test"})
        return httpx.Response(401, json={"message": "Unauthorized"})

    adapter = (Plex if kind == "plex" else Seerr)(client(handler))
    integrations = system.app.state.integrations
    monkeypatch.setattr(integrations, "get", lambda _: adapter)
    with pytest.raises(SafetyError, match="HTTP 401"):
        await integrations.health(kind)


async def test_discord_delivery_waits_for_saved_message_and_disables_mentions(system, monkeypatch):
    from deletarr.db import encode

    notifications = system.app.state.executor.notifications
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "12345"})

    monkeypatch.setattr("deletarr.jobs.HTTP", lambda *args, **kwargs: client(handler))
    destination = {
        "kind": "discord",
        "config": notifications.vault.encrypt(encode({"url": "https://discord.com/api/webhooks/test/fake-token"})),
    }
    await notifications.send(destination, "@everyone Cleanup complete")
    assert calls[0].url.params["wait"] == "true"
    assert json.loads(calls[0].content)["allowed_mentions"] == {"parse": []}
