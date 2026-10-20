# ADR 0004 — Use supported Seerr availability writes

Status: accepted, 2026-10-20.

Seerr's availability API accepts a JSON request body with `is4k`. Sending a
bodyless POST can be rejected with HTTP 415. Always send JSON and explicitly
target standard availability; mixed-rendition handling remains protected.

The current schema lists `deleted`, but the implementation of
`POST /api/v1/media/:id/:status` has no deleted-state handler. A successful HTTP
response alone therefore does not establish unavailability. Use its supported
`unknown` action (status 1) when no indexed copies remain, `partial` (4) for
remaining TV content, and `available` (5) for a complete remaining copy. Verify
the response status. Do not interpret blocklisted status as deleted.

This represents unavailability without claiming a remote deleted state.
Seerr request workflow and request history remain owned by Seerr. Never invoke
Seerr's DELETE file endpoint to repair metadata after an already completed
physical deletion. Reconciliation rechecks storage and physical-operation
checkpoints before retrying only the availability update.
