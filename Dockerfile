# syntax=docker/dockerfile:1.7
FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json vite.config.ts ./
COPY web ./web
RUN npm run build

FROM python:3.12-slim-bookworm AS dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.lock ./
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.lock

FROM python:3.12-slim-bookworm AS source
WORKDIR /source
COPY . .
RUN python -c 'import tarfile; archive=tarfile.open("/source.tar.gz","w:gz"); archive.add(".",arcname="deletarr"); archive.close()'

FROM python:3.12-slim-bookworm AS runtime
ARG UID=10001
ARG GID=10001
RUN groupadd --gid ${GID} deletarr && useradd --uid ${UID} --gid ${GID} --no-create-home --shell /usr/sbin/nologin deletarr && mkdir /app /config && chown ${UID}:${GID} /config
COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=root:root deletarr /app/deletarr
COPY --chown=root:root LICENSE NOTICE.md /app/
COPY --chown=root:root licenses /app/licenses
COPY --from=source --chown=root:root /source.tar.gz /app/source.tar.gz
COPY --from=frontend --chown=root:root /build/web/dist /app/web/dist
RUN chmod -R a+rX /app
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DELETARR_CONFIG=/config DELETARR_PORT=8980
WORKDIR /app
USER deletarr:deletarr
VOLUME ["/config"]
EXPOSE 8980
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python","-c","import os,urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+os.getenv('DELETARR_PORT','8980')+'/api/health',timeout=3)"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["python","-m","deletarr"]
