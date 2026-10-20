# Validation and release status

This checkout is an implemented release candidate, not a claim of completed live production certification. No real library was connected or deleted during development. Tests use disposable files, an isolated database and contract transports; browser screenshots use a test-only administrator. Library-action browser tests use intercepted, explicitly named fixture media; these fixtures never contact a live integration or delete real content.

## Local evidence

- Backend behavioral suite: 138 tests passed, including shared-season deletion, file reimports requiring fresh approval, read-only mapping suggestions, current source metadata, and integration configuration changes.
- Nine browser workflows: passed against the real FastAPI backend and built React application, covering authentication, rule creation/simulation, all main navigation pages, dry-run defaults, mobile menu, no horizontal overflow and console exceptions.
- Production frontend build, Python Ruff checks and Prettier formatting: passed.
- npm audit: zero known vulnerabilities after updating Vite/esbuild to patched releases.
- pip-audit of `requirements.lock`: no known vulnerabilities found.
- Linux AMD64 Docker image: built successfully; non-root, read-only rootfs, dropped capabilities and no external network smoke test passed health, static UI, authentication, dry-run, private config permissions and administrator/database persistence after restart.
- Desktop/mobile captures are retained in `docs/screenshots/` and visually inspected. Visual checks cover the current responsive shell; functional recovery is additionally checked through browser regression tests. This is not an accessibility certification.

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
| ARM64 | AMD64 and ARM64 preview images build successfully in CI. ARM64 container execution and ARM NAS hardware remain unverified (no local binfmt emulation). |
| SMB/NFS/UGOS Pro | Not hardware-tested. Verify mount disconnects, stable identities, hardlinks, ACLs, fsync/unlink semantics and host snapshot behavior with expendable media. |
| Registry publication | Public GHCR preview images and immutable commit tags are published by the preview workflow. A versioned production release remains a separate gate. |
| Capacity/performance/accessibility | No large-library load, screen-reader or formal accessibility audit. Profile on the intended host. |

Never equate green mock contracts with a verified live integration. Keep dry-run enabled until the appropriate gates have been checked using expendable test content and reviewed plans.

Validation performed on Linux AMD64, Python 3.12 and Node 24 on 2026-10-20. Source distribution and license texts are included in the non-root image. The local Compose configuration parses successfully. Preview registry publication is verified; no versioned production release or live-library deletion was performed by these checks.

## Repository review regressions

The October 20 review covered the Python modules, React views/helpers, deployment configuration, workflows, locked dependencies and contributor documentation. Existing checks passed before targeted reproductions exposed additional edge cases. Fixes have regression coverage for:

- Multiple storage-pressure plans retaining the same recovery target.
- Atomic duplicate prevention when background jobs are submitted concurrently, plus executor locking during reconciliation to prevent conflicting manual resolutions.
- Manual cleanup/pressure submissions retaining their requested dry-run/live mode, including a mode change during asynchronous validation.
- Keep Forever applying permanent media protection even when a candidate is already temporarily protected.
- Reconciliation recognizing an absent file after its parent folder was removed, retaining mount validation, and recovering after unexpected synchronization errors without repeating physical deletion.
- Clearing recovered operation errors, transient dashboard polling errors and initial sign-in connectivity errors.
- Rejecting non-finite rule/storage values.
- Aggregating show/season sizes without double-counting shared episode files, and excluding detached file records from current indexed-storage metrics while preserving history.
- Reporting running-job cancellation as cancelled rather than completed.

Green tests describe these reproducible cases, not proof that every possible defect or live-service incompatibility has been eliminated.

## Library actions and versions

Twenty-four backend cases cover manual intent without a retention match, no mutation in dry-run, retained-file protection, exact previewed sidecar removal, empty-parent cleanup, unrelated-file retention, folder/symlink changes, shared artwork, stable-ID grouping, multipart editions, TV episode coordinates, hardlinks, active playback, changed kept copies, mode changes, combined limits and schema-4 backup/migration. Browser workflows cover single/bulk preview, exact selections, Versions comparison, releasing protection, mobile layout and live count/byte confirmation. New captures under `test-results/library-*` are disposable fixture evidence, not actual library metrics.
