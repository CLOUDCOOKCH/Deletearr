# Internal contributor API

The API is a same-origin frontend implementation detail, not a public unauthenticated API product. `/api/internal/openapi.json` is administrator-protected. The generated schema is also retained as `docs/openapi.json` for contributors; no interactive explorer is exposed.

Except health/authentication bootstrap, endpoints require a valid `deletarr_session` HttpOnly cookie. Mutations require JSON, the exact allowed Origin and the session's `X-CSRF-Token`. `/api/auth/status` returns CSRF only to an authenticated session. Local login is rate-limited; session tokens are not stored in plaintext.

Success bodies are typed domain objects or `{ok:true}`. Refusals use `{error:message}` with HTTP 409; authentication/origin/CSRF failures use 401/403; invalid input is 422 with field names, excluding rejected secrets. Unexpected failures have a generic 500 response. Integrations never return raw error bodies.

| Area | Principal routes |
| --- | --- |
| Authentication | `/auth/status`, `/auth/setup`, `/auth/login`, `/auth/logout`, `/auth/password`, `/auth/plex/start`, `/auth/plex/poll` |
| Dashboard | `/dashboard`, `/streams` |
| Catalog | `/libraries`, `/libraries/discover`, `/media`, `/media/{id}`, `/media/{id}/poster`, `/media/{id}/protection` |
| Library actions | `/library/deletion-plans`, `/library/deletion-plans/execute`, `/versions`, `/versions/{group_id}/keep`, `/library/version-protection` |
| Rules | `/rules`, `/rules/{id}`, `/rules/{id}/simulate` |
| Cleanup | `/candidates`, `/candidates/{id}/action`, `/candidates/{id}/plan`, `/plans/{id}`, `/plans/{id}/execute` |
| Jobs | `/jobs`, `/jobs/{id}/cancel`, `/schedules`, `/schedules/{id}` |
| History | `/history`, `/operations/{id}`, `/reconciliation/{id}/resolve` |
| Integrations | `/integrations`, `/integrations/{kind}`, `/integrations/{kind}/test` |
| Storage | `/storage`, `/storage/roots`, `/storage/mappings`, `/storage/suggestions`, `/storage/pressure`, `/storage/pressure/{id}/preview` |
| Settings | `/settings`, `/settings/live`, `/settings/dry-run`, `/settings/export`, `/backup`, `/diagnostics` |
| Notifications | `/notifications`, `/notifications/{id}/dismiss`, `/destinations`, `/destinations/{id}/test`, `/deliveries` |

Prefix all routes with `/api`. Media/candidates/history/jobs use bounded pagination. Media supports title search, kind/library/protection filters and allowlisted sorting. Conditions use an allowlisted tree with at most eight levels and twenty children per group; they never execute scripts. Rules and settings require their current `version` on update. Candidate actions require their concurrency version. Plan execution requires an exact digest, bounded plan age, current authorization and live-mode count/bytes confirmation.

Worker operations use server-generated idempotency keys. The frontend cannot supply arbitrary file targets to execution. A plan references a server-discovered candidate and exact catalog files. A duplicate queued execution for the same plan is deduplicated; an already consumed plan cannot execute again.

The database and API schema are part of the contributor contract; regenerate OpenAPI after endpoint changes and update contract tests for any third-party method/path/parameter change.

Library preview accepts catalog media UUIDs and optional indexed file UUIDs, never arbitrary filesystem paths. Its response includes exact media files, related sidecars, empty-parent cleanup candidates, retained files, aggregate counts/bytes, mode and live confirmation text. Submit accepts plan ID/digest pairs and the reviewed `expected_dry_run`; a changed mode requires another preview. Manual plans are isolated from retention schedules and require explicit submission. Keeping a version saves physical-file protection on submission, including simulation; releasing it is an explicit authenticated action.
