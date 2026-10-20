# Integration contracts and research

The adapters target supported APIs rather than screen scraping. Reviewed primary sources on 2026-10-19:

- [Plex Media Server API and authentication](https://developer.plex.tv/pms/)
- [Plex watched-state semantics](https://support.plex.tv/articles/201018487-mark-as-watched-or-unwatched/)
- [Plex watch-state synchronization limitations](https://support.plex.tv/articles/sync-watch-state-and-ratings/)
- [Radarr OpenAPI](https://radarr.video/docs/api/) and [source specification](https://github.com/Radarr/Radarr/blob/develop/src/Radarr.Api.V3/openapi.json)
- [Sonarr OpenAPI](https://sonarr.tv/docs/api/) and [v3 specification](https://github.com/Sonarr/Sonarr/blob/develop/src/Sonarr.Api.V3/openapi.json)
- [Seerr request pagination](https://docs.seerr.dev/api/get-all-requests/) and [availability updates](https://docs.seerr.dev/api/update-media-status/)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [React standalone applications](https://react.dev/learn/build-a-react-app-from-scratch), [Vite](https://vite.dev/guide/)
- [Discord execute webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook), [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage)

Upstream development specifications can change. Contract tests preserve the methods, paths, fields and verification behavior used by this release; live compatibility is still a deployment release gate.

## Plex

Settings → Plex supports the Plex PIN sign-in flow for server connection.
An authenticated local administrator authorizes Plex in a separate tab, then
selects an owned server and one of its advertised connections. The backend
verifies account identity, ownership, server identity and library access before
saving an encrypted server token. Temporary encrypted connection grants expire
after ten minutes and are single-use. No tokens are returned to the browser.
LAN trust remains explicit. Login enrollment is a separate visible checkbox;
only that verified identity may subsequently sign in. Local login remains
available. Environment-managed connections cannot be replaced through this flow.

Uses token headers and JSON responses. `/identity` establishes server identity, `/library/sections` discovers libraries, and paginated section enumeration retrieves media with GUIDs, parts, labels and collections. TV sync visits show/season/episode types in hierarchy order. Renames update metadata; a disappearance marks an item missing and protects it rather than deleting local content.

`/status/sessions/history/all` is paginated and merged into local events. `/api/v2/resources` on plex.tv must confirm ownership of this machine identifier before current multi-user visibility is accepted. Access to owner history does not certify lifetime completeness. No missing record proves never-watched. Watched evidence is any observed Plex user's completed/marked-watched event; latest observed event across users drives last-watch retention. Changes do not erase locally retained history. A first-watch timer uses earliest observed completion, which can be later than the actual historical first watch.

Before deletion, exact source leaf metadata, source file scope, full TV leaf scope and sessions are refreshed. A missing/changed item, unidentified session, target playback or unavailable Plex protects the target. Playback beginning between files stops remaining work conservatively. Artwork is fetched server-side from stored Plex metadata paths and limited to raster image types. No token is returned to the browser.

Plex administrator sign-in uses the PIN flow and server-side account verification. Only an identity explicitly enrolled while locally authenticated can sign in. Local login never depends on Plex.

## Radarr

Uses API v3: status/version, movie/moviefile discovery, monitoring update, exact moviefile delete, rootfolder access and whole-movie removal. `PUT /movie/{id}` sets monitored false and is read back before physical deletion. Whole-record removal uses `deleteFiles=false&addImportExclusion=true`, after exact files are absent; record absence and `/exclusions` TMDB identity are verified.

Unmonitor prevents normal monitored acquisition; it is not an immutable block against every manual user, import list, plugin or future configuration change. Removal requests an explicit list exclusion instead. A movie with other unplanned manager files cannot have its record removed. Disable the manager recycle bin and empty-folder cleanup first. The adapter does not change those settings for you.

## Sonarr

Uses API v3 supported by Sonarr v3/v4. Status/version, series, episode, episodefile and rootfolder APIs are distinct. Episode IDs are unmonitored via `/episode/monitor`; a season/show scope also updates its monitoring scope. Every selected physical file is checked against all Sonarr episodes it represents and the approved Plex season/episode numbers. Missing or extra associations refuse execution. No episode action escalates into series removal.

Whole-show record removal uses `deleteFiles=false&addImportListExclusion=true` after exact files are absent and confirms the TVDB list exclusion. Series with unplanned files are refused. API v5-only servers are not claimed compatible.

## Seerr

Uses an administrator-provided API key, paginated requests and paginated media inventory, including titles with no request record. Association uses normalized TMDB/TVDB identifiers with a media-type boundary and TV TMDB fallback; equal numeric movie and show IDs never imply the same title. Request timestamps protect the requested season or whole movie/show as appropriate. Re-requests are retention signals; Deletarr does not implement or claim enforcement of request blocking in Seerr.

After deletion, standard media aggregate availability is set to `partial` when TV files remain and `unknown` when none remain across associated indexed copies. A fully retained duplicate movie copy remains `available`; an uncertain multipart movie requires manual reconciliation. Status writes send JSON `{ "is4k": false }`, and verify returned standard status codes: unknown=1, partial=4, available=5. The current Seerr source advertises `deleted` in its schema but has no corresponding status-route handler; Deletarr uses the supported unavailable/unknown state rather than reporting a successful deleted-state update. It never calls Seerr's file-deletion endpoint to repair availability. Remaining files must have authorized paths and verified identities. Failure becomes reconciliation; files are never restored or deleted again merely to retry status synchronization. Mixed 4K/standard availability is refused for automatic synchronization because rendition-specific presence is not proven. Per-season Seerr repair is not fabricated through an unsupported write; review/reconcile those states in Seerr.

Compatibility rechecked on 2026-10-20 against [Seerr's schema](https://github.com/seerr-team/seerr/blob/develop/seerr-api.yml), [status route](https://github.com/seerr-team/seerr/blob/develop/server/routes/media.ts), and [status constants](https://github.com/seerr-team/seerr/blob/develop/server/constants/media.ts). The Seerr 415 recovery contract verifies availability-only retries after physical deletion, without repeating unlink or calling another file-deletion API.

## HTTP and notifications

Requests have bounded timeouts and a 32 MiB response ceiling. Safe GET connection failures have bounded exponential retries. Mutations have one attempt: a timeout is ambiguous and must be inspected before any repeat. External response bodies and tokens are not embedded in error strings or logs. No redirects or environment proxy inheritance is allowed by the adapter.

Discord sends plain content with mentions disabled and `wait=true`, then verifies the returned saved-message ID. Telegram checks its `ok` response. Generic webhooks accept any successful HTTP response body. Events persist locally; delivery retries back off and stop after five attempts. Webhook receivers should deduplicate their own processing; exactly-once delivery cannot be guaranteed after an ambiguous network result.

Playback history entries are not automatically assumed complete watches. A positive Plex watched marker is required before promoting an entry into completed-watch evidence; ambiguous entries are retained as playback and revoke affected watch-dependent eligibility. This conservative behavior can protect additional content on Plex versions whose history omits those markers.
