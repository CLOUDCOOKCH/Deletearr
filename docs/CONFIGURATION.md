# Configuration reference

Deployment environment variables override integration configuration in the database. UI-managed operational settings live in SQLite. Changing environment-managed connection fields in the UI is refused. Environment variables cannot activate live deletion.

| Variable | Default / purpose |
| --- | --- |
| `DELETARR_CONFIG` | `/config`, persistent SQLite/key/setup-token/backup directory |
| `DELETARR_HOST` | `0.0.0.0`, container application interface |
| `DELETARR_CA_BUNDLE` | Optional PEM CA bundle for integration HTTPS; defaults to system trust |
| `DELETARR_LOG_LEVEL` | `info`, Uvicorn log level; request access logging stays disabled |
| `DELETARR_PORT` | `8980`, container application port |
| `DELETARR_WEB` | `/app/web/dist` by project-relative default, compiled UI |
| `DELETARR_ORIGIN` | Incoming exact origin when unset; set explicitly for a reverse proxy |
| `DELETARR_SECURE_COOKIE` | `false`; automatically secure for direct HTTPS; set true behind HTTPS proxy |
| `DELETARR_<KIND>_URL` | Optional environment-owned base URL, KIND = PLEX/RADARR/SONARR/SEERR |
| `DELETARR_<KIND>_SECRET` | Token/API key; prefer protected files |
| `DELETARR_<KIND>_SECRET_FILE` | File overrides inline secret; compatible with Docker secrets |
| `DELETARR_<KIND>_TRUST_PRIVATE` | `false`; explicit trust required for LAN/loopback integrations |

Compose host variables are documented in `.env.example`. `.env` substitution does not automatically inject arbitrary application variables into a container: add them to `environment:`. Configurable build UID/GID default to 10001. Match ownership on `/config` and read/write/unlink permissions on the mounted media trees. No privileged mode, host PID/network namespace, extra capabilities or Docker socket is required by Deletarr.

## Docker secrets

Declare a Compose secret, mount it at `/run/secrets/plex_token`, and set `DELETARR_PLEX_SECRET_FILE` to that path, together with URL and trust flag. The value is read at startup and encrypted before database storage. The local encryption key is `/config/credentials.key`, mode 0600. Back it up separately from the database. Encryption protects a database-only disclosure; someone who can read the entire config volume can read the key too.

The session token is random, opaque and stored only as a SHA-256 digest in SQLite. No static session signing secret is required. Sessions last 12 hours. Password changes revoke all sessions.

## Defaults

Dry-run true; live enrollment false; UTC; minimum library age 7 days; recent playback 24 hours; recent-request protection 30 days. Limits: 20 media leaves, 50 files, 100000000000 logical bytes and estimated reclaimable bytes, and 10% of each library, across a rolling 24-hour window. Review these conservative defaults for small libraries.

Operational settings can be exported without credentials and imported through the reviewed Settings form. Import does not change destructive mode. Rules and integration credentials are reviewed separately; the export is not a silent whole-installation restore. For a full restore, use a protected database backup and matching encryption key.

## LAN URLs and SSRF

Use explicit `http://host:port[/basepath]` or HTTPS URLs, without embedded user/password, query or fragment. DNS addresses are validated and pinned for the outbound TCP request while retaining the correct Host header and TLS SNI. Redirects are not followed. Private destinations require explicit trust. Link-local, multicast, unspecified and known instance-metadata destinations remain forbidden even when LAN access is trusted. Outgoing webhook hosts use the same boundary.

For integration HTTPS with a private CA, provide an appropriate standard system trust store or mount a PEM bundle and set `DELETARR_CA_BUNDLE` in the container. TLS verification is always enabled; there is no insecure checkbox. The application HTTP adapter intentionally does not inherit proxy variables for LAN requests.

## Reverse proxy

Forward to the application HTTP port. Set `DELETARR_ORIGIN=https://deletarr.example.net` and `DELETARR_SECURE_COOKIE=true`. The application does not trust arbitrary forwarded headers and does not need them for authorization. Serve the complete application on one origin (no subpath routing in this release). Apply your reverse proxy's request-size limit, idle timeouts and TLS policy. Do not expose the application unauthenticated on the public Internet.

## Storage

Mount SMB/NFS on the host first, verify it, then bind the mounted media paths. `/config` must use local reliable storage supporting SQLite locks and fsync. Compose refuses to create nonexistent host media paths. Root authorization creates a random `.deletarr-volume-*` marker and records filesystem device/inode identity. A changed or missing root stops cleanup. Reauthorizing an existing root invalidates plans and file identities; run a full sync afterward. No root/mapping is auto-authorized by a path-name suggestion.

Storage mapping suggestions inspect Plex library locations and Radarr/Sonarr root folders, then compare a bounded sample of relative paths and logical sizes against authorized roots. These read-only checks never establish filesystem authority or create mappings without explicit administrator confirmation; a size match alone is not proof of shared storage.
