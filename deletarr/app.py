import errno
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .catalog import Catalog
from .db import Database, encode, now, uid
from .executor import Executor
from .filesystem import Filesystem
from .integrations import HTTP, Integrations, Plex
from .jobs import KINDS, Notifications, Worker
from .rules import Engine, RuleInput
from .security import PASSWORDS, SafetyError, Vault, authenticate, new_session, token_hash, validate_url


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def storage_access_error(path, exc):
    if exc.errno in {errno.EACCES, errno.EPERM}:
        reason = (
            f"Permission denied for container UID {os.geteuid()} / GID {os.getegid()}. "
            "Grant this identity read, write and directory traversal access in your NAS folder permissions. "
            "Authorization must create a small mount marker, even in dry-run mode."
        )
    elif exc.errno == errno.EROFS:
        reason = "The mount is read-only. Mount this media directory read/write so authorization can create its mount marker."
    elif exc.errno == errno.ENOENT:
        reason = "Directory not found inside the container. Add the media bind mount in Compose, redeploy, and use its target path (for example /media/movies), not the NAS /volume1 path."
    elif exc.errno in {errno.ENOTDIR, errno.ELOOP}:
        reason = (
            "The path must be a real directory with no symlink components. Check the bind mount and container path."
        )
    else:
        reason = f"Filesystem access failed ({errno.errorcode.get(exc.errno, 'UNKNOWN')}). Check the NAS mount and available space."
    return SafetyError(f"Cannot authorize {path}: {reason}")


class Login(Strict):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(Strict):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class Setup(Login):
    setup_token: str


class Connection(Strict):
    url: str = Field(max_length=2048)
    secret: str = Field(default="", max_length=4096)
    allow_private: bool = False
    timeout: int = Field(default=15, ge=2, le=60)


class PlexConnect(Strict):
    server_id: str = Field(max_length=256)
    uri: str = Field(max_length=2048)
    allow_private: bool = False
    enable_login: bool = True


class RootInput(Strict):
    path: str = Field(max_length=4096)
    confirmation: Literal["AUTHORIZE THIS MEDIA ROOT"]


class MappingInput(Strict):
    source: Literal["plex", "radarr", "sonarr"]
    prefix: str = Field(min_length=1, max_length=4096)
    root_id: str


class Toggle(Strict):
    enabled: bool


class Protection(Strict):
    protected: bool


class CandidateAction(Strict):
    version: int
    action: Literal["approve", "reject", "postpone", "keep"]
    days: int = Field(default=7, ge=1, le=3650)


class ExecuteInput(Strict):
    digest: str = Field(min_length=64, max_length=64)
    confirmation: str


class SettingsInput(Strict):
    version: int
    timezone: str = "UTC"
    recent_playback_hours: int = Field(default=24, ge=1, le=8760)
    minimum_age_days: int = Field(default=7, ge=1, le=3650)
    request_protection_days: int = Field(default=30, ge=1, le=3650)
    max_items: int = Field(default=20, ge=1, le=10000)
    max_files: int = Field(default=50, ge=1, le=10000)
    max_logical_bytes: int = Field(default=100_000_000_000, ge=1)
    max_reclaimable_bytes: int = Field(default=100_000_000_000, ge=1)
    max_library_percent: int = Field(default=10, ge=1, le=100)
    theme: Literal["light", "dark", "system", "one-piece", "black-clover", "highschool-dxd"] = "system"
    accent: str = Field(default="#b0ed83", pattern=r"^#[0-9a-fA-F]{6}$")
    onboarding_complete: bool = False


class LiveInput(Strict):
    password: str
    acknowledge_permanent_deletion: Literal[True]


class ReconciliationInput(Strict):
    confirmation: Literal["I VERIFIED THE EXTERNAL STATE AND ACCEPT THIS RESOLUTION"]
    explanation: str = Field(min_length=20, max_length=2000)


class ScheduleInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    cron: str = Field(max_length=100)
    timezone: str = "UTC"
    enabled: bool = True
    interval_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


class PressureInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    root_id: str
    threshold: float = Field(gt=0)
    target: float = Field(gt=0)
    unit: Literal["bytes", "percent"] = "bytes"
    never_order: Literal["exclude", "oldest_added", "newest_added"] = "exclude"
    enabled: bool = True


class JobInput(Strict):
    kind: str


class DestinationInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["discord", "telegram", "webhook"]
    url: str = Field(default="", max_length=2048)
    token: str = Field(default="", max_length=1024)
    chat_id: str = Field(default="", max_length=100)
    allow_private: bool = False
    events: list[str] = Field(max_length=30)
    enabled: bool = True


