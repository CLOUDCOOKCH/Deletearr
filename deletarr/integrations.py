"""Supported, bounded HTTP adapters. Mutations are never automatically retried."""

import asyncio
import json
import os
import ssl
from functools import lru_cache
from urllib.parse import quote

import httpx

from . import __version__
from .db import now, uid
from .security import SafetyError, resolve_url

MAX_RESPONSE = 32 * 1024 * 1024


@lru_cache(maxsize=4)
def tls_context(cafile):
    return ssl.create_default_context(cafile=cafile)


class HTTP:
    def __init__(self, url, headers, private=False, timeout=15, transport=None, service="Integration"):
        self.url, self.headers, self.private, self.timeout = url.rstrip("/"), headers, private, timeout
        self.transport = transport
        self.service = service

    async def request(self, method, path, **kwargs):
        allow_text = kwargs.pop("allow_text", False)
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise SafetyError("Invalid integration endpoint")
        # Only Plex's known API paths are diagnostic-safe. Other adapters may
        # carry credentials in paths (for example Telegram bot URLs).
        context = self.service
        if self.service in {"Plex", "Plex account"}:
            context += f" {method} {path.split('?')[0]}"
        for attempt in range(3 if method == "GET" else 1):
            try:
                target = httpx.URL(self.url + path)
                headers = dict(self.headers)
                extensions = {}
                if self.transport is None:
                    # Pin the validated address for the TCP connection; a second DNS lookup
                    # must not turn a public hostname into a metadata/LAN endpoint.
                    _, addresses = await asyncio.to_thread(resolve_url, self.url, self.private)
                    headers["Host"] = target.netloc.decode()
                    extensions["sni_hostname"] = target.host
                    target = target.copy_with(host=addresses[attempt % len(addresses)])
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=False,
                    transport=self.transport,
                    trust_env=False,
                    verify=tls_context(os.getenv("DELETARR_CA_BUNDLE")),
                ) as client:
                    async with client.stream(
                        method, target, headers=headers, extensions=extensions, **kwargs
                    ) as response:
                        if response.status_code == 404:
                            return None
                        if response.status_code >= 300:
                            raise SafetyError(f"{context} returned HTTP {response.status_code}")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_RESPONSE:
                                raise SafetyError("Integration response exceeds size limit")
                        if not data:
                            return {}
                        if allow_text:
                            return {"accepted": True}
                        if response.headers.get("content-type", "").startswith("image/"):
                            return bytes(data), response.headers["content-type"]
                        try:
                            return json.loads(data)
                        except (ValueError, UnicodeDecodeError) as exc:
                            raise SafetyError("Integration returned invalid JSON") from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 == (3 if method == "GET" else 1):
                    if method == "GET":
                        reason = (
                            "TLS certificate validation failed; use a hostname matching the certificate or local HTTP"
                            if isinstance(exc.__cause__, ssl.SSLCertVerificationError)
                            or "CERTIFICATE_VERIFY_FAILED" in str(exc)
                            else "request timed out"
                            if isinstance(exc, httpx.TimeoutException)
                            else "connection failed; check the server address, port and container network access"
                        )
                        raise SafetyError(f"{context}: {reason}") from exc
                    raise SafetyError(
                        f"{context} request failed; inspect remote state before retrying a mutation"
                    ) from exc
                await asyncio.sleep(0.2 * 2**attempt)


class Plex:
    def __init__(self, http):
        self.http = http

    async def identity(self):
        data = await self.http.request("GET", "/identity")
        if not data or not data.get("MediaContainer", {}).get("machineIdentifier"):
            raise SafetyError("Plex server identity unavailable")
        return data["MediaContainer"]

    async def libraries(self):
        data = await self.http.request("GET", "/library/sections")
        if data is None:
            raise SafetyError("Plex library endpoint unavailable")
        return data["MediaContainer"].get("Directory", [])

    async def pages(self, path, **params):
        start = 0
        while start < 1_000_000:
            data = await self.http.request(
                "GET", path, params={**params, "X-Plex-Container-Start": start, "X-Plex-Container-Size": 200}
            )
            if data is None:
                raise SafetyError("Plex catalog or history endpoint unavailable")
            container = data["MediaContainer"]
            items = container.get("Metadata", [])
            yield items
            count = len(items)
            start += count
            if count == 0 or start >= int(container.get("totalSize", start)):
                return
        raise SafetyError("Plex pagination limit exceeded")

    async def metadata(self, key):
        data = await self.http.request(
            "GET", "/library/metadata/" + quote(str(key), safe=""), params={"includeGuids": 1}
        )
        items = (data or {}).get("MediaContainer", {}).get("Metadata", [])
        if len(items) != 1:
            raise SafetyError("Plex item is missing or ambiguous")
        return items[0]

    async def sessions(self):
        data = await self.http.request("GET", "/status/sessions")
        if data is None or "MediaContainer" not in data:
            raise SafetyError("Active playback could not be verified")
        return data["MediaContainer"].get("Metadata", [])

    async def history_visibility(self):
        identity = await self.identity()
        account = HTTP("https://plex.tv", self.http.headers, transport=self.http.transport, service="Plex account")
        resources = await account.request("GET", "/api/v2/resources", params={"includeHttps": 1})
        if not isinstance(resources, list):
            return False
        return any(
            r.get("owned") is True and r.get("clientIdentifier") == identity["machineIdentifier"] for r in resources
        )


