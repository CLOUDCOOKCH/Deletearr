# Security policy

Deletarr operates on permanent media deletion. Treat any path escape, authorization bypass, protection bypass, credential disclosure, replay of destructive requests or silent expansion of scope as a security issue.

Use the repository's private vulnerability reporting channel if enabled. Otherwise contact the maintainer privately through the hosting account before filing publicly. This checkout does not invent a maintainer contact address.

The trust boundary includes the administrator, configured integration hosts, Linux host/kernel, authorized media roots and their concurrent writers. It excludes arbitrary untrusted scripts and multi-user public hosting. API credentials and the config volume must remain private. A compromised Plex/manager, hostile writer with media-root access or manager plugin can change remote behavior; no application-level transaction spans those systems.

Security properties: dry-run default; explicit live enrollment; authenticated, CSRF/origin-protected management; owner-verified Plex sign-in; unknown evidence fails closed; exact scope and physical identities; one executor; no recursive Deletarr deletion; bounded HTTP and URL/DNS trust; mutation timeouts enter reconciliation; no secret logging; no telemetry.

Testing should use disposable roots and contract fixtures. Do not probe or delete real libraries as part of an automated security test. Do not publish sensitive watch history, raw tokens or installation keys.
