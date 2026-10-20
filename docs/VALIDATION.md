# Validation and release status

This checkout is an implemented release candidate, not a claim of completed live production certification. No real library was connected or deleted during development. Tests use disposable files, an isolated database and contract transports; browser screenshots use a test-only administrator and no fabricated media catalog.

## Local evidence

- Backend behavioral suite: 73 tests passed, including shared-season deletion, file reimports requiring fresh approval, read-only mapping suggestions, current source metadata, and integration configuration changes.
- Browser workflow: passed against the real FastAPI backend and built React application, covering authentication, rule creation/simulation, all main navigation pages, dry-run defaults, mobile menu, no horizontal overflow and console exceptions.
- Production frontend build, Python Ruff checks and Prettier formatting: passed.
- npm audit: zero known vulnerabilities after updating Vite/esbuild to patched releases.
- pip-audit of `requirements.lock`: no known vulnerabilities found.
- Linux AMD64 Docker image: built successfully; non-root, read-only rootfs, dropped capabilities and no external network smoke test passed health, static UI, authentication, dry-run, private config permissions and administrator/database persistence after restart.
- Desktop/mobile captures are retained in `docs/screenshots/` and visually inspected. The unavailable skill launcher prevented its separate automated design-review workflow; visual review was performed directly. This is not an accessibility certification.

## Safety test coverage

Tests exercise unknown/NOT logic, typed rule validation, excluded libraries, explicit protection, missing watch evidence, request season scope, stale approvals, grace periods, dry-run no-mutation behavior, exact unlink and retained neighboring files, hardlinks, symlink leaves/parents, changed inode/content, missing mount markers, active/unreachable Plex, expired plans, idempotency, downstream failure and reconciliation without repeated unlink, shared episode rejection, manager monitoring ordering, unsafe manager recycling/folder configuration, import-exclusion verification, absent seasons, new downloads at old paths, rolling budgets, persistent queues, restart recovery, timezone schedules, notification deduplication, backups, authentication/CSRF/origin/rate limits, credential-safe validation and SSRF address rules.

## Required external verification before production use

| Gate | Status / necessary evidence |
| --- | --- |
| Plex current server version and owner token | Not live-tested. Verify identity, all-user history visibility, watched markers, sessions, pagination, artwork and changed-scope blocking. Ambiguous playback stays protected. |
| Radarr live API/file deletion | Not live-tested. Verify local/remote path equivalence, recycle/empty-folder settings, exact moviefile deletion and list exclusion. |
| Sonarr live API/file deletion | Not live-tested. Verify episode IDs, season scopes, specials/shared releases and import exclusions on the deployed supported API version. |
| Seerr availability | Not live-tested. Verify recent-request season scope and aggregate partial/deleted behavior. Mixed 4K/per-season repair remains manual reconciliation. |
| Discord/Telegram/webhook delivery | Contract implementation present; actual account/webhook delivery not verified without credentials. |
| ARM64 | Multi-architecture build workflow configured; all 25 locked dependencies resolved to compatible Linux ARM64 or architecture-independent wheels. ARM64 container execution and ARM NAS hardware remain unverified (no local binfmt emulation). |
| SMB/NFS/UGOS Pro | Not hardware-tested. Verify mount disconnects, stable identities, hardlinks, ACLs, fsync/unlink semantics and host snapshot behavior with expendable media. |
| Registry publication | Not performed. Configure repository release environment, run CI gates and publish a versioned release deliberately. |
| Capacity/performance/accessibility | No large-library load, screen-reader or formal accessibility audit. Profile on the intended host. |

Never equate green mock contracts with a verified live integration. Keep dry-run enabled until the appropriate gates have been checked using expendable test content and reviewed plans.

Validation performed on Linux AMD64, Python 3.12 and Node 24 on 2026-10-19. Source distribution and license texts are included in the non-root image. The local Compose configuration parses successfully. No registry push, release publication or live-library deletion was performed.