class Radarr:
    def __init__(self, http):
        self.http = http

    async def status(self):
        return await self.http.request("GET", "/api/v3/system/status")

    async def movies(self):
        return await self.http.request("GET", "/api/v3/movie")

    async def roots(self):
        return await self.http.request("GET", "/api/v3/rootfolder")

    async def files(self, movie_id):
        return await self.http.request("GET", "/api/v3/moviefile", params={"movieId": movie_id})

    async def unmonitor(self, movie_id):
        path = f"/api/v3/movie/{int(movie_id)}"
        movie = await self.http.request("GET", path)
        if not movie:
            raise SafetyError("Radarr movie is missing")
        if movie["monitored"]:
            await self.http.request("PUT", path, json={**movie, "monitored": False})
        result = await self.http.request("GET", path)
        if not result or result["monitored"]:
            raise SafetyError("Radarr unmonitor postcondition failed")

    async def delete_file(self, file_id):
        await self.http.request("DELETE", f"/api/v3/moviefile/{int(file_id)}")

    async def remove(self, movie_id):
        # Separate file deletion precedes record removal: avoids recursive movie folder deletion.
        movie = await self.http.request("GET", f"/api/v3/movie/{int(movie_id)}")
        if not movie or not movie.get("tmdbId"):
            raise SafetyError("Radarr movie identity unavailable for import exclusion")
        await self.http.request(
            "DELETE", f"/api/v3/movie/{int(movie_id)}", params={"deleteFiles": "false", "addImportExclusion": "true"}
        )
        if await self.http.request("GET", f"/api/v3/movie/{int(movie_id)}") is not None:
            raise SafetyError("Radarr removal postcondition failed")
        exclusions = await self.http.request("GET", "/api/v3/exclusions")
        if not exclusions or not any(e.get("tmdbId") == movie["tmdbId"] for e in exclusions):
            raise SafetyError("Radarr import exclusion could not be confirmed")


class Sonarr:
    def __init__(self, http):
        self.http = http

    async def status(self):
        return await self.http.request("GET", "/api/v3/system/status")

    async def series(self):
        return await self.http.request("GET", "/api/v3/series")

    async def roots(self):
        return await self.http.request("GET", "/api/v3/rootfolder")

    async def episodes(self, series_id):
        return await self.http.request("GET", "/api/v3/episode", params={"seriesId": series_id})

    async def files(self, series_id):
        return await self.http.request("GET", "/api/v3/episodefile", params={"seriesId": series_id})

    async def unmonitor(self, episode_ids):
        await self.http.request(
            "PUT", "/api/v3/episode/monitor", json={"episodeIds": sorted(set(episode_ids)), "monitored": False}
        )
        for episode_id in episode_ids:
            episode = await self.http.request("GET", f"/api/v3/episode/{int(episode_id)}")
            if not episode or episode["monitored"]:
                raise SafetyError("Sonarr episode unmonitor postcondition failed")

    async def unmonitor_scope(self, series_id, season=None):
        path = f"/api/v3/series/{int(series_id)}"
        series = await self.http.request("GET", path)
        if not series:
            raise SafetyError("Sonarr series missing")
        if season is None:
            series["monitored"] = False
        else:
            if len([s for s in series.get("seasons", []) if s["seasonNumber"] == season]) != 1:
                raise SafetyError("Sonarr season is missing or ambiguous")
            for item in series["seasons"]:
                if item["seasonNumber"] == season:
                    item["monitored"] = False
        await self.http.request("PUT", path, json=series)
        verified = await self.http.request("GET", path)
        if (
            not verified
            or (season is None and verified["monitored"])
            or (
                season is not None
                and (
                    len([x for x in verified.get("seasons", []) if x["seasonNumber"] == season]) != 1
                    or any(x["monitored"] for x in verified["seasons"] if x["seasonNumber"] == season)
                )
            )
        ):
            raise SafetyError("Sonarr monitoring postcondition failed")

    async def delete_file(self, file_id):
        await self.http.request("DELETE", f"/api/v3/episodefile/{int(file_id)}")

    async def remove(self, series_id):
        series = await self.http.request("GET", f"/api/v3/series/{int(series_id)}")
        if not series or not series.get("tvdbId"):
            raise SafetyError("Sonarr series identity unavailable for import exclusion")
        await self.http.request(
            "DELETE",
            f"/api/v3/series/{int(series_id)}",
            params={"deleteFiles": "false", "addImportListExclusion": "true"},
        )
        if await self.http.request("GET", f"/api/v3/series/{int(series_id)}") is not None:
            raise SafetyError("Sonarr removal postcondition failed")
        exclusions = await self.http.request("GET", "/api/v3/importlistexclusion")
        if not exclusions or not any(e.get("tvdbId") == series["tvdbId"] for e in exclusions):
            raise SafetyError("Sonarr import exclusion could not be confirmed")


