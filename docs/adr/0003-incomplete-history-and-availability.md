# ADR 0003 — Unknown evidence fails closed

Status: accepted.

Plex owner-visible history can establish observed completed watches across users. It does not prove lifetime completeness. Missing history remains unknown. The condition evaluator uses three-valued logic; NOT(unknown) is unknown. Required unknown data protects the item even for an automatic action.

Plex server ownership is checked against `/api/v2/resources`; current history visibility is revoked before every refresh and restored only after owner verification and successful complete pagination. Observed events are retained locally and merged monotonically. Earliest observed completion is a conservative first-watch reference; it is not represented as a guaranteed first-ever event. Never-watched requires complete lifetime evidence and is not inferred for imported libraries.

Seerr is a retention signal and availability system, not a Deletarr request-blocking system. Recent requests protect movie/show/season scopes. The aggregate standard-status endpoint can mark partial availability without claiming that an entire show is deleted. Mixed 4K/standard and exact per-season availability repair remain explicit reconciliation cases because the current catalog does not prove those remote distinctions reliably.

Consequences: fewer eligible candidates; unsupported operations are refused. No arbitrary scripting, eval or protection overrides are offered. Safety requirements win over convenience.

Playback history entries are not automatically assumed complete watches. A positive Plex watched marker is required before promoting an entry into completed-watch evidence; ambiguous entries are retained as playback and revoke affected watch-dependent eligibility. This conservative behavior can protect additional content on Plex versions whose history omits those markers.
