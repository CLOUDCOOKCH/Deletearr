# Third-party notices

Deletarr original application code is AGPL-3.0-or-later. Runtime and frontend dependencies retain their own licenses. The dependency lockfiles identify exact versions used for this release candidate.

React/React DOM, FastAPI, Starlette, Pydantic, httpx, Uvicorn, croniter, Argon2 bindings, cryptography, Vite, TypeScript and Lucide are third-party open-source dependencies; consult their packaged licenses. Manrope is served locally under the SIL Open Font License, retained in the installed @fontsource-variable/manrope package. Lucide uses the ISC license. These libraries are used as dependencies, not relicensed as Deletarr code.

Plex, Radarr, Sonarr, Seerr, Discord and Telegram names identify integrations. Their branding/artwork is not copied into Deletarr. Library artwork, when displayed, is proxied from the administrator's own Plex server.

Redistributed frontend license texts are retained in `licenses/` and included in the container. The container includes the corresponding application source at `/app/source.tar.gz`, downloadable through the authenticated Source code link. Python dependencies retain their installed distribution metadata/license files under `/opt/venv`.