def create_app(directory=None, run_worker=True):
    db = Database(directory or os.getenv("DELETARR_CONFIG", "/config"))
    vault = Vault(db.directory)
    integrations = Integrations(db, vault)
    filesystem = Filesystem(db)
    catalog = Catalog(db, integrations, filesystem)
    engine = Engine(db, catalog, filesystem)
    notifications = Notifications(db, vault)
    executor = Executor(db, catalog, engine, integrations, filesystem, notifications)
    worker = Worker(db, catalog, engine, executor, integrations, filesystem, notifications)
    setup_path = db.directory / "setup-token"
    if not db.one("SELECT id FROM administrator") and not setup_path.exists():
        fd = os.open(setup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(secrets.token_urlsafe(32))
    # Environment credentials override UI connections; never use environment variables to enable deletion.
    for kind in ["plex", "radarr", "sonarr", "seerr"]:
        prefix = "DELETARR_" + kind.upper()
        url = os.getenv(prefix + "_URL")
        secret = os.getenv(prefix + "_SECRET")
        secret_file = os.getenv(prefix + "_SECRET_FILE")
        if secret_file:
            secret = Path(secret_file).read_text().strip()
        if url and secret:
            private = os.getenv(prefix + "_TRUST_PRIVATE", "false").lower() == "true"
            # Transient DNS/integration outages must not prevent local sign-in.
            # Every actual outbound request still resolves, validates and pins its destination.
            validate_url(url, private, resolve=False)
            db.execute(
                """INSERT INTO integrations(kind,url,secret,allow_private) VALUES(?,?,?,?)
                ON CONFLICT(kind) DO UPDATE SET url=excluded.url,secret=excluded.secret,allow_private=excluded.allow_private""",
                (kind, url.rstrip("/"), vault.encrypt(secret), int(private)),
            )

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            worker.start()
        yield
        if run_worker:
            await worker.shutdown()
        db.connection.close()

    app = FastAPI(
        title="Deletarr internal API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.db, app.state.worker, app.state.executor = db, worker, executor
    app.state.integrations, app.state.catalog, app.state.engine = integrations, catalog, engine

    def session(request):
        token = request.cookies.get("deletarr_session", "")
        row = db.one("SELECT * FROM sessions WHERE id=? AND expires>?", (token_hash(token), now()))
        if not row:
            raise HTTPException(401, "Sign in to continue")
        return row

    def cookie(response, request, token):
        secure = os.getenv("DELETARR_SECURE_COOKIE", "false").lower() == "true" or request.url.scheme == "https"
        response.set_cookie(
            "deletarr_session", token, httponly=True, secure=secure, samesite="strict", max_age=43200, path="/"
        )
        response.headers["Cache-Control"] = "no-store"

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.url.path.startswith("/api/"):
            if int(request.headers.get("content-length", "0")) > 1_048_576:
                return JSONResponse({"error": "Request exceeds 1 MiB"}, status_code=413)
            public = request.url.path in {
                "/api/health",
                "/api/auth/status",
                "/api/auth/setup",
                "/api/auth/login",
                "/api/auth/plex/start",
                "/api/auth/plex/poll",
            }
            try:
                if not public:
                    row = session(request)
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
                            raise HTTPException(403, "CSRF token missing or invalid")
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    origin = request.headers.get("origin")
                    expected = os.getenv("DELETARR_ORIGIN") or str(request.base_url).rstrip("/")
                    if origin != expected:
                        raise HTTPException(403, "Request origin is not trusted")
                    if not request.headers.get("content-type", "").startswith("application/json"):
                        raise HTTPException(415, "Send application/json")
            except HTTPException as exc:
                return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SafetyError)
    async def safety_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        # Pydantic's normal response includes rejected input, which may contain passwords/tokens.
        return JSONResponse(
            {"error": "Invalid request", "fields": [str(e["loc"][-1]) for e in exc.errors()]}, status_code=422
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict_error(request, exc):
        return JSONResponse({"error": "This change conflicts with existing data"}, status_code=409)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"error": "Unexpected error. Inspect system diagnostics."}, status_code=500)

    @app.get("/api/health")
    def health():
        db.one("SELECT 1")
        return {"status": "ok", "version": __version__}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        admin = db.one("SELECT username,plex_identity FROM administrator WHERE id=1")
        try:
            row = session(request)
            return {"setup_required": False, "authenticated": True, "csrf": row["csrf"], "username": admin["username"]}
        except HTTPException:
            return {
                "setup_required": admin is None,
                "authenticated": False,
                "plex_login": bool(admin and admin["plex_identity"]),
            }

    @app.post("/api/auth/setup")
    def setup(data: Setup, request: Request, response: Response):
        if db.one("SELECT id FROM administrator"):
            raise SafetyError("Administrator already exists")
        if not setup_path.exists() or not hmac.compare_digest(data.setup_token, setup_path.read_text().strip()):
            raise HTTPException(403, "Setup token invalid; read /config/setup-token on your host")
        if len(data.password) < 12:
            raise SafetyError("Use a password of at least 12 characters")
        password_hash = PASSWORDS.hash(data.password)
        db.execute("INSERT INTO administrator(id,username,password_hash) VALUES(1,?,?)", (data.username, password_hash))
        setup_path.unlink(missing_ok=True)
        token, csrf = new_session(db)
        cookie(response, request, token)
        db.audit("administrator.created")
        return {"csrf": csrf}

    @app.post("/api/auth/login")
    def login(data: Login, request: Request, response: Response):
        token, csrf = authenticate(
            db, data.username, data.password, request.client.host if request.client else "unknown"
        )
        cookie(response, request, token)
        return {"csrf": csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        db.execute("DELETE FROM sessions WHERE id=?", (session(request)["id"],))
        response.delete_cookie("deletarr_session")
        return {"ok": True}

    @app.post("/api/auth/password")
    def password(data: PasswordChange):
        admin = db.one("SELECT * FROM administrator WHERE id=1")
        try:
            PASSWORDS.verify(admin["password_hash"], data.current_password)
        except Exception as exc:
            raise SafetyError("Current password invalid") from exc
        db.execute("UPDATE administrator SET password_hash=? WHERE id=1", (PASSWORDS.hash(data.new_password),))
        db.execute("DELETE FROM sessions")
        return {"ok": True}

    @app.post("/api/auth/plex/start")
    async def plex_start(request: Request, response: Response, enroll: bool = False, connect: bool = False):
        if enroll or connect:
            row = session(request)
            if request.headers.get("x-csrf-token") != row["csrf"]:
                raise HTTPException(403, "CSRF token invalid")
        elif not db.one("SELECT id FROM administrator WHERE plex_identity IS NOT NULL"):
            raise SafetyError("Plex administrator login has not been enrolled")
        if db.one("SELECT COUNT(*) AS n FROM auth_pins WHERE expires>?", (now(),))["n"] > 5:
            raise SafetyError("Too many pending Plex sign-ins")
        client_id = uid()
        headers = {"Accept": "application/json", "X-Plex-Product": "Deletarr", "X-Plex-Client-Identifier": client_id}
        data = await HTTP("https://plex.tv", headers).request("POST", "/api/v2/pins", data={"strong": "true"})
        if not data or not data.get("id") or not data.get("code"):
            raise SafetyError("Plex authentication unavailable")
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO auth_pins VALUES(?,?,?,?,?)",
            (token_hash(token), data["id"], vault.encrypt(client_id), now() + 600, 2 if connect else int(enroll)),
        )
        response.set_cookie(
            "deletarr_plex", token, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=600
        )
        return {
            "url": f"https://app.plex.tv/auth#?clientID={quote(client_id)}&code={quote(data['code'])}&context%5Bdevice%5D%5Bproduct%5D=Deletarr"
        }

    @app.post("/api/auth/plex/poll")
    async def plex_poll(request: Request, response: Response):
        pin = db.one(
            "SELECT * FROM auth_pins WHERE id=? AND expires>?",
            (token_hash(request.cookies.get("deletarr_plex", "")), now()),
        )
        if not pin:
            raise SafetyError("Plex authentication expired")
        if pin["enrolling"]:
            row = session(request)
            if request.headers.get("x-csrf-token") != row["csrf"]:
                raise HTTPException(403, "CSRF token invalid")

        def choices(grant):
            return {
                "pending": False,
                "servers": [
                    {
                        "id": r["clientIdentifier"],
                        "name": r.get("name", "Plex server"),
                        "connections": [c["uri"] for c in r.get("connections", []) if c.get("uri")],
                    }
                    for r in grant["resources"]
                ],
            }

        if pin["enrolling"] == 3:
            return choices(json.loads(vault.decrypt(pin["secret"])))
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": "Deletarr",
            "X-Plex-Client-Identifier": vault.decrypt(pin["secret"]),
        }
        data = await HTTP("https://plex.tv", headers).request("GET", f"/api/v2/pins/{pin['pin_id']}")
        if not data or not data.get("authToken"):
            return {"pending": True}
        identity = await HTTP("https://plex.tv", {**headers, "X-Plex-Token": data["authToken"]}).request(
            "GET", "/api/v2/user"
        )
        if not identity or not identity.get("id"):
            raise SafetyError("Plex account identity could not be verified")
        if pin["enrolling"] == 2:
            resources = await HTTP(
                "https://plex.tv", {**headers, "X-Plex-Token": data["authToken"]}, service="Plex account"
            ).request("GET", "/api/v2/resources", params={"includeHttps": 1})
            if not isinstance(resources, list):
                raise SafetyError("Plex server discovery unavailable")
            owned = [
                r
                for r in resources
                if r.get("owned") is True
                and "server" in str(r.get("provides", "")).split(",")
                and r.get("clientIdentifier")
                and r.get("accessToken")
            ]
            grant = {"identity": str(identity["id"]), "resources": owned}
            db.execute(
                "UPDATE auth_pins SET secret=?,enrolling=3 WHERE id=?", (vault.encrypt(encode(grant)), pin["id"])
            )
            return choices(grant)
        if pin["enrolling"]:
            row = session(request)
            if request.headers.get("x-csrf-token") != row["csrf"]:
                raise HTTPException(403, "CSRF token invalid")
            db.execute("UPDATE administrator SET plex_identity=? WHERE id=1", (str(identity["id"]),))
        else:
            admin = db.one("SELECT plex_identity FROM administrator WHERE id=1")
            if not admin or admin["plex_identity"] != str(identity["id"]):
                raise HTTPException(403, "This Plex account is not the enrolled administrator")
        db.execute("DELETE FROM auth_pins WHERE id=?", (pin["id"],))
        token, csrf = new_session(db)
        cookie(response, request, token)
        return {"csrf": csrf, "pending": False}

    @app.post("/api/integrations/plex/connect")
    async def plex_connect(data: PlexConnect, request: Request, response: Response):
        if os.getenv("DELETARR_PLEX_URL"):
            raise SafetyError("This integration is managed by environment variables")
        pin = db.one(
            "SELECT * FROM auth_pins WHERE id=? AND enrolling=3 AND expires>?",
            (token_hash(request.cookies.get("deletarr_plex", "")), now()),
        )
        if not pin:
            raise SafetyError("Plex connection expired; sign in with Plex again")
        grant = json.loads(vault.decrypt(pin["secret"]))
        resource = next((r for r in grant["resources"] if r["clientIdentifier"] == data.server_id), None)
        if not resource or data.uri not in [c.get("uri") for c in resource.get("connections", [])]:
            raise SafetyError("Select a connection advertised by your owned Plex server")
        url = validate_url(data.uri, data.allow_private)
        headers = {
            "Accept": "application/json",
            "X-Plex-Token": resource["accessToken"],
            "X-Plex-Client-Identifier": integrations.plex_client_id,
            "X-Plex-Product": "Deletarr",
        }
        adapter = Plex(HTTP(url, headers, data.allow_private, service="Plex"))
        identity = await adapter.identity()
        if identity["machineIdentifier"] != data.server_id:
            raise SafetyError("Plex connection identifies a different server")
        await adapter.libraries()
        previous = db.one("SELECT server_id FROM integrations WHERE kind='plex'")
        if previous and previous["server_id"] and previous["server_id"] != data.server_id:
            raise SafetyError(
                "A different Plex server is already indexed; keep its connection or reset the catalog first"
            )
        with db.transaction() as c:
            if (
                c.execute("DELETE FROM auth_pins WHERE id=? AND enrolling=3 AND expires>?", (pin["id"], now())).rowcount
                != 1
            ):
                raise SafetyError("Plex connection expired or was already used; sign in with Plex again")
            c.execute(
                """INSERT INTO integrations(kind,url,secret,allow_private,timeout,health,checked_at,server_id)
                VALUES('plex',?,?,?,15,'connected',?,?) ON CONFLICT(kind) DO UPDATE SET
                url=excluded.url,secret=excluded.secret,allow_private=excluded.allow_private,
                health=excluded.health,checked_at=excluded.checked_at,server_id=excluded.server_id""",
                (url, vault.encrypt(resource["accessToken"]), int(data.allow_private), now(), data.server_id),
            )
            if data.enable_login:
                c.execute("UPDATE administrator SET plex_identity=? WHERE id=1", (grant["identity"],))
            c.execute("UPDATE plans SET expires=0")
            c.execute("UPDATE libraries SET synced_at=NULL")
        response.delete_cookie("deletarr_plex")
        db.audit("integration.plex.connected", detail={"server_id": data.server_id, "login_enabled": data.enable_login})
        return {"ok": True}

    @app.get("/api/dashboard")
    def dashboard():
        counts = {
            row["kind"]: row["n"] for row in db.all("SELECT kind,COUNT(*) n FROM media WHERE missing=0 GROUP BY kind")
        }
        files = db.one("""SELECT COALESCE(SUM(f.size),0) bytes,COUNT(*) n FROM files f WHERE f.deleted=0
            AND EXISTS(SELECT 1 FROM media_files mf JOIN media m ON m.id=mf.media_id
            WHERE mf.file_id=f.id AND m.missing=0)""")
        candidates = db.one(
            "SELECT COUNT(*) n FROM candidates WHERE state NOT IN ('Completed','Rejected','Protected')"
        )["n"]
        protected = db.one("SELECT COUNT(*) n FROM media WHERE protected=1")["n"]
        savings = db.one("""WITH RECURSIVE scope(id) AS (
            SELECT media_id FROM candidates WHERE state IN ('Ready','GracePeriod','PendingApproval')
            UNION SELECT m.id FROM media m JOIN scope ON m.parent_id=scope.id)
            SELECT COALESCE(SUM(size),0) bytes FROM files WHERE deleted=0 AND id IN
            (SELECT mf.file_id FROM media_files mf JOIN scope ON mf.media_id=scope.id)""")["bytes"]
        return {
            "counts": counts,
            "logical_bytes": files["bytes"],
            "files": files["n"],
            "candidates": candidates,
            "protected": protected,
            "candidate_logical_bytes": savings,
            "settings": db.settings(),
            "volumes": filesystem.snapshot(),
            "integrations": db.all("SELECT kind,url,health,checked_at FROM integrations"),
            "recently_watched": db.all(
                "SELECT id,title,last_watched FROM media WHERE last_watched IS NOT NULL ORDER BY last_watched DESC LIMIT 8"
            ),
            "recent_jobs": db.all("SELECT * FROM jobs ORDER BY COALESCE(started_at,available_at) DESC LIMIT 6"),
            "upcoming": db.all("SELECT * FROM schedules WHERE enabled=1 ORDER BY next_at LIMIT 5"),
            "trends": db.all(
                "SELECT date(at,'unixepoch') day,COUNT(*) events FROM audit WHERE event='deletion.completed' GROUP BY day ORDER BY day DESC LIMIT 30"
            ),
        }

    @app.get("/api/libraries")
    def libraries():
        return db.all(
            "SELECT l.*,(SELECT COUNT(*) FROM media m WHERE m.library_id=l.id AND missing=0) items FROM libraries l"
        )

    @app.post("/api/libraries/discover")
    async def discover():
        await catalog.discover()
        return libraries()

    @app.put("/api/libraries/{library_id}")
    def library_toggle(library_id: str, data: Toggle):
        db.execute("UPDATE libraries SET enabled=? WHERE id=?", (int(data.enabled), library_id))
        db.audit("library.updated", library_id, {"enabled": data.enabled})
        return {"ok": True}

    @app.get("/api/media")
    def media(
        q: str = Query("", max_length=200),
        kind: str = "",
        library: str = "",
        protected: bool | None = None,
        sort: Literal["title", "added_at", "last_watched", "year"] = "title",
        direction: Literal["asc", "desc"] = "asc",
        page: int = Query(1, ge=1),
        limit: int = Query(48, ge=1, le=100),
    ):
        where = "WHERE m.missing=0 AND m.title LIKE ?"
        args = ["%" + q + "%"]
        if kind:
            where += " AND m.kind=?"
            args.append(kind)
        if library:
            where += " AND m.library_id=?"
            args.append(library)
        if protected is not None:
            where += " AND m.protected=?"
            args.append(int(protected))
        total = db.one("SELECT COUNT(*) n FROM media m " + where, args)["n"]
        rows = db.all(
            """SELECT m.*,l.title library,(SELECT SUM(f.size) FROM files f JOIN media_files mf ON mf.file_id=f.id
            WHERE mf.media_id=m.id AND f.deleted=0) size,(SELECT state FROM candidates c WHERE c.media_id=m.id ORDER BY eligible_at DESC LIMIT 1) cleanup_state
            FROM media m JOIN libraries l ON l.id=m.library_id """
            + where
            + f" ORDER BY m.{sort} {direction},m.id LIMIT ? OFFSET ?",
            [*args, limit, (page - 1) * limit],
        )
        for row in rows:
            if row["kind"] in {"show", "season"}:
                row["size"] = sum(file["size"] for file in catalog.files(row["id"]))
            metadata = json.loads(row.pop("metadata"))
            row["summary"] = metadata.get("summary")
            row["poster"] = "/api/media/" + row["id"] + "/poster" if metadata.get("thumb") else None
        return {"items": rows, "total": total, "page": page, "limit": limit}

    @app.get("/api/media/{media_id}")
    def media_detail(media_id: str):
        item = db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not item:
            raise HTTPException(404, "Media not found")
        return {
            **item,
            "files": catalog.files(media_id),
            "descendants": catalog.descendants(media_id),
            "watch_events": db.all(
                "SELECT * FROM watch_events WHERE media_id=? ORDER BY watched_at DESC LIMIT 200", (media_id,)
            ),
            "external_ids": db.all("SELECT * FROM external_ids WHERE media_id=?", (media_id,)),
            "requests": db.all("SELECT * FROM requests WHERE media_id=?", (media_id,)),
            "evaluations": db.all(
                "SELECT * FROM evaluations WHERE media_id=? ORDER BY created_at DESC LIMIT 10", (media_id,)
            ),
            "candidates": db.all("SELECT * FROM candidates WHERE media_id=?", (media_id,)),
        }

    @app.get("/api/media/{media_id}/poster")
    async def poster(media_id: str):
        item = db.one("SELECT metadata FROM media WHERE id=?", (media_id,))
        path = json.loads(item["metadata"]).get("thumb", "") if item else ""
        if not path.startswith("/library/metadata/") or ".." in path or "?" in path:
            raise HTTPException(404, "Artwork unavailable")
        result = await integrations.get("plex").http.request("GET", path)
        if not isinstance(result, tuple) or result[1].split(";")[0] not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(404, "Artwork unavailable")
        return Response(result[0], media_type=result[1])

    @app.put("/api/media/{media_id}/protection")
    def media_protect(media_id: str, data: Protection):
        db.execute("UPDATE media SET protected=? WHERE id=?", (int(data.protected), media_id))
        db.audit("media.protection", media_id, {"protected": data.protected})
        return {"ok": True}

    @app.get("/api/rules")
    def rules():
        return db.all("SELECT * FROM rules ORDER BY priority,id")

    def save_rule(rule_id, data, creating=False):
        values = data.model_dump()
        conditions = encode(values.pop("conditions"))
        version = values.pop("version")
        if data.library_id and not db.one("SELECT id FROM libraries WHERE id=?", (data.library_id,)):
            raise SafetyError("Library does not exist")
        if creating:
            db.execute(
                """INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rule_id,
                    data.name,
                    data.description,
                    int(data.enabled),
                    data.priority,
                    data.library_id,
                    data.kind,
                    data.action,
                    data.grace_days,
                    data.min_age_days,
                    data.strategy,
                    1,
                    conditions,
                    now(),
                    now(),
                ),
            )
        else:
            count = db.execute(
                """UPDATE rules SET name=?,description=?,enabled=?,priority=?,library_id=?,kind=?,action=?,
                grace_days=?,min_age_days=?,strategy=?,version=version+1,conditions=?,updated_at=? WHERE id=? AND version=?""",
                (
                    data.name,
                    data.description,
                    int(data.enabled),
                    data.priority,
                    data.library_id,
                    data.kind,
                    data.action,
                    data.grace_days,
                    data.min_age_days,
                    data.strategy,
                    conditions,
                    now(),
                    rule_id,
                    version,
                ),
            )
            if not count:
                raise SafetyError("Rule changed; reload before saving")
            db.execute(
                "UPDATE candidates SET state='Protected',version=version+1,approved_at=NULL WHERE rule_id=? AND state NOT IN ('Completed','Executing','Synchronizing','ReconciliationRequired')",
                (rule_id,),
            )
            worker.enqueue("evaluate")
        db.audit("rule.saved", rule_id)
        return db.one("SELECT * FROM rules WHERE id=?", (rule_id,))

    @app.post("/api/rules")
    def create_rule(data: RuleInput):
        return save_rule(uid(), data, True)

    @app.put("/api/rules/{rule_id}")
    def update_rule(rule_id: str, data: RuleInput):
        return save_rule(rule_id, data)

    @app.post("/api/rules/{rule_id}/simulate")
    def simulate(rule_id: str):
        results = engine.run(rule_id)
        files = {f["id"]: f for result in results if result["eligible"] for f in catalog.files(result["media_id"])}
        return {
            "items": results,
            "matches": sum(r["matched"] for r in results),
            "eligible": sum(r["eligible"] for r in results),
            "logical_bytes": sum(f["size"] for f in files.values()),
            "estimated_reclaimable": sum(
                json.loads(f["identity"])["reclaimable"] for f in files.values() if f["identity"]
            ),
        }

    @app.get("/api/candidates")
    def candidates(
        q: str = Query("", max_length=200),
        state: str = "",
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=100),
    ):
        where = "WHERE m.title LIKE ?" + (" AND c.state=?" if state else "")
        args = ["%" + q + "%"] + ([state] if state else [])
        rows = db.all(
            """SELECT c.*,m.title,m.kind,l.title library,r.name rule_name FROM candidates c JOIN media m ON m.id=c.media_id
            JOIN rules r ON r.id=c.rule_id JOIN libraries l ON l.id=m.library_id """
            + where
            + " ORDER BY eligible_at DESC LIMIT ? OFFSET ?",
            [*args, limit, (page - 1) * limit],
        )
        for row in rows:
            row["files"] = catalog.files(row["media_id"])
            row["logical_bytes"] = sum(f["size"] for f in row["files"])
            leaves = [m for m in catalog.descendants(row["media_id"]) if m["kind"] in {"movie", "episode"}]
            row["last_watched"] = max((m["last_watched"] or 0 for m in leaves), default=0) or None
            row["history_visible"] = bool(leaves) and all(m["history_visible"] for m in leaves)
            ancestors = catalog.ancestors(row["media_id"])
            artwork = sorted(ancestors, key=lambda m: m["kind"] != "show")
            source = next((m for m in artwork if json.loads(m["metadata"]).get("thumb")), None)
            row["poster"] = f"/api/media/{source['id']}/poster" if source else None
        return {
            "items": rows,
            "total": db.one("SELECT COUNT(*) n FROM candidates c JOIN media m ON m.id=c.media_id " + where, args)["n"],
        }

    @app.post("/api/candidates/{candidate_id}/action")
    def candidate_action(candidate_id: str, data: CandidateAction):
        item = db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
        if not item or item["version"] != data.version:
            raise SafetyError("Candidate changed; reload before acting")
        target = {"approve": "Ready", "reject": "Rejected", "postpone": "Postponed", "keep": "Protected"}[data.action]
        if target == item["state"] and data.action != "keep":
            return {"ok": True}
        if target != item["state"]:
            executor.transition(candidate_id, target, expected_version=data.version)
        if data.action == "approve":
            db.execute("UPDATE candidates SET approved_at=? WHERE id=?", (now(), candidate_id))
        elif data.action == "postpone":
            db.execute("UPDATE candidates SET postponed_until=? WHERE id=?", (now() + data.days * 86400, candidate_id))
        elif data.action == "keep":
            db.execute("UPDATE media SET protected=1 WHERE id=?", (item["media_id"],))
        return {"ok": True}

    @app.post("/api/candidates/{candidate_id}/plan")
    def plan(candidate_id: str):
        return executor.build(candidate_id)

    @app.get("/api/plans/{plan_id}")
    def get_plan(plan_id: str):
        row = db.one("SELECT * FROM plans WHERE id=?", (plan_id,))
        if not row:
            raise HTTPException(404, "Plan not found")
        return {**row, "snapshot": json.loads(row["snapshot"])}

    @app.post("/api/plans/{plan_id}/execute")
    def execute_plan(plan_id: str, data: ExecuteInput):
        plan = db.one("SELECT * FROM plans WHERE id=?", (plan_id,))
        if not plan or plan["digest"] != data.digest:
            raise SafetyError("Plan does not match")
        snapshot = json.loads(plan["snapshot"])
        required = f"DELETE {len([n for n in snapshot['affected'] if n['kind'] in {'movie', 'episode'}])} ITEMS {snapshot['logical_bytes']} BYTES"
        if not db.settings()["dry_run"] and data.confirmation != required:
            raise SafetyError("Confirm exactly: " + required)
        return {
            "job_id": worker.enqueue(
                "cleanup",
                {"plan_id": plan_id, "digest": data.digest, "expected_dry_run": db.settings()["dry_run"]},
                "cleanup-plan:" + plan_id,
            )
        }

    @app.get("/api/integrations")
    def list_integrations():
        return [
            {
                **row,
                "configured": True,
                "environment_managed": bool(os.getenv("DELETARR_" + row["kind"].upper() + "_URL")),
            }
            for row in db.all("SELECT kind,url,allow_private,timeout,health,checked_at,server_id FROM integrations")
        ]

    @app.put("/api/integrations/{kind}")
    def save_integration(kind: Literal["plex", "radarr", "sonarr", "seerr"], data: Connection):
        if os.getenv("DELETARR_" + kind.upper() + "_URL"):
            raise SafetyError("This integration is managed by environment variables")
        url = validate_url(data.url, data.allow_private)
        existing = db.one("SELECT * FROM integrations WHERE kind=?", (kind,))
        secret = vault.encrypt(data.secret) if data.secret else existing["secret"] if existing else None
        if not secret:
            raise SafetyError("Integration credential is required")
        db.execute(
            """INSERT INTO integrations(kind,url,secret,allow_private,timeout) VALUES(?,?,?,?,?)
            ON CONFLICT(kind) DO UPDATE SET url=excluded.url,secret=excluded.secret,allow_private=excluded.allow_private,
            timeout=excluded.timeout,health=NULL""",
            (kind, url, secret, int(data.allow_private), data.timeout),
        )
        db.audit("integration.configured", kind)
        db.execute("UPDATE plans SET expires=0")
        if kind in {"radarr", "sonarr"}:
            db.execute("UPDATE files SET manager=NULL,manager_id=NULL,manager_parent=NULL WHERE manager=?", (kind,))
        db.execute("UPDATE libraries SET synced_at=NULL")
        return {"ok": True}

    @app.post("/api/integrations/{kind}/test")
    async def test_integration(kind: Literal["plex", "radarr", "sonarr", "seerr"]):
        return await integrations.health(kind)

    @app.get("/api/streams")
    async def streams():
        return [
            {"title": x.get("title"), "plex_key": x.get("ratingKey")} for x in await integrations.get("plex").sessions()
        ]

    @app.get("/api/storage")
    def storage():
        return {"roots": filesystem.snapshot(), "mappings": db.all("SELECT * FROM mappings")}

    @app.post("/api/storage/roots")
    def add_root(data: RootInput):
        try:
            ident = filesystem.register(data.path)
        except OSError as exc:
            raise storage_access_error(data.path, exc) from exc
        db.audit("storage.authorized", ident, {"path": data.path})
        return {"id": ident}

    @app.post("/api/storage/mappings")
    def add_mapping(data: MappingInput):
        prefix = Path(data.prefix)
        if not prefix.is_absolute() or ".." in prefix.parts or "\\" in data.prefix:
            raise SafetyError("Use an absolute POSIX source prefix")
        filesystem.root(data.root_id)
        for previous in db.all("SELECT prefix FROM mappings WHERE source=?", (data.source,)):
            other = Path(previous["prefix"])
            if prefix.is_relative_to(other) or other.is_relative_to(prefix):
                raise SafetyError("Ambiguous overlapping source mappings")
        ident = uid()
        db.execute("INSERT INTO mappings VALUES(?,?,?,?)", (ident, data.source, str(prefix), data.root_id))
        db.audit("mapping.created", ident, data.model_dump())
        return {"id": ident}

    @app.post("/api/storage/roots/{root_id}/reauthorize")
    def reauthorize_root(root_id: str, data: RootInput):
        root = db.one("SELECT * FROM roots WHERE id=?", (root_id,))
        if not root or root["path"] != data.path:
            raise SafetyError("Confirm the exact existing media root path")
        if executor.lock.locked():
            raise SafetyError("Wait for the current execution to finish before reauthorizing storage")
        try:
            filesystem.reauthorize(root_id)
        except OSError as exc:
            raise storage_access_error(data.path, exc) from exc
        return {"ok": True, "next_step": "Run a full library sync to refresh every file identity"}

    @app.get("/api/jobs")
    def jobs(page: int = Query(1, ge=1)):
        return db.all(
            "SELECT * FROM jobs ORDER BY COALESCE(started_at,available_at) DESC LIMIT 100 OFFSET ?", ((page - 1) * 100,)
        )

    @app.get("/api/storage/pressure")
    def pressure_policies():
        return db.all("SELECT * FROM pressure_policies")

    @app.post("/api/storage/pressure")
    def create_pressure_policy(data: PressureInput):
        filesystem.root(data.root_id)
        if data.target <= data.threshold or (data.unit == "percent" and data.target > 100):
            raise SafetyError("Recovery target must exceed the threshold and percentages must be at most 100")
        ident = uid()
        db.execute(
            "INSERT INTO pressure_policies VALUES(?,?,?,?,?,?,?,?)",
            (
                ident,
                data.name,
                data.root_id,
                data.threshold,
                data.target,
                data.unit,
                data.never_order,
                int(data.enabled),
            ),
        )
        db.audit("storage.pressure_policy_created", ident)
        return {"id": ident}

    @app.post("/api/storage/pressure/{policy_id}/preview")
    def preview_pressure(policy_id: str):
        from .storage import StoragePlanner

        policy = db.one("SELECT * FROM pressure_policies WHERE id=?", (policy_id,))
        if not policy:
            raise HTTPException(404, "Pressure policy not found")
        return StoragePlanner(db, catalog, engine, executor, filesystem).select(policy)

    @app.delete("/api/storage/pressure/{policy_id}")
    def remove_pressure_policy(policy_id: str):
        db.execute("DELETE FROM pressure_policies WHERE id=?", (policy_id,))
        return {"ok": True}

    @app.get("/api/storage/suggestions")
    async def mapping_suggestions_endpoint():
        from .storage import mapping_suggestions

        return await mapping_suggestions(db, integrations, filesystem)

    @app.post("/api/jobs")
    def submit_job(data: JobInput):
        payload = {"expected_dry_run": db.settings()["dry_run"]} if data.kind in {"cleanup", "pressure"} else None
        return {"id": worker.enqueue(data.kind, payload)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return {"ok": True, "note": "An in-flight external operation must finish; remaining work will stop"}

    @app.get("/api/schedules")
    def schedules():
        return db.all("SELECT * FROM schedules ORDER BY next_at")

    @app.post("/api/schedules")
    def add_schedule(data: ScheduleInput):
        if data.kind not in KINDS or not croniter.is_valid(data.cron) or len(data.cron.split()) != 5:
            raise SafetyError("Use a supported job type and a five-field cron expression")
        try:
            next_at = int(
                croniter(data.cron, datetime.fromtimestamp(now(), ZoneInfo(data.timezone)))
                .get_next(datetime)
                .timestamp()
            )
            if data.interval_seconds:
                next_at = now() + data.interval_seconds
        except Exception as exc:
            raise SafetyError("Invalid schedule timezone") from exc
        ident = uid()
        db.execute(
            "INSERT INTO schedules(id,name,kind,cron,timezone,enabled,next_at,interval_seconds) VALUES(?,?,?,?,?,?,?,?)",
            (ident, data.name, data.kind, data.cron, data.timezone, int(data.enabled), next_at, data.interval_seconds),
        )
        return {"id": ident}

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id: str):
        db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        return {"ok": True}

    @app.get("/api/history")
    def history(page: int = Query(1, ge=1)):
        return {
            "operations": db.all(
                """SELECT o.*,p.snapshot FROM operations o JOIN plans p ON p.id=o.plan_id
                ORDER BY o.created_at DESC LIMIT 50 OFFSET ?""",
                ((page - 1) * 50,),
            ),
            "audit": db.all("SELECT * FROM audit ORDER BY at DESC LIMIT 100 OFFSET ?", ((page - 1) * 100,)),
            "reconciliation": db.all("SELECT * FROM reconciliation ORDER BY created_at DESC LIMIT 100"),
        }

    @app.get("/api/operations/{operation_id}")
    def operation_detail(operation_id: str):
        return {
            "operation": db.one("SELECT * FROM operations WHERE id=?", (operation_id,)),
            "checkpoints": db.all(
                "SELECT * FROM checkpoints WHERE operation_id=? ORDER BY created_at", (operation_id,)
            ),
        }

    @app.post("/api/reconciliation/{task_id}/resolve")
    def resolve_reconciliation(task_id: str, data: ReconciliationInput):
        if executor.lock.locked():
            raise SafetyError("Wait for the current executor operation to finish")
        task = db.one("SELECT * FROM reconciliation WHERE id=? AND state='pending'", (task_id,))
        if not task:
            raise SafetyError("Pending reconciliation task not found")
        operation = db.one("SELECT * FROM operations WHERE id=?", (task["operation_id"],))
        plan = db.one("SELECT * FROM plans WHERE id=?", (operation["plan_id"],))
        snapshot = json.loads(plan["snapshot"])
        for file in snapshot["files"]:
            if filesystem.exists(file["root_id"], file["path"]):
                raise SafetyError("Physical media still exists; do not acknowledge an incomplete deletion")
        with db.transaction() as c:
            c.execute(
                "UPDATE reconciliation SET state='resolved_manually',reason=? WHERE id=?", (data.explanation, task_id)
            )
            c.execute(
                "UPDATE operations SET state='ReconciledManually',error=?,updated_at=? WHERE id=?",
                ("Administrator attestation: " + data.explanation, now(), operation["id"]),
            )
            for file in snapshot["files"]:
                c.execute("UPDATE files SET deleted=1 WHERE id=?", (file["id"],))
        state = db.one("SELECT state FROM candidates WHERE id=?", (snapshot["candidate_id"],))["state"]
        if state == "ReconciliationRequired":
            executor.transition(snapshot["candidate_id"], "Completed", {"manual_reconciliation": True})
        db.audit(
            "reconciliation.resolved_manually",
            operation["id"],
            {"explanation": data.explanation, "automatic_postconditions_verified": False},
        )
        return {"ok": True, "verification": "administrator_attestation", "note": "No external mutation was repeated"}

    @app.get("/api/settings")
    def settings():
        return db.settings()

    @app.put("/api/settings")
    def save_settings(data: SettingsInput):
        try:
            ZoneInfo(data.timezone)
        except Exception as exc:
            raise SafetyError("Invalid IANA timezone") from exc
        old = db.settings()
        if old["version"] != data.version:
            raise SafetyError("Settings changed; reload before saving")
        values = {**old, **data.model_dump()}
        values.pop("version", None)
        count = db.execute(
            "UPDATE settings SET data=?,version=version+1 WHERE id=1 AND version=?", (encode(values), data.version)
        )
        if not count:
            raise SafetyError("Settings changed; reload before saving")
        db.audit("settings.updated")
        return db.settings()

    @app.post("/api/settings/live")
    def enable_live(data: LiveInput):
        admin = db.one("SELECT * FROM administrator WHERE id=1")
        try:
            PASSWORDS.verify(admin["password_hash"], data.password)
        except Exception as exc:
            raise SafetyError("Administrator password invalid") from exc
        old = db.settings()
        if not db.one("SELECT id FROM roots WHERE confirmed=1"):
            raise SafetyError("Authorize and validate media storage before enabling live deletion")
        old.update({"dry_run": False, "live_enrolled": True})
        old.pop("version")
        db.execute("UPDATE settings SET data=?,version=version+1 WHERE id=1", (encode(old),))
        db.audit("deletion.live_enabled")
        return db.settings()

    @app.post("/api/settings/dry-run")
    def enable_dry_run():
        old = db.settings()
        old["dry_run"] = True
        old.pop("version")
        db.execute("UPDATE settings SET data=?,version=version+1 WHERE id=1", (encode(old),))
        db.audit("deletion.dry_run_enabled")
        return db.settings()

    @app.get("/api/settings/export")
    def export_settings():
        exported = db.settings()
        exported.update({"dry_run": True, "live_enrolled": False})
        return {
            "format": 1,
            "settings": exported,
            "rules": rules(),
            "integrations": list_integrations(),
            "note": "No credentials, viewing history, media paths or sessions included. Import settings through the reviewed settings form.",
        }

    @app.post("/api/backup")
    def backup():
        path = db.backup()
        db.audit("database.backup", detail={"filename": path.name})
        return {
            "filename": path.name,
            "contains_credentials": True,
            "note": "Protected local backup; copy credentials.key separately for disaster recovery",
        }

    @app.get("/api/diagnostics")
    def diagnostics():
        with db.lock:
            integrity = db.connection.execute("PRAGMA quick_check").fetchone()[0]
        return {
            "version": __version__,
            "database": integrity,
            "schema": db.one("SELECT MAX(version) version FROM schema_version"),
            "config_writable": os.access(db.directory, os.W_OK),
            "worker_running": bool(worker.task and not worker.task.done()),
            "destructive_executor": "single",
            "roots": filesystem.snapshot(),
            "integrations": list_integrations(),
            "pending_reconciliation": db.one("SELECT COUNT(*) n FROM reconciliation WHERE state='pending'")["n"],
        }

    @app.get("/api/notifications")
    def list_notifications():
        return db.all("SELECT * FROM notifications WHERE dismissed=0 ORDER BY created_at DESC LIMIT 100")

    @app.get("/api/deliveries")
    def notification_deliveries():
        return db.all(
            "SELECT id,notification_id,destination_id,state,attempts,error,available_at FROM deliveries ORDER BY available_at DESC LIMIT 100"
        )

    @app.post("/api/notifications/{notification_id}/dismiss")
    def dismiss(notification_id: str):
        db.execute("UPDATE notifications SET dismissed=1 WHERE id=?", (notification_id,))
        return {"ok": True}

    @app.get("/api/destinations")
    def destinations():
        return db.all("SELECT id,name,kind,events,enabled FROM destinations")

    @app.post("/api/destinations")
    def add_destination(data: DestinationInput):
        config = {"url": data.url, "allow_private": data.allow_private}
        if data.kind == "telegram":
            if not data.token or not data.chat_id or "/" in data.token:
                raise SafetyError("Telegram token and chat ID are required")
            config.update({"token": data.token, "chat_id": data.chat_id})
        else:
            validate_url(data.url, data.allow_private)
        if data.kind == "discord" and not data.url.startswith("https://discord.com/api/webhooks/"):
            raise SafetyError("Use an HTTPS discord.com webhook URL")
        ident = uid()
        db.execute(
            "INSERT INTO destinations VALUES(?,?,?,?,?,?)",
            (ident, data.name, data.kind, vault.encrypt(encode(config)), encode(data.events), int(data.enabled)),
        )
        return {"id": ident}

    @app.post("/api/destinations/{destination_id}/test")
    async def test_destination(destination_id: str):
        destination = db.one("SELECT * FROM destinations WHERE id=?", (destination_id,))
        if not destination:
            raise HTTPException(404, "Destination not found")
        await notifications.send(destination, "Test notification from Deletarr")
        return {"ok": True}

    @app.delete("/api/destinations/{destination_id}")
    def delete_destination(destination_id: str):
        db.execute("DELETE FROM deliveries WHERE destination_id=?", (destination_id,))
        db.execute("DELETE FROM destinations WHERE id=?", (destination_id,))
        return {"ok": True}

    @app.get("/api/internal/openapi.json")
    def internal_openapi():
        return app.openapi()

    @app.get("/api/source")
    def corresponding_source():
        path = Path(__file__).parent.parent / "source.tar.gz"
        if not path.is_file():
            raise HTTPException(
                404,
                "The source archive is included in container distributions; development source is the local checkout",
            )
        return FileResponse(path, filename="deletarr-source.tar.gz", media_type="application/gzip")

    @app.get("/api/license")
    def application_license():
        return FileResponse(Path(__file__).parent.parent / "LICENSE", media_type="text/plain")

    dist = Path(os.getenv("DELETARR_WEB", str(Path(__file__).parent.parent / "web" / "dist")))
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        if (dist / "themes").is_dir():
            app.mount("/themes", StaticFiles(directory=dist / "themes"), name="themes")

        @app.get("/{path:path}")
        def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Endpoint not found")
            return FileResponse(dist / "index.html")

    return app
