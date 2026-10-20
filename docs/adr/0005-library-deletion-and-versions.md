# ADR 0005 — Explicit library intent and bounded folder cleanup

Status: accepted, 2026-10-20.

Library deletion and keeping an alternate copy use the existing sealed-plan executor, queue, protections, manager preconditions and reconciliation workflow. A disabled internal rule supplies candidate/state-machine compatibility; immutable manual selections are stored separately and excluded from scheduled retention discovery. Preview is not execution authorization. Submission checks plan age/digest, candidate version, aggregate limits and reviewed mode; live execution additionally confirms exact counts and bytes.

Retained copies have physical-file protection rather than protecting the whole Plex item, which would prevent removing its other editions. Stable provider identifiers group titles; episode coordinates distinguish TV copies. Plex Media entries define editions, with all Part entries kept together. No title-only matching, overlap escalation or inferred whole-series bundles is allowed. Shared files require a containing approved scope.

The administrator requested related sidecars after preview. Enumerate exact adjacent allowlisted sidecars and all parent directories up to, but never including, the authorized root. Revalidate scope and identities before execution; check retained media ownership again before each sidecar removal. Descriptor-relative unlink and empty-only rmdir avoid arbitrary recursive deletion. Unknown files, unplanned subdirectories, symlinks and shared artwork remain. Bounded enumeration refuses overly large scopes. Manager empty-folder recursion remains disabled; Deletarr owns its audited empty-directory pass.

Operations checkpoint each file and folder. Recovery never repeats an ambiguous physical deletion; absent approved sidecars may have been removed by a manager. Retained hardlinks are verified against original sealed identities, allowing only link-count changes attributable to confirmed removed targets. Logical bytes, estimated block recovery and observed free space remain separate.

Schema 5 is additive, backs up existing databases before migration, expires older plans and preserves operating mode. Live Plex/Arr/Seerr and NAS filesystem behavior still require expendable-media verification; contract tests do not establish hardware certification.
