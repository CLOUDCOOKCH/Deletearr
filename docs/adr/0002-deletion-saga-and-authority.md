# ADR 0002 — Exact plans and a persisted deletion saga

Status: accepted.

A database transaction cannot atomically coordinate Plex, a manager, Seerr and a filesystem. Deletarr records an immutable, hashed, 15-minute plan and runs a single executor with checkpoints.

Authority is the intersection of: enabled library; matching current highest-priority rule and version; approval/grace eligibility; unprotected scope; verified source catalog and playback; trusted mapped root; exact physical identity; required manager connectivity and monitoring state; and deletion budgets. No priority can bypass a protection. Approval does not bypass final validation.

Manager file APIs are preferred, after unmonitor postconditions are read back. Folder-recursive deletion is not requested. Whole-record removal follows exact file deletion with `deleteFiles=false` and a supported import-exclusion option. Unmanaged media uses descriptor-relative unlink of a known regular file. Neighboring extras are not enumerated as Deletarr targets. Manager plugins and side effects remain an external trust boundary.

A checkpoint marked started may have succeeded remotely. Such mutations are not retried blindly. Restart and partial failure produce ReconciliationRequired records. Physical absence under a still-valid mount can confirm a previously attempted file operation. Only availability synchronization can then resume automatically. Ambiguous record removals require operator review.

A rolling 24-hour budget is intentionally stricter than a per-run maximum: splitting manual jobs cannot bypass item/file/byte/library-percentage limits. Failed live attempts conservatively consume their planned scope. Budgets deduplicate physical file and media identities.

Direct permanent deletion has no trash/quarantine. No restoration is attempted after a downstream Seerr error. Retained audit snapshots preserve identity, rules, paths and estimates after Plex removes the item.