class Seerr:
    def __init__(self, http):
        self.http = http

    async def status(self):
        data = await self.http.request("GET", "/api/v1/status")
        access = await self.http.request("GET", "/api/v1/request", params={"take": 1, "skip": 0, "filter": "all"})
        if not isinstance(access, dict) or "results" not in access:
            raise SafetyError("Seerr request permissions could not be verified")
        return data

    async def requests(self):
        results = []
        for skip in range(0, 1_000_000, 100):
            page = await self.http.request(
                "GET", "/api/v1/request", params={"take": 100, "skip": skip, "filter": "all"}
            )
            if page is None or "results" not in page:
                raise SafetyError("Seerr requests could not be verified")
            results.extend(page["results"])
            if len(page["results"]) < 100:
                return results
        raise SafetyError("Seerr pagination limit exceeded")

    async def media(self):
        results = []
        for skip in range(0, 1_000_000, 100):
            page = await self.http.request("GET", "/api/v1/media", params={"take": 100, "skip": skip, "filter": "all"})
            if page is None or "results" not in page:
                raise SafetyError("Seerr media inventory could not be verified")
            results.extend(page["results"])
            if len(page["results"]) < 100:
                return results
        raise SafetyError("Seerr media pagination limit exceeded")

    async def availability(self, media_id, remaining, complete=False):
        status = "available" if complete else "partial" if remaining else "deleted"
        result = await self.http.request("POST", f"/api/v1/media/{int(media_id)}/{status}")
        if not result or result.get("status") != (5 if complete else 4 if remaining else 6):
            raise SafetyError("Seerr availability postcondition failed")


class Integrations:
    def __init__(self, db, vault):
        self.db, self.vault = db, vault
        # Stable per installation, independent of API tokens and browser sessions.
        path = db.directory / "plex-client-id"
        with db.lock:
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as file:
                    file.write(uid())
            self.plex_client_id = path.read_text().strip()

    def get(self, kind):
        row = self.db.one("SELECT * FROM integrations WHERE kind=?", (kind,))
        if not row:
            raise SafetyError(f"{kind.capitalize()} integration is not configured")
        secret = self.vault.decrypt(row["secret"])
        headers = {"Accept": "application/json", "X-Plex-Token" if kind == "plex" else "X-Api-Key": secret}
        if kind == "plex":
            headers.update(
                {
                    "X-Plex-Client-Identifier": self.plex_client_id,
                    "X-Plex-Product": "Deletarr",
                    "X-Plex-Version": __version__,
                    "X-Plex-Platform": "Linux",
                    "X-Plex-Device": "Docker",
                }
            )
        http = HTTP(row["url"], headers, bool(row["allow_private"]), row["timeout"], service=kind.capitalize())
        return {"plex": Plex, "radarr": Radarr, "sonarr": Sonarr, "seerr": Seerr}[kind](http)

    def configured(self, kind):
        return bool(self.db.one("SELECT kind FROM integrations WHERE kind=?", (kind,)))

    async def health(self, kind):
        try:
            adapter = self.get(kind)
            data = await (adapter.identity() if kind == "plex" else adapter.status())
            if kind == "plex":
                await adapter.libraries()
            if not data:
                raise SafetyError("Integration health response missing")
            health = "connected"
            self.db.execute("UPDATE integrations SET health=?,checked_at=? WHERE kind=?", (health, now(), kind))
            return {"healthy": True, "version": data.get("version"), "server_id": data.get("machineIdentifier")}
        except SafetyError:
            self.db.execute("UPDATE integrations SET health=?,checked_at=? WHERE kind=?", ("disconnected", now(), kind))
            raise
