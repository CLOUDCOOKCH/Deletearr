import React, {
  useEffect,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  CalendarClock,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Film,
  FolderOpen,
  HardDrive,
  History as HistoryIcon,
  LayoutDashboard,
  ListFilter,
  Loader2,
  LockKeyhole,
  LogOut,
  Menu,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Tv,
  X,
  XCircle,
} from "lucide-react";
import "@fontsource-variable/manrope";
import { api, bytes, date, setCsrf, setTimezone } from "./api";
import "./style.css";
import { PressureSettings } from "./PressureSettings";
import { MappingSuggestions } from "./MappingSuggestions";
import { AppearanceSettings, animeThemes } from "./AppearanceSettings";
import "./themes.css";
import "./workspace.css";

type Row = Record<string, any>;
type Condition = {
  group?: "and" | "or" | "not" | null;
  children?: Condition[];
  field?: string | null;
  operator?: string;
  value?: string | number | boolean | null;
};
let notify: (text: string, error?: boolean) => void = () => {};
function useData<T = any>(path: string, revision = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    const fetchData = () =>
      api<T>(path)
        .then((x) => {
          if (active) setData(x);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    fetchData();
    const timer =
      /^\/(dashboard|candidates|jobs|history|notifications|streams)/.test(path)
        ? window.setInterval(fetchData, 5000)
        : undefined;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [path, revision]);
  return { data, error };
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge " + tone}>{children}</span>;
}
function Empty({
  icon: Icon = FolderOpen,
  title,
  children,
  action,
}: {
  icon?: any;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Icon size={34} strokeWidth={1.3} />
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
function ErrorBox({ text }: { text: string }) {
  return text ? (
    <div role="alert" className="error">
      <XCircle size={17} />
      {text}
    </div>
  ) : null;
}
function Loading() {
  return (
    <div className="loading">
      <Loader2 className="spin" size={22} />
      Loading your library…
    </div>
  );
}
function Button({
  children,
  onClick,
  kind = "",
  disabled = false,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: string;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={"button " + kind}
    >
      {children}
    </button>
  );
}
function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}) {
  const id = React.useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.isValidElement(children)
        ? React.cloneElement(children as React.ReactElement<any>, {
            id,
            "aria-describedby": help ? id + "-help" : undefined,
          })
        : children}
      {help && <small id={id + "-help"}>{help}</small>}
    </div>
  );
}
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = React.useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => {
      ref.current?.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="modal-head">
        <h2>{title}</h2>
        <button
          aria-label="Close dialog"
          className="icon-button"
          onClick={close}
        >
          <X />
        </button>
      </div>
      {children}
    </dialog>
  );
}
async function act(path: string, method = "POST", body: unknown = {}) {
  try {
    const result = await api(path, method, body);
    notify("Saved successfully");
    return result;
  } catch (e) {
    notify((e as Error).message, true);
    throw e;
  }
}
function parse(text: string | object) {
  return typeof text === "string" ? JSON.parse(text) : text;
}

function Auth({ status, onLogin }: { status: Row; onLogin: () => void }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [plex, setPlex] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const r = await api(
        status.setup_required ? "/auth/setup" : "/auth/login",
        "POST",
        data,
      );
      setCsrf(r.csrf);
      onLogin();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function plexLogin() {
    try {
      const r = await api("/auth/plex/start", "POST", {});
      window.open(r.url, "_blank", "noopener,noreferrer");
      setPlex(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="auth-world">
      <div className="auth-intro">
        <Logo />
        <div>
          <h1>
            A little room
            <br />
            for what’s next.
          </h1>
          <p>
            Intelligent media retention.
            <br />
            Your library, thoughtfully kept.
          </p>
        </div>
        <span>
          <ShieldCheck size={18} />
          Self-hosted. Private. In your control.
        </span>
      </div>
      <main className="auth-main">
        <div className="auth-form">
          <Badge tone="green">Dry-run by default</Badge>
          <h2>
            {status.setup_required ? "Make yourself at home." : "Welcome back."}
          </h2>
          <p>
            {status.setup_required
              ? "Create your single administrator account to start managing your library."
              : "Sign in to see what stays, what goes, and why."}
          </p>
          <form onSubmit={submit}>
            <Field label="Username">
              <input
                name="username"
                autoComplete="username"
                required
                maxLength={100}
              />
            </Field>
            <Field
              label="Password"
              help={
                status.setup_required
                  ? "At least 12 characters. Local login works even when Plex is offline."
                  : undefined
              }
            >
              <input
                name="password"
                type="password"
                autoComplete={
                  status.setup_required ? "new-password" : "current-password"
                }
                required
                minLength={status.setup_required ? 12 : 1}
              />
            </Field>
            {status.setup_required && (
              <Field
                label="Installation setup token"
                help="Read /config/setup-token on your Docker host. This prevents someone else claiming your installation."
              >
                <input
                  name="setup_token"
                  type="password"
                  required
                  autoComplete="off"
                />
              </Field>
            )}
            <ErrorBox text={error} />
            <Button type="submit" kind="primary" disabled={busy}>
              {busy ? <Loader2 className="spin" size={16} /> : null}
              {status.setup_required ? "Create administrator" : "Sign in"}
              <ArrowRight size={16} />
            </Button>
          </form>
          {status.plex_login && (
            <Button onClick={plexLogin}>Sign in with Plex</Button>
          )}
          {plex && (
            <Button
              onClick={async () => {
                const r = await api("/auth/plex/poll", "POST", {});
                if (r.pending) notify("Finish authentication in Plex first");
                else {
                  setCsrf(r.csrf);
                  onLogin();
                }
              }}
            >
              I’ve signed in to Plex
            </Button>
          )}
          <small>
            Permanent deletion remains off until you explicitly enable it.
          </small>
        </div>
      </main>
    </div>
  );
}
function Logo() {
  return (
    <span className="logo">
      <span className="logo-mark">
        <ArrowDownToLine size={23} strokeWidth={2.5} />
      </span>
      deletarr<span className="logo-dot">.</span>
    </span>
  );
}
const nav = [
  ["Dashboard", LayoutDashboard],
  ["Libraries", Film],
  ["Rules", SlidersHorizontal],
  ["Cleanup Queue", ListFilter],
  ["History", HistoryIcon],
  ["Statistics", Activity],
  ["Notifications", Bell],
  ["Settings", SettingsIcon],
] as const;
function pageFromHash() {
  try {
    return decodeURIComponent(location.hash.slice(1)) || "Dashboard";
  } catch {
    return "Dashboard";
  }
}
function App() {
  const [smallScreen, setSmallScreen] = useState(window.innerWidth <= 640);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 640px)");
    const update = () => setSmallScreen(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const [status, setStatus] = useState<Row | null>(null),
    [page, setPage] = useState(pageFromHash),
    [revision, setRevision] = useState(0),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null),
    [mobile, setMobile] = useState(false),
    [detail, setDetail] = useState<string | null>(null),
    [fatal, setFatal] = useState("");
  const refresh = () => setRevision((v) => v + 1);
  const load = () =>
    api("/auth/status")
      .then((s) => {
        setStatus(s);
        if (s.csrf) setCsrf(s.csrf);
      })
      .catch((e) => setFatal(e.message));
  useEffect(() => {
    load();
    window.addEventListener("deletarr-session-expired", load);
    return () => window.removeEventListener("deletarr-session-expired", load);
  }, []);
  useEffect(() => {
    notify = (text, error = false) => {
      setToast({ text, error });
      window.setTimeout(() => setToast(null), 6000);
    };
  }, []);
  const settings = useData<Row>(
    status?.authenticated ? "/settings" : "/auth/status",
    revision,
  );
  useEffect(() => {
    if (settings.data?.theme) {
      document.documentElement.dataset.theme = settings.data.theme;
      document.documentElement.style.setProperty(
        "--accent",
        animeThemes[settings.data.theme]?.accent || settings.data.accent,
      );
      setTimezone(settings.data.timezone);
    }
  }, [settings.data]);
  useEffect(() => {
    const listener = () => setPage(pageFromHash());
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobile(false);
        document.querySelector<HTMLButtonElement>(".mobile-menu")?.focus();
      }
    };
    if (mobile) window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [mobile]);
  const navigate = (next: string) => {
    location.hash = next;
    setPage(next);
    setMobile(false);
  };
  if (fatal)
    return (
      <main className="auth-main">
        <ErrorBox text={fatal} />
        <Button onClick={load}>Reconnect</Button>
      </main>
    );
  if (!status) return <Loading />;
  if (!status.authenticated)
    return (
      <Auth
        status={status}
        onLogin={() => {
          load();
          refresh();
        }}
      />
    );
  const props = { revision, refresh, navigate, open: setDetail };
  return (
    <div className="app">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          const content = document.getElementById("main-content");
          content?.focus();
          content?.scrollIntoView({ block: "start" });
        }}
      >
        Skip to content
      </a>
      {mobile && smallScreen && (
        <button
          className="menu-backdrop"
          aria-label="Close menu"
          onClick={() => setMobile(false)}
        />
      )}
      <aside
        className={mobile ? "sidebar open" : "sidebar"}
        inert={smallScreen && !mobile}
      >
        <Logo />
        {animeThemes[settings.data?.theme] && (
          <span className="active-theme-name">
            {animeThemes[settings.data?.theme].name}
          </span>
        )}
        <nav aria-label="Main navigation">
          {nav.map(([name, Icon]) => (
            <button
              key={name}
              className={page === name ? "active" : ""}
              aria-current={page === name ? "page" : undefined}
              onClick={() => navigate(name)}
            >
              <Icon size={19} />
              <span>{name}</span>
              {name === "Cleanup Queue" && (
                <ChevronRight className="nav-arrow" size={14} />
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button
            onClick={async () => {
              await api("/auth/logout", "POST", {});
              load();
            }}
            className="account"
          >
            <span className="avatar">
              {status.username?.slice(0, 1).toUpperCase()}
            </span>
            <span>
              {status.username}
              <small>Administrator</small>
            </span>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-menu"
              aria-label="Open menu"
              aria-expanded={mobile}
              onClick={() => setMobile(!mobile)}
            >
              <Menu />
            </button>
            <span className="breadcrumb">
              <b>{page}</b>
            </span>
          </div>
          <div className="top-actions">
            <span
              className={
                "mode " + (settings.data?.dry_run === false ? "live" : "")
              }
            >
              <span className="status-dot" />
              {settings.data?.dry_run === false
                ? "Live deletion enabled"
                : "Dry-run mode"}
            </span>
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {page === "Dashboard" ? (
            <Dashboard {...props} />
          ) : page === "Libraries" ? (
            <Libraries {...props} />
          ) : page === "Rules" ? (
            <Rules {...props} />
          ) : page === "Cleanup Queue" ? (
            <Queue {...props} />
          ) : page === "History" ? (
            <HistoryPage {...props} />
          ) : page === "Statistics" ? (
            <Statistics {...props} />
          ) : page === "Notifications" ? (
            <NotificationsPage {...props} />
          ) : (
            <SettingsPage {...props} />
          )}
        </main>
        <footer>
          <a href="/api/source">Source code</a>
          <a href="/api/license">AGPL-3.0</a>
        </footer>
      </div>
      {toast && (
        <div
          className={"toast " + (toast.error ? "toast-error" : "")}
          role="status"
        >
          {toast.error ? <XCircle size={18} /> : <Check size={18} />}
          <span>{toast.text}</span>
          <button
            aria-label="Dismiss notification"
            className="icon-button"
            onClick={() => setToast(null)}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {detail && (
        <MediaDetail
          id={detail}
          close={() => setDetail(null)}
          refresh={refresh}
        />
      )}
    </div>
  );
}
type Props = {
  revision: number;
  refresh: () => void;
  navigate: (x: string) => void;
  open: (x: string) => void;
};
function PageHeading({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{children}</div>
    </div>
  );
}
async function runJob(kind: string) {
  const r = await act("/jobs", "POST", { kind });
  notify("Job queued. Follow progress in History.");
  return r;
}
function Dashboard({ revision, refresh, navigate, open }: Props) {
  const { data: d, error } = useData<Row>("/dashboard", revision);
  const streams = useData<Row[] | Row>(
    d?.integrations.some((i: Row) => i.kind === "plex")
      ? "/streams"
      : "/health",
    revision,
  );
  if (error) return <ErrorBox text={error} />;
  if (!d) return <Loading />;
  const live = d.settings.dry_run === false;
  return (
    <>
      <PageHeading title="Overview" description="">
        <Button onClick={() => runJob("full_sync").then(refresh)}>
          <RefreshCw size={16} /> Sync libraries
        </Button>
        <Button
          kind={d.candidates ? "" : "primary"}
          onClick={() => runJob("evaluate").then(refresh)}
        >
          <Play size={15} /> Evaluate rules
        </Button>
      </PageHeading>
      {!d.settings.onboarding_complete && (
        <div className="onboard-banner">
          <ShieldCheck size={24} />
          <div>
            <h3>Let’s connect your library.</h3>
            <p>
              Connect Plex, authorize storage, then preview your first rule.
            </p>
          </div>
          <Button onClick={() => navigate("Settings")}>
            Finish setup <ArrowRight size={16} />
          </Button>
        </div>
      )}
      <dl className="library-summary" aria-label="Library summary">
        <div>
          <dt>
            <Film size={16} /> Movies
          </dt>
          <dd>{d.counts.movie || 0}</dd>
        </div>
        <div>
          <dt>
            <Tv size={16} /> Shows
          </dt>
          <dd>
            {d.counts.show || 0}
            <small>{d.counts.episode || 0} episodes</small>
          </dd>
        </div>
        <div>
          <dt>
            <HardDrive size={16} /> Indexed storage
          </dt>
          <dd>
            {bytes(d.logical_bytes)}
            <small>Logical file size</small>
          </dd>
        </div>
        <div>
          <dt>
            <ShieldCheck size={16} /> Keep Forever
          </dt>
          <dd>
            {d.protected}
            <small>Protected titles</small>
          </dd>
        </div>
      </dl>
      <div className="overview-grid">
        <section className="review-focus">
          <div className="review-focus-head">
            <h2>Your next cleanup</h2>
          </div>
          <h3>
            {d.candidates
              ? `${d.candidates} ${d.candidates === 1 ? "candidate" : "candidates"} to review.`
              : "No cleanup candidates"}
          </h3>
          <p>
            {d.candidates
              ? "Review what matched your rules and decide what stays."
              : "Evaluate your rules to find eligible titles."}
          </p>
          {d.candidates > 0 && (
            <div className="review-estimate">
              <HardDrive size={18} />
              <span>
                <b>{bytes(d.candidate_logical_bytes)}</b> candidate file size
                <small>
                  Logical estimate · actual reclaimed space may differ
                </small>
              </span>
            </div>
          )}
          <Button
            kind={d.candidates ? "primary" : ""}
            onClick={() => navigate("Cleanup Queue")}
          >
            Review cleanup queue <ArrowRight size={16} />
          </Button>
          {live && (
            <p className="danger-text review-live">
              Live deletion is enabled. Removed files cannot be recovered
              through Deletarr.
            </p>
          )}
        </section>
        <section className="panel storage-panel">
          <div className="section-heading">
            <h2>Storage overview</h2>
            <Button kind="quiet" onClick={() => navigate("Settings")}>
              Manage <ArrowRight size={14} />
            </Button>
          </div>
          {d.volumes.length ? (
            d.volumes.map((v: Row) => (
              <div className="volume" key={v.id}>
                <div className="volume-head">
                  <span>
                    <HardDrive size={18} />
                    {v.path}
                  </span>
                  <Badge tone={v.healthy ? "green" : "red"}>
                    {v.healthy ? "Mounted" : "Unavailable"}
                  </Badge>
                </div>
                {v.healthy ? (
                  <>
                    <div className="capacity">
                      <b>
                        {bytes(v.available)}
                        <small> available</small>
                      </b>
                      <span>{bytes(v.total)} total</span>
                    </div>
                    <div
                      className="storage-bar"
                      role="meter"
                      aria-label={`${v.path} storage used`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(
                        ((v.total - v.free) / v.total) * 100,
                      )}
                    >
                      <div
                        style={{
                          width: `${((v.total - v.free) / v.total) * 100}%`,
                        }}
                      />
                    </div>
                    <div className="volume-legend">
                      <span>{bytes(v.total - v.free)} used</span>
                      <span>
                        {((v.available / v.total) * 100).toFixed(1)}% available
                      </span>
                    </div>
                  </>
                ) : (
                  <ErrorBox text={v.error} />
                )}
              </div>
            ))
          ) : (
            <Empty icon={HardDrive} title="Connect your storage">
              Authorize a mounted media root in Settings to see its capacity and
              verify access.
            </Empty>
          )}
        </section>
      </div>
      <details className="dashboard-disclosure">
        <summary>
          Connections{" "}
          <small>
            {d.integrations.filter((i: Row) => i.health === "connected").length}{" "}
            of 4 connected
          </small>
        </summary>
        <section className="connection-strip" aria-label="Integration status">
          {["plex", "radarr", "sonarr", "seerr"].map((name, i) => {
            const service = d.integrations.find((x: Row) => x.kind === name);
            const connected = service?.health === "connected";
            return (
              <button
                className="connection"
                key={name}
                onClick={() => navigate("Settings")}
              >
                <span className={"service-icon service-" + name}>
                  {
                    [
                      <Play key="plex" />,
                      <Film key="radarr" />,
                      <Tv key="sonarr" />,
                      <Search key="seerr" />,
                    ][i]
                  }
                </span>
                <span>
                  <strong>{name[0].toUpperCase() + name.slice(1)}</strong>
                  <small>
                    {connected
                      ? "Connected"
                      : service
                        ? "Not verified"
                        : "Not configured"}
                  </small>
                </span>
                <span
                  className={
                    "connection-indicator" + (connected ? " connected" : "")
                  }
                  aria-hidden="true"
                />
              </button>
            );
          })}
        </section>
      </details>
      <details className="dashboard-disclosure">
        <summary>
          Library activity <small>Recent watches & schedules</small>
        </summary>
        <div className="dashboard-columns">
          <section className="panel recently-watched">
            <div className="section-heading">
              <h2>Recently watched</h2>
              <Button kind="quiet" onClick={() => navigate("Libraries")}>
                View library <ArrowRight size={14} />
              </Button>
            </div>
            {d.recently_watched.length ? (
              <div className="watch-shelf">
                {d.recently_watched.map((m: Row) => (
                  <button
                    className="watch-title"
                    key={m.id}
                    onClick={() => open(m.id)}
                  >
                    <CandidateArtwork
                      poster={`/api/media/${m.id}/poster`}
                      title={m.title}
                    />
                    <strong>{m.title}</strong>
                    <small>{date(m.last_watched)}</small>
                  </button>
                ))}
              </div>
            ) : (
              <Empty icon={Film} title="Your next watch belongs here">
                Completed watch activity appears after you connect and
                synchronize Plex.
              </Empty>
            )}
          </section>
          <section className="panel schedule-panel">
            <div className="section-heading">
              <h2>Coming up</h2>
              <CalendarClock size={18} />
            </div>
            {d.upcoming.length ? (
              <div className="list">
                {d.upcoming.map((item: Row) => (
                  <div className="list-row" key={item.id}>
                    <Clock3 size={17} />
                    <b>{item.name}</b>
                    <small>{date(item.next_at)}</small>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={CalendarClock} title="You set the pace">
                Schedule synchronization, evaluation, and cleanup in Settings.
              </Empty>
            )}
            <div className="footnote">
              <Play size={14} />
              Active Plex streams:{" "}
              {Array.isArray(streams.data)
                ? streams.data.length
                : "Unavailable"}
              {streams.error ? " · could not verify connection" : ""}
            </div>
            <Button kind="wide quiet" onClick={() => navigate("History")}>
              View recent activity <ArrowRight size={15} />
            </Button>
          </section>
        </div>
      </details>
    </>
  );
}
function Metric({
  title,
  value,
  subtitle,
  icon: Icon,
}: {
  title: string;
  value: ReactNode;
  subtitle: string;
  icon: any;
}) {
  return (
    <div className="metric">
      <div>
        {title}
        <Icon size={18} />
      </div>
      <strong>{value}</strong>
      <small>{subtitle}</small>
    </div>
  );
}
function Libraries({ revision, refresh, open }: Props) {
  const [q, setQ] = useState(""),
    [kind, setKind] = useState("movie"),
    [library, setLibrary] = useState(""),
    [view, setView] = useState("grid"),
    [page, setPage] = useState(1),
    [sort, setSort] = useState("title"),
    [selected, setSelected] = useState<Set<string>>(new Set());
  const { data: d, error } = useData<Row>(
    `/media?q=${encodeURIComponent(q)}&kind=${kind}&library=${library}&page=${page}&sort=${sort}`,
    revision,
  );
  const libs = useData<Row[]>("/libraries", revision);
  async function protect() {
    await Promise.all(
      [...selected].map((id) =>
        api("/media/" + id + "/protection", "PUT", { protected: true }),
      ),
    );
    notify("Selected media protected");
    setSelected(new Set());
    refresh();
  }
  return (
    <>
      <PageHeading
        title="Your library"
        description="Movies, shows, and the details that matter."
      >
        <Button onClick={() => runJob("full_sync")}>
          <RefreshCw size={16} />
          Sync library
        </Button>
      </PageHeading>
      <div className="toolbar">
        <label className="search">
          <Search size={18} />
          <input
            aria-label="Search media"
            placeholder="Search your library…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <select
          aria-label="Media type"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All media</option>
          <option value="movie">Movies</option>
          <option value="show">Shows</option>
          <option value="season">Seasons</option>
          <option value="episode">Episodes</option>
        </select>
        <select
          aria-label="Library"
          value={library}
          onChange={(e) => {
            setLibrary(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All libraries</option>
          {libs.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.title}
            </option>
          ))}
        </select>
        <select
          aria-label="Sort by"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
        >
          <option value="title">Title</option>
          <option value="added_at">Date added</option>
          <option value="last_watched">Last watched</option>
          <option value="year">Year</option>
        </select>
        <div className="segmented">
          <button
            className={view === "grid" ? "selected" : ""}
            onClick={() => setView("grid")}
          >
            Posters
          </button>
          <button
            className={view === "table" ? "selected" : ""}
            onClick={() => setView("table")}
          >
            Table
          </button>
        </div>
      </div>
      {selected.size > 0 && (
        <div className="selection-bar">
          <b>{selected.size} selected</b>
          <Button onClick={protect}>
            <ShieldCheck size={15} />
            Keep Forever
          </Button>
          <Button kind="quiet" onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </div>
      )}
      <ErrorBox text={error} />
      {!d ? (
        <Loading />
      ) : d.items.length === 0 ? (
        <section className="panel">
          <Empty
            icon={Film}
            title={q ? "No matching titles" : "Your library is not synced yet"}
          >
            {q
              ? "Try another title or change your filters."
              : "Connect Plex and run your first sync. Your real movies, shows and episodes will appear here."}
          </Empty>
        </section>
      ) : view === "grid" ? (
        <div className="poster-grid">
          {d.items.map((m: Row) => (
            <article className="poster-card" key={m.id}>
              <div className="poster-image">
                <input
                  type="checkbox"
                  aria-label={"Select " + m.title}
                  checked={selected.has(m.id)}
                  onChange={(e) =>
                    setSelected((prev) => {
                      const next = new Set(prev);
                      e.target.checked ? next.add(m.id) : next.delete(m.id);
                      return next;
                    })
                  }
                />
                <button
                  onClick={() => open(m.id)}
                  aria-label={"Open " + m.title}
                >
                  {m.poster ? (
                    <img src={m.poster} alt="" loading="lazy" />
                  ) : (
                    <Film size={38} strokeWidth={1} />
                  )}
                </button>
                <div className="poster-badges">
                  {m.protected ? (
                    <Badge tone="green">
                      <ShieldCheck size={12} />
                      Keep
                    </Badge>
                  ) : null}
                  {m.watch_count > 0 ? (
                    <Badge>
                      <Check size={12} />
                      Watched
                    </Badge>
                  ) : null}
                </div>
              </div>
              <button className="title-button" onClick={() => open(m.id)}>
                {m.title}
              </button>
              <div className="poster-meta">
                <span>{m.year || m.kind}</span>
                <span>{bytes(m.size)}</span>
              </div>
              {m.cleanup_state && (
                <small className="eligibility">{m.cleanup_state}</small>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="table-wrap panel">
          <table>
            <thead>
              <tr>
                <th>Select</th>
                <th>Title</th>
                <th>Library</th>
                <th>Size</th>
                <th>Added</th>
                <th>Last watched</th>
                <th>Protection</th>
                <th>Cleanup</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((m: Row) => (
                <tr key={m.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={"Select " + m.title}
                      checked={selected.has(m.id)}
                      onChange={(e) =>
                        setSelected((prev) => {
                          const n = new Set(prev);
                          e.target.checked ? n.add(m.id) : n.delete(m.id);
                          return n;
                        })
                      }
                    />
                  </td>
                  <td>
                    <button className="text-button" onClick={() => open(m.id)}>
                      {m.title}
                    </button>
                    <small>
                      {m.kind} · {m.year || "—"}
                    </small>
                  </td>
                  <td>{m.library}</td>
                  <td>{bytes(m.size)}</td>
                  <td>{date(m.added_at)}</td>
                  <td>{date(m.last_watched)}</td>
                  <td>{m.protected ? "Keep Forever" : "Standard"}</td>
                  <td>
                    <Badge>{m.cleanup_state || "Not evaluated"}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d && (
        <div className="pagination">
          <span>{d.total} titles</span>
          <Button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            <ChevronLeft size={14} />
            Previous
          </Button>
          <span>Page {page}</span>
          <Button
            disabled={page * 48 >= d.total}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </>
  );
}
function MediaDetail({
  id,
  close,
  refresh,
}: {
  id: string;
  close: () => void;
  refresh: () => void;
}) {
  const [revision, setRevision] = useState(0);
  const { data: d, error } = useData<Row>("/media/" + id, revision);
  return (
    <Modal title={d?.title || "Media details"} close={close}>
      <ErrorBox text={error} />
      {!d ? (
        <Loading />
      ) : (
        <>
          <div className="detail-summary">
            <Badge>{d.kind}</Badge>
            <Badge tone={d.protected ? "green" : "neutral"}>
              {d.protected ? "Keep Forever" : "Standard retention"}
            </Badge>
            <p>
              {parse(d.metadata).summary || "No description supplied by Plex."}
            </p>
            <Button
              onClick={async () => {
                await act("/media/" + id + "/protection", "PUT", {
                  protected: !d.protected,
                });
                setRevision((v) => v + 1);
                refresh();
              }}
            >
              <ShieldCheck size={16} />
              {d.protected ? "Remove Keep Forever" : "Keep Forever"}
            </Button>
          </div>
          <dl className="detail-data">
            <dt>Added to Plex</dt>
            <dd>{date(d.added_at)}</dd>
            <dt>Last completed watch</dt>
            <dd>{date(d.last_watched)}</dd>
            <dt>Watch history</dt>
            <dd>
              {d.history_complete
                ? "Complete"
                : "Historical coverage uncertain"}
            </dd>
            <dt>Observed watches</dt>
            <dd>{d.watch_count}</dd>
            <dt>External identifiers</dt>
            <dd>
              {d.external_ids
                .map((x: Row) => `${x.provider}: ${x.value}`)
                .join(" · ") || "Unavailable"}
            </dd>
          </dl>
          <h3>Physical media files</h3>
          {d.files.map((f: Row) => (
            <div key={f.id} className="file-row">
              <code>{f.path || f.source_path}</code>
              <span>
                {bytes(f.size)} · {f.manager || "Unmanaged"} ·{" "}
                {f.identity
                  ? JSON.parse(f.identity).links + " hardlink(s)"
                  : "Mapping unverified"}
              </span>
            </div>
          ))}
          <h3>Watch history</h3>
          {d.watch_events.length ? (
            d.watch_events.map((w: Row) => (
              <div key={w.id} className="list-row">
                {w.completed ? <CheckCheck size={16} /> : <Play size={16} />}
                <span>User {w.user_id}</span>
                <span>
                  {w.completed
                    ? "Completed watch"
                    : "Playback · completion uncertain"}
                </span>
                <small>{date(w.watched_at)}</small>
              </div>
            ))
          ) : (
            <p className="muted">
              No observed events. This does not prove the item has never been
              watched.
            </p>
          )}
          <h3>Retention decisions</h3>
          {d.evaluations.map((e: Row) => (
            <details key={e.id}>
              <summary>
                {date(e.created_at)} ·{" "}
                {e.eligible ? "Eligible" : "Protected or conditions not met"}
              </summary>
              <pre>{JSON.stringify(parse(e.reasons), null, 2)}</pre>
            </details>
          ))}
          <h3>Related seasons and episodes</h3>
          <p className="muted">
            {d.descendants.length - 1} child items · {d.requests.length}{" "}
            associated request references
          </p>
        </>
      )}
    </Modal>
  );
}

const defaultCondition: Condition = {
  field: "last_watched_days",
  operator: "gte",
  value: 90,
};
const fieldNames: Record<string, string> = {
  kind: "Media type",
  watched: "Watched",
  size_bytes: "File size (bytes)",
  added_days: "Days since added",
  last_watched_days: "Days since last watch",
  first_watched_days: "Days since first watch",
  release_year: "Release year",
  protected: "Protected",
  free_bytes: "Volume available bytes",
  free_percent: "Volume available percent",
};
function ConditionEditor({
  value,
  onChange,
  depth = 0,
}: {
  value: Condition;
  onChange: (c: Condition) => void;
  depth?: number;
}) {
  if (value.group)
    return (
      <div className="condition-group">
        <div className="condition-group-head">
          <select
            aria-label="Logical operator"
            value={value.group}
            onChange={(e) =>
              onChange({
                ...value,
                group: e.target.value as any,
                children:
                  e.target.value === "not"
                    ? [value.children![0]]
                    : value.children,
              })
            }
          >
            <option value="and">All conditions (AND)</option>
            <option value="or">Any condition (OR)</option>
            <option value="not">None of this condition (NOT)</option>
          </select>
          <Button
            kind="quiet"
            onClick={() => onChange({ ...defaultCondition })}
          >
            Use a single condition
          </Button>
        </div>
        {value.children?.map((child, i) => (
          <div className="condition-child" key={i}>
            <ConditionEditor
              value={child}
              depth={depth + 1}
              onChange={(c) =>
                onChange({
                  ...value,
                  children: value.children!.map((v, j) => (j === i ? c : v)),
                })
              }
            />
            {value.children!.length > 1 && (
              <button
                className="icon-button"
                aria-label="Remove condition"
                onClick={() =>
                  onChange({
                    ...value,
                    children: value.children!.filter((_, j) => j !== i),
                  })
                }
              >
                <X size={16} />
              </button>
            )}
          </div>
        ))}
        {value.group !== "not" && (
          <Button
            kind="quiet"
            disabled={depth >= 7}
            onClick={() =>
              onChange({
                ...value,
                children: [...value.children!, { ...defaultCondition }],
              })
            }
          >
            <Plus size={14} />
            Add condition
          </Button>
        )}
      </div>
    );
  const boolean = value.field === "watched" || value.field === "protected";
  return (
    <div className="condition-row">
      <select
        aria-label="Condition attribute"
        value={value.field || "last_watched_days"}
        onChange={(e) =>
          onChange({
            field: e.target.value,
            operator: "eq",
            value:
              e.target.value === "kind"
                ? "movie"
                : e.target.value === "watched" || e.target.value === "protected"
                  ? true
                  : 90,
          })
        }
      >
        {Object.entries(fieldNames).map(([key, name]) => (
          <option value={key} key={key}>
            {name}
          </option>
        ))}
      </select>
      <select
        aria-label="Comparison"
        value={value.operator}
        onChange={(e) => onChange({ ...value, operator: e.target.value })}
      >
        {(boolean || value.field === "kind"
          ? ["eq"]
          : ["eq", "gt", "gte", "lt", "lte"]
        ).map((op) => (
          <option key={op} value={op}>
            {
              {
                eq: "is",
                gt: "greater than",
                gte: "at least",
                lt: "less than",
                lte: "at most",
              }[op]
            }
          </option>
        ))}
      </select>
      {boolean ? (
        <select
          aria-label="Condition value"
          value={String(value.value)}
          onChange={(e) =>
            onChange({ ...value, value: e.target.value === "true" })
          }
        >
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      ) : value.field === "kind" ? (
        <select
          aria-label="Condition value"
          value={String(value.value)}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        >
          {["movie", "show", "season", "episode"].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      ) : (
        <input
          aria-label="Condition value"
          type="number"
          min={0}
          value={Number(value.value)}
          onChange={(e) =>
            onChange({ ...value, value: Number(e.target.value) })
          }
        />
      )}
      <Button
        kind="quiet"
        disabled={depth >= 7}
        onClick={() =>
          onChange({ group: "and", children: [value, { ...defaultCondition }] })
        }
      >
        Group
      </Button>
    </div>
  );
}
function Rules({ revision, refresh }: Props) {
  const { data: rules, error } = useData<Row[]>("/rules", revision);
  const libs = useData<Row[]>("/libraries", revision);
  const [editing, setEditing] = useState<Row | null>(null),
    [preview, setPreview] = useState<Row | null>(null),
    [busy, setBusy] = useState(false);
  const fresh = () => ({
    name: "Watched movies after 90 days",
    description: "Remove movies after their last completed watch.",
    enabled: true,
    priority: 100,
    library_id: null,
    kind: "movie",
    action: "approval",
    grace_days: 7,
    min_age_days: 7,
    strategy: "unmonitor",
    version: 1,
    conditions: { ...defaultCondition },
  });
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(true);
    try {
      const { id, created_at, updated_at, ...body } = editing;
      await act("/rules" + (id ? "/" + id : ""), id ? "PUT" : "POST", body);
      setEditing(null);
      refresh();
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeading
        title="Retention rules"
        description="Choose what becomes eligible. Protections always come first."
      >
        <Button onClick={() => runJob("evaluate")}>
          <Play size={15} />
          Evaluate all
        </Button>
        <Button kind="primary" onClick={() => setEditing(fresh())}>
          <Plus size={16} />
          Create rule
        </Button>
      </PageHeading>
      <div className="safety-note">
        <ShieldCheck size={19} />
        <span>
          Rules never override protected media, active playback, uncertain
          required history, or unsafe paths.
        </span>
      </div>
      <ErrorBox text={error} />
      {!rules ? (
        <Loading />
      ) : rules.length === 0 ? (
        <section className="panel">
          <Empty
            icon={SlidersHorizontal}
            title="Create your first retention rule"
          >
            Start with watched movies after 90 days, then simulate the result
            before making any decisions.
          </Empty>
        </section>
      ) : (
        <div className="rule-list">
          {rules.map((r) => (
            <article className="rule-card" key={r.id}>
              <div className="rule-priority">
                {r.priority}
                <small>priority</small>
              </div>
              <div className="rule-main">
                <div className="rule-title">
                  <h2>{r.name}</h2>
                  <Badge tone={r.enabled ? "green" : "neutral"}>
                    {r.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                  <Badge>
                    {r.action === "approval"
                      ? "Manual approval"
                      : r.action === "grace"
                        ? `${r.grace_days} day grace`
                        : "Automatic"}
                  </Badge>
                </div>
                <p>{r.description || "Custom retention rule"}</p>
                <div className="rule-meta">
                  <span>{r.kind || "All media"}</span>
                  <span>
                    {r.library_id
                      ? libs.data?.find((l) => l.id === r.library_id)?.title
                      : "All managed libraries"}
                  </span>
                  <span>Version {r.version}</span>
                </div>
              </div>
              <div className="actions">
                <Button
                  onClick={async () => {
                    setPreview(
                      await api("/rules/" + r.id + "/simulate", "POST", {}),
                    );
                  }}
                >
                  <Play size={14} />
                  Simulate
                </Button>
                <Button
                  kind="quiet"
                  onClick={() =>
                    setEditing({
                      ...r,
                      enabled: !!r.enabled,
                      conditions: parse(r.conditions),
                    })
                  }
                >
                  Edit
                </Button>
                <Button
                  kind="quiet"
                  onClick={() => {
                    const { id, created_at, updated_at, ...copy } = r;
                    setEditing({
                      ...copy,
                      name: r.name + " (copy)",
                      version: 1,
                      enabled: !!r.enabled,
                      conditions: parse(r.conditions),
                    });
                  }}
                >
                  Duplicate
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
      {editing && (
        <Modal
          title={editing.id ? "Edit retention rule" : "Create retention rule"}
          close={() => setEditing(null)}
        >
          <form onSubmit={save}>
            <div className="form-grid">
              <Field label="Rule name">
                <input
                  required
                  value={editing.name}
                  onChange={(e) =>
                    setEditing({ ...editing, name: e.target.value })
                  }
                />
              </Field>
              <Field label="Description">
                <input
                  value={editing.description}
                  onChange={(e) =>
                    setEditing({ ...editing, description: e.target.value })
                  }
                />
              </Field>
              <Field label="Media scope">
                <select
                  value={editing.kind || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, kind: e.target.value || null })
                  }
                >
                  <option value="">All media types</option>
                  {["movie", "show", "season", "episode"].map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </Field>
              <Field label="Library">
                <select
                  value={editing.library_id || ""}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      library_id: e.target.value || null,
                    })
                  }
                >
                  <option value="">Global default</option>
                  {libs.data?.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.title}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Priority" help="Lower numbers run first.">
                <input
                  type="number"
                  min={0}
                  max={10000}
                  value={editing.priority}
                  onChange={(e) =>
                    setEditing({ ...editing, priority: +e.target.value })
                  }
                />
              </Field>
              <Field label="Action">
                <select
                  value={editing.action}
                  onChange={(e) =>
                    setEditing({ ...editing, action: e.target.value })
                  }
                >
                  <option value="approval">
                    Require administrator approval
                  </option>
                  <option value="grace">Grace period then automatic</option>
                  <option value="automatic">Automatic deletion</option>
                </select>
              </Field>
              <Field label="Grace period (days)">
                <input
                  type="number"
                  min={0}
                  value={editing.grace_days}
                  onChange={(e) =>
                    setEditing({ ...editing, grace_days: +e.target.value })
                  }
                />
              </Field>
              <Field label="Minimum library age (days)">
                <input
                  type="number"
                  min={1}
                  value={editing.min_age_days}
                  onChange={(e) =>
                    setEditing({ ...editing, min_age_days: +e.target.value })
                  }
                />
              </Field>
              <Field label="Manager strategy">
                <select
                  value={editing.strategy}
                  onChange={(e) =>
                    setEditing({ ...editing, strategy: e.target.value })
                  }
                >
                  <option value="unmonitor">
                    Unmonitor, then remove exact files
                  </option>
                  <option value="remove">
                    Remove whole movie/show record after files
                  </option>
                </select>
              </Field>
              <Field label="Enabled">
                <input
                  type="checkbox"
                  checked={editing.enabled}
                  onChange={(e) =>
                    setEditing({ ...editing, enabled: e.target.checked })
                  }
                />
              </Field>
            </div>
            <h3>Conditions</h3>
            <div className="preset-row">
              <Button
                onClick={() =>
                  setEditing({
                    ...editing,
                    conditions: {
                      field: "last_watched_days",
                      operator: "gte",
                      value: 90,
                    },
                  })
                }
              >
                Watched after 90 days
              </Button>
              <Button
                onClick={() =>
                  setEditing({
                    ...editing,
                    kind: "episode",
                    conditions: {
                      group: "and",
                      children: [
                        { field: "watched", operator: "eq", value: false },
                        { field: "added_days", operator: "gte", value: 180 },
                      ],
                    },
                  })
                }
              >
                Unwatched after 180 days
              </Button>
              <Button
                onClick={() =>
                  setEditing({
                    ...editing,
                    kind: "movie",
                    conditions: {
                      group: "and",
                      children: [
                        {
                          field: "size_bytes",
                          operator: "gt",
                          value: 40 * 1024 ** 3,
                        },
                        {
                          field: "last_watched_days",
                          operator: "gte",
                          value: 90,
                        },
                      ],
                    },
                  })
                }
              >
                Large watched movies
              </Button>
            </div>
            <ConditionEditor
              value={editing.conditions}
              onChange={(c) => setEditing({ ...editing, conditions: c })}
            />
            <p className="muted">
              Dates measure completed Plex watches or date added. Unknown data
              never qualifies a deletion.
            </p>
            <div className="modal-actions">
              <Button onClick={() => setEditing(null)}>Cancel</Button>
              <Button kind="primary" type="submit" disabled={busy}>
                Save rule
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {preview && (
        <Modal title="Rule simulation" close={() => setPreview(null)}>
          <div className="simulation-banner">
            <ShieldCheck size={20} />
            Preview only. No files are modified.
          </div>
          <div className="preview-stats">
            <b>{preview.matches} matches</b>
            <b>{preview.eligible} eligible</b>
            <b>{bytes(preview.estimated_reclaimable)} estimated reclaimable</b>
          </div>
          {preview.items.map((r: Row) => (
            <details key={r.media_id}>
              <summary>
                {r.title}{" "}
                <Badge tone={r.eligible ? "green" : "neutral"}>
                  {r.eligible ? "Eligible" : "Protected"}
                </Badge>
              </summary>
              <p>{r.protections.join(" · ")}</p>
              <pre>{JSON.stringify(r.conditions, null, 2)}</pre>
            </details>
          ))}
        </Modal>
      )}
    </>
  );
}
function CandidateArtwork({
  poster,
  title,
}: {
  poster?: string;
  title: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [poster]);
  return (
    <div className="candidate-art">
      {poster && !failed ? (
        <img
          src={poster}
          alt={`${title} poster`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <Film size={27} aria-label="Poster unavailable" />
      )}
    </div>
  );
}
function Queue({ revision, refresh, open }: Props) {
  const [state, setState] = useState(""),
    [q, setQ] = useState(""),
    [page, setPage] = useState(1),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [plan, setPlan] = useState<Row | null>(null),
    [confirmation, setConfirmation] = useState("");
  const { data: d, error } = useData<Row>(
    `/candidates?state=${state}&q=${encodeURIComponent(q)}&page=${page}`,
    revision,
  );
  const settings = useData<Row>("/settings", revision);
  async function action(c: Row, name: string) {
    await act("/candidates/" + c.id + "/action", "POST", {
      version: c.version,
      action: name,
      days: 7,
    });
    refresh();
  }
  async function bulk(name: string) {
    for (const c of d?.items.filter((x: Row) => selected.has(x.id)) || [])
      await action(c, name);
    setSelected(new Set());
    refresh();
  }
  return (
    <>
      <PageHeading
        title="Cleanup queue"
        description="Review candidates before removing anything."
      >
        <Button onClick={() => runJob("evaluate")}>
          <RefreshCw size={16} />
          Refresh candidates
        </Button>
        <Button kind="primary" onClick={() => runJob("cleanup")}>
          <Play size={15} />
          {settings.data?.dry_run
            ? "Simulate ready items"
            : "Run authorized cleanup"}
        </Button>
      </PageHeading>
      <div className="simulation-banner">
        <ShieldCheck size={20} />
        <div>
          <b>
            {settings.data?.dry_run
              ? "Dry-run: no files will be deleted."
              : "Live deletion is enabled."}
          </b>
          <span>
            {settings.data?.dry_run
              ? ""
              : " Files removed by Deletarr cannot be recovered through this application."}
          </span>
        </div>
      </div>
      <div className="toolbar">
        <label className="search">
          <Search size={17} />
          <input
            aria-label="Search cleanup queue"
            placeholder="Find a candidate…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <select
          aria-label="Candidate state"
          value={state}
          onChange={(e) => {
            setState(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All states</option>
          {[
            "PendingApproval",
            "GracePeriod",
            "Ready",
            "Protected",
            "Completed",
            "Rejected",
            "ReconciliationRequired",
          ].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        {selected.size > 0 && (
          <>
            <span>{selected.size} selected</span>
            <Button onClick={() => bulk("approve")}>Approve selected</Button>
            <Button onClick={() => bulk("reject")}>Reject selected</Button>
            <Button onClick={() => bulk("keep")}>Keep Forever</Button>
          </>
        )}
      </div>
      <ErrorBox text={error} />
      {!d ? (
        <Loading />
      ) : !d.items.length ? (
        <section className="panel">
          <Empty icon={ListFilter} title="No cleanup candidates">
            Candidates appear when your retention rules match. Evaluate your
            rules after syncing the library.
          </Empty>
        </section>
      ) : (
        <div className="candidate-list">
          {d.items.map((c: Row) => (
            <article className="candidate" key={c.id}>
              <input
                type="checkbox"
                aria-label={"Select " + c.title}
                checked={selected.has(c.id)}
                onChange={(e) =>
                  setSelected((prev) => {
                    const n = new Set(prev);
                    e.target.checked ? n.add(c.id) : n.delete(c.id);
                    return n;
                  })
                }
              />
              <CandidateArtwork poster={c.poster} title={c.title} />
              <div className="candidate-main">
                <button
                  className="title-button"
                  onClick={() => open(c.media_id)}
                >
                  {c.title}
                </button>
                <p>
                  {c.rule_name} · {c.library}
                </p>
                <p className="candidate-watch">
                  Last watched:{" "}
                  {c.last_watched ? date(c.last_watched) : "Unknown"}
                  {!c.history_visible && " · History incomplete"}
                </p>
                <div className="candidate-meta">
                  <Badge tone={c.state === "Ready" ? "green" : "neutral"}>
                    {c.state.replace(/([a-z])([A-Z])/g, "$1 $2")}
                  </Badge>
                  <span>{c.files.length} exact files</span>
                  <span>{bytes(c.logical_bytes)} logical</span>
                  {c.due_at && <span>Grace expires {date(c.due_at)}</span>}
                </div>
                <details>
                  <summary>Eligibility & exact paths</summary>
                  <p>
                    {parse(c.reason).protections?.join(" · ") ||
                      "Retention conditions matched. Final protection checks still required."}
                  </p>
                  {c.files.map((f: Row) => (
                    <div className="file-row" key={f.id}>
                      <code>{f.path || f.source_path}</code>
                      <span>
                        {bytes(f.size)} logical ·{" "}
                        {f.identity
                          ? bytes(parse(f.identity).reclaimable) +
                            " estimated reclaimable"
                          : "Identity unavailable"}
                      </span>
                    </div>
                  ))}
                  <pre>
                    {JSON.stringify(parse(c.reason).conditions, null, 2)}
                  </pre>
                </details>
              </div>
              <div className="candidate-actions">
                <Button
                  onClick={async () => {
                    try {
                      setPlan(
                        await api("/candidates/" + c.id + "/plan", "POST", {}),
                      );
                      setConfirmation("");
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}
                >
                  Inspect plan
                  <ChevronRight size={14} />
                </Button>
                {[
                  "PendingApproval",
                  "GracePeriod",
                  "Ready",
                  "Postponed",
                ].includes(c.state) && (
                  <>
                    <Button kind="primary" onClick={() => action(c, "approve")}>
                      <Check size={14} />
                      Approve
                    </Button>
                    <details className="candidate-more">
                      <summary>
                        More actions <ChevronRight size={13} />
                      </summary>
                      <div>
                        <Button
                          kind="quiet"
                          onClick={() => action(c, "postpone")}
                        >
                          <Clock3 size={14} />
                          Postpone 7 days
                        </Button>
                        <Button kind="quiet" onClick={() => action(c, "keep")}>
                          <ShieldCheck size={14} />
                          Keep Forever
                        </Button>
                        <Button
                          kind="quiet danger-text"
                          onClick={() => action(c, "reject")}
                        >
                          Reject
                        </Button>
                      </div>
                    </details>
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
      {d && (
        <div className="pagination">
          <span>{d.total} candidates</span>
          <Button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button
            disabled={page * 50 >= d.total}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
      {plan && (
        <Modal title="Exact deletion plan" close={() => setPlan(null)}>
          <p>
            <b>{plan.title}</b> · {plan.files.length} physical files ·{" "}
            {bytes(plan.logical_bytes)} logical ·{" "}
            {bytes(plan.estimated_reclaimable)} estimated reclaimable
          </p>
          <p className="muted">
            Valid for 15 minutes. Rules, playback, request protection, file
            identities and integrations are checked again immediately before
            execution.
          </p>
          {plan.files.map((f: Row) => (
            <div className="file-row" key={f.id}>
              <code>{f.path}</code>
              <span>
                {bytes(f.size)} · {f.manager || "Direct filesystem"} ·{" "}
                {plan.strategy}
              </span>
            </div>
          ))}
          <details>
            <summary>Full plan & expected postconditions</summary>
            <pre>{JSON.stringify(plan, null, 2)}</pre>
          </details>
          {!settings.data?.dry_run && (
            <Field
              label="Permanent deletion confirmation"
              help={`Type DELETE ${plan.affected.filter((n: Row) => ["movie", "episode"].includes(n.kind)).length} ITEMS ${plan.logical_bytes} BYTES`}
            >
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                autoComplete="off"
              />
            </Field>
          )}
          <div className="modal-actions">
            <Button onClick={() => setPlan(null)}>Close</Button>
            <Button
              kind={settings.data?.dry_run ? "primary" : "danger"}
              onClick={async () => {
                await act("/plans/" + plan.id + "/execute", "POST", {
                  digest: plan.digest,
                  confirmation,
                });
                setPlan(null);
                refresh();
                notify("Execution queued. Inspect History for its result.");
              }}
            >
              {settings.data?.dry_run
                ? "Simulate this plan"
                : "Permanently delete"}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function HistoryPage({ revision, refresh }: Props) {
  const [resolution, setResolution] = useState<Row | null>(null);
  const [page, setPage] = useState(1),
    [filter, setFilter] = useState("");
  const { data: d, error } = useData<Row>("/history?page=" + page, revision);
  const jobs = useData<Row[]>("/jobs?page=" + page, revision);
  return (
    <>
      <PageHeading
        title="Activity history"
        description="A durable record of cleanup, synchronization and the reasons behind them."
      >
        <Button onClick={refresh}>
          <RefreshCw size={16} />
          Refresh
        </Button>
        <Button onClick={() => runJob("reconcile")}>
          Reconcile operations
        </Button>
      </PageHeading>
      <ErrorBox text={error} />
      {d?.reconciliation.some((r: Row) => r.state === "pending") && (
        <div className="error">
          Some operations need reconciliation. Ambiguous deletions are never
          repeated automatically.
        </div>
      )}
      {d?.reconciliation
        .filter((r: Row) => r.state === "pending")
        .map((r: Row) => (
          <div className="schedule-row" key={r.id}>
            <div>
              <b>Reconciliation required</b>
              <small>{r.reason}</small>
            </div>
            <Button onClick={() => setResolution(r)}>
              Record external resolution
            </Button>
          </div>
        ))}
      {resolution && (
        <Modal
          title="Record a manual reconciliation"
          close={() => setResolution(null)}
        >
          <p>
            First verify monitoring, import exclusions and availability in the
            external applications. Deletarr verifies file absence but cannot
            independently verify your manual repairs. This records an
            administrator attestation; it never repeats deletion.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const data = Object.fromEntries(new FormData(e.currentTarget));
              await act(
                "/reconciliation/" + resolution.id + "/resolve",
                "POST",
                data,
              );
              setResolution(null);
              refresh();
            }}
          >
            <Field
              label="Resolution evidence"
              help="Describe the external state you checked and repaired."
            >
              <textarea
                name="explanation"
                required
                minLength={20}
                maxLength={2000}
              />
            </Field>
            <Field
              label="Confirmation"
              help="I VERIFIED THE EXTERNAL STATE AND ACCEPT THIS RESOLUTION"
            >
              <input name="confirmation" required autoComplete="off" />
            </Field>
            <div className="modal-actions">
              <Button onClick={() => setResolution(null)}>Cancel</Button>
              <Button type="submit">Record administrator attestation</Button>
            </div>
          </form>
        </Modal>
      )}
      <div className="toolbar">
        <label className="search">
          <Search size={17} />
          <input
            aria-label="Filter history"
            placeholder="Filter this page of history…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>Cleanup operations</h2>
        </div>
        {!d ? (
          <Loading />
        ) : !d.operations.length ? (
          <Empty icon={HistoryIcon} title="A clean slate">
            Simulated and live operations will stay here, even after media
            leaves Plex.
          </Empty>
        ) : (
          d.operations
            .filter((o: Row) =>
              JSON.stringify(o).toLowerCase().includes(filter.toLowerCase()),
            )
            .map((o: Row) => (
              <details className="history-row" key={o.id}>
                <summary>
                  <span>{parse(o.snapshot).title}</span>
                  <Badge tone={o.dry_run ? "green" : "neutral"}>
                    {o.dry_run ? "Dry-run" : "Live"}
                  </Badge>
                  <Badge>{o.state}</Badge>
                  <small>{date(o.created_at)}</small>
                </summary>
                <p>{o.error}</p>
                <pre>
                  {JSON.stringify(
                    { outcome: parse(o.outcome), plan: parse(o.snapshot) },
                    null,
                    2,
                  )}
                </pre>
                <Button
                  onClick={async () => {
                    const r = await api("/operations/" + o.id);
                    notify(
                      r.checkpoints
                        .map((c: Row) => `${c.name}: ${c.state}`)
                        .join(" · ") || "No mutation checkpoints",
                    );
                  }}
                >
                  Inspect checkpoints
                </Button>
              </details>
            ))
        )}
      </section>
      <section className="panel section-space">
        <div className="section-heading">
          <h2>Background jobs</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Started</th>
                <th>Attempts</th>
                <th>Result</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.data?.map((j) => (
                <tr key={j.id}>
                  <td>{j.kind.replaceAll("_", " ")}</td>
                  <td>
                    <Badge
                      tone={
                        j.state === "failed"
                          ? "red"
                          : j.state === "completed"
                            ? "green"
                            : "neutral"
                      }
                    >
                      {j.state}
                    </Badge>
                  </td>
                  <td>{date(j.started_at)}</td>
                  <td>{j.attempts}</td>
                  <td>{j.error || "—"}</td>
                  <td>
                    {["running", "queued"].includes(j.state) && (
                      <Button
                        kind="quiet"
                        onClick={() =>
                          act("/jobs/" + j.id + "/cancel").then(refresh)
                        }
                      >
                        Cancel remaining work
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel section-space">
        <div className="section-heading">
          <h2>Audit trail</h2>
        </div>
        {d?.audit
          .filter((a: Row) =>
            JSON.stringify(a).toLowerCase().includes(filter.toLowerCase()),
          )
          .map((a: Row) => (
            <details key={a.id} className="audit-row">
              <summary>
                <span>{a.event}</span>
                <small>
                  {a.actor} · {date(a.at)}
                </small>
              </summary>
              <pre>{JSON.stringify(parse(a.detail), null, 2)}</pre>
            </details>
          ))}
      </section>
      <div className="pagination">
        <Button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
          Previous
        </Button>
        <span>Page {page}</span>
        <Button
          disabled={!d?.audit.length && !jobs.data?.length}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>
    </>
  );
}
function Statistics({ revision }: Props) {
  const { data: d, error } = useData<Row>("/dashboard", revision);
  const history = useData<Row>("/history", revision);
  if (!d) return error ? <ErrorBox text={error} /> : <Loading />;
  const operations =
    history.data?.operations.filter(
      (o: Row) => !o.dry_run && o.state === "Completed",
    ) || [];
  const logical = operations.reduce(
    (n: number, o: Row) => n + (parse(o.outcome).logical_bytes || 0),
    0,
  );
  return (
    <>
      <PageHeading
        title="The space between stories."
        description="Measured library activity, with estimates clearly separated from observations."
      />
      <div className="metrics">
        <Metric
          title="Indexed media"
          value={bytes(d.logical_bytes)}
          subtitle="Logical size of known physical files"
          icon={Database}
        />
        <Metric
          title="Completed live operations"
          value={operations.length}
          subtitle="Most recent 50 recorded operations"
          icon={CheckCheck}
        />
        <Metric
          title="Logical bytes removed"
          value={bytes(logical)}
          subtitle="Completed live operations on this page"
          icon={Trash2}
        />
        <Metric
          title="Actual reclaimed bytes"
          value="Unavailable"
          subtitle="Free-space deltas cannot prove attribution"
          icon={HardDrive}
        />
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>Completed cleanup activity</h2>
          <Badge>Recorded events</Badge>
        </div>
        {d.trends.length ? (
          <div
            className="trend-chart"
            role="img"
            aria-label="Completed live cleanup operations by day"
          >
            {d.trends
              .slice()
              .reverse()
              .map((t: Row) => (
                <div className="trend-column" key={t.day}>
                  <span>{t.events}</span>
                  <div
                    style={{
                      height: Math.max(
                        8,
                        (t.events /
                          Math.max(...d.trends.map((v: Row) => v.events))) *
                          150,
                      ),
                    }}
                  />
                  <small>{t.day}</small>
                </div>
              ))}
          </div>
        ) : (
          <Empty icon={Activity} title="Measurements need a little history">
            Real cleanup trends will appear after completed live operations. Dry
            runs do not count as reclaimed storage.
          </Empty>
        )}
      </section>
    </>
  );
}
const notificationEvents = [
  "cleanup_completed",
  "cleanup_failed",
  "approval_required",
  "upcoming_deletion",
  "storage_below_threshold",
  "integration_disconnected",
  "deletion_blocked",
  "reconciliation_required",
  "job_failed",
];
function NotificationsPage({ revision, refresh }: Props) {
  const { data: items, error } = useData<Row[]>("/notifications", revision);
  const destinations = useData<Row[]>("/destinations", revision);
  const [editing, setEditing] = useState(false);
  return (
    <>
      <PageHeading
        title="Stay in the loop."
        description="Useful updates about your library, delivered where you want them."
      >
        <Button kind="primary" onClick={() => setEditing(true)}>
          <Plus size={16} />
          Add destination
        </Button>
      </PageHeading>
      <ErrorBox text={error} />
      <div className="dashboard-columns">
        <section className="panel">
          <div className="section-heading">
            <h2>In-app notifications</h2>
          </div>
          {!items ? (
            <Loading />
          ) : !items.length ? (
            <Empty icon={Bell} title="All quiet here">
              Cleanup results and important safety notices will appear here.
            </Empty>
          ) : (
            items.map((n) => (
              <div className="notification-row" key={n.id}>
                <Bell size={18} />
                <div>
                  <b>{n.event.replaceAll("_", " ")}</b>
                  <p>{n.message}</p>
                  <small>{date(n.created_at)}</small>
                </div>
                <button
                  className="icon-button"
                  aria-label="Dismiss notification"
                  onClick={() =>
                    act("/notifications/" + n.id + "/dismiss").then(refresh)
                  }
                >
                  <X size={17} />
                </button>
              </div>
            ))
          )}
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Delivery destinations</h2>
          </div>
          {!destinations.data?.length ? (
            <Empty icon={ArrowRight} title="Keep the right people informed">
              Connect a Discord webhook, Telegram bot, or your own webhook
              endpoint.
            </Empty>
          ) : (
            destinations.data.map((d) => (
              <div className="destination" key={d.id}>
                <div>
                  <b>{d.name}</b>
                  <small>
                    {d.kind} · {d.enabled ? "enabled" : "disabled"}
                  </small>
                </div>
                <Button onClick={() => act("/destinations/" + d.id + "/test")}>
                  Test
                </Button>
                <button
                  className="icon-button"
                  aria-label="Delete destination"
                  onClick={() =>
                    act("/destinations/" + d.id, "DELETE", {}).then(refresh)
                  }
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))
          )}
        </section>
      </div>
      {editing && (
        <Modal
          title="Add notification destination"
          close={() => setEditing(false)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              await act("/destinations", "POST", {
                name: f.get("name"),
                kind: f.get("kind"),
                url: f.get("url"),
                token: f.get("token"),
                chat_id: f.get("chat_id"),
                allow_private: f.get("allow_private") === "on",
                events: f.getAll("events"),
                enabled: true,
              });
              setEditing(false);
              refresh();
            }}
          >
            <Field label="Name">
              <input name="name" required />
            </Field>
            <Field label="Type">
              <select name="kind">
                <option value="discord">Discord</option>
                <option value="telegram">Telegram</option>
                <option value="webhook">Generic webhook</option>
              </select>
            </Field>
            <Field label="Webhook URL (Discord or generic)">
              <input name="url" type="url" />
            </Field>
            <Field label="Bot token (Telegram)">
              <input name="token" type="password" />
            </Field>
            <Field label="Chat ID (Telegram)">
              <input name="chat_id" />
            </Field>
            <label className="checkbox">
              <input name="allow_private" type="checkbox" />
              Trust private/LAN destination
            </label>
            <h3>Event subscriptions</h3>
            <div className="form-grid">
              {notificationEvents.map((event) => (
                <label className="checkbox" key={event}>
                  <input
                    name="events"
                    value={event}
                    type="checkbox"
                    defaultChecked
                  />
                  {event.replaceAll("_", " ")}
                </label>
              ))}
            </div>
            <div className="modal-actions">
              <Button type="submit" kind="primary">
                Save destination
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

const settingGroups = [
  { name: "Workspace", tabs: ["Setup", "General", "Appearance"] },
  { name: "Connections", tabs: ["Plex", "Radarr", "Sonarr", "Seerr"] },
  {
    name: "Library & cleanup",
    tabs: ["Libraries", "Storage", "Scheduler", "Deletion Safety"],
  },
  {
    name: "Administration",
    tabs: ["Authentication", "Backup & Maintenance", "System Diagnostics"],
  },
];
function SettingsPage({ revision, refresh, navigate }: Props) {
  const [tab, setTab] = useState("Setup"),
    [live, setLive] = useState(false),
    [liveError, setLiveError] = useState(""),
    [liveBusy, setLiveBusy] = useState(false),
    [plexPending, setPlexPending] = useState(false);
  const { data: s, error } = useData<Row>("/settings", revision);
  const integrations = useData<Row[]>("/integrations", revision),
    storage = useData<Row>("/storage", revision),
    libraries = useData<Row[]>("/libraries", revision),
    schedules = useData<Row[]>("/schedules", revision),
    diagnostics = useData<Row>("/diagnostics", revision);
  async function saveGeneral(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!s) return;
    const f = new FormData(e.currentTarget),
      { dry_run, live_enrolled, ...data } = s;
    for (const [key, value] of f.entries())
      data[key] = ["timezone", "theme", "accent"].includes(key)
        ? value
        : Number(value);
    await act("/settings", "PUT", data);
    refresh();
  }
  return (
    <>
      <PageHeading title="Settings" description="" />
      <ErrorBox text={error} />
      <div className="settings-layout">
        <div className="settings-picker">
          <label htmlFor="settings-section-picker">Settings section</label>
          <select
            id="settings-section-picker"
            value={tab}
            onChange={(event) => setTab(event.target.value)}
          >
            {settingGroups.map((group) => (
              <optgroup key={group.name} label={group.name}>
                {group.tabs.map((section) => (
                  <option key={section}>{section}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <nav className="settings-tabs" aria-label="Settings sections">
          {settingGroups.map((group) => (
            <div
              className="settings-group"
              key={group.name}
              role="group"
              aria-label={group.name}
            >
              <span>{group.name}</span>
              {group.tabs.map((t) => (
                <button
                  key={t}
                  className={tab === t ? "selected" : ""}
                  aria-current={tab === t ? "page" : undefined}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <section className="panel settings-body">
          <h2>{tab}</h2>
          {tab === "Setup" && (
            <>
              <p className="muted">
                Work through these steps in order. Optional integrations can be
                skipped. Live deletion always stays off during setup.
              </p>
              <ol className="setup-steps">
                {[
                  [
                    "Create administrator",
                    "Your local account is ready.",
                    "General",
                    true,
                  ],
                  [
                    "Connect Plex",
                    "Test the media server connection.",
                    "Plex",
                    integrations.data?.some(
                      (i) => i.kind === "plex" && i.health === "connected",
                    ),
                  ],
                  [
                    "Select libraries",
                    "Discover libraries and explicitly enable management.",
                    "Libraries",
                    libraries.data?.some((l) => l.enabled),
                  ],
                  [
                    "Connect Radarr, Sonarr and Seerr",
                    "Optional. Enables monitoring coordination and request protection.",
                    "Radarr",
                    false,
                  ],
                  [
                    "Authorize storage and path mappings",
                    "Validate mounted media roots and map integration paths.",
                    "Storage",
                    storage.data?.roots.length && storage.data?.mappings.length,
                  ],
                  [
                    "Run initial synchronization",
                    "Import media, files and completed watch history.",
                    "Libraries",
                    libraries.data?.some((l) => l.synced_at),
                  ],
                  [
                    "Configure retention presets",
                    "Create a rule and simulate its results.",
                    "Rules",
                    false,
                  ],
                  [
                    "Review dry-run cleanup",
                    "Inspect the exact plan and its safety checks.",
                    "Cleanup Queue",
                    false,
                  ],
                ].map(([title, help, destination, done], i) => (
                  <li key={i}>
                    <span className={done ? "step done" : "step"}>
                      {done ? <Check size={16} /> : i + 1}
                    </span>
                    <div>
                      <b>{title}</b>
                      <small>{help}</small>
                    </div>
                    <Button
                      kind="quiet"
                      onClick={() =>
                        ["Rules", "Cleanup Queue"].includes(String(destination))
                          ? navigate(String(destination))
                          : setTab(String(destination))
                      }
                    >
                      Open
                      <ArrowRight size={14} />
                    </Button>
                  </li>
                ))}
              </ol>
              <Button
                kind="primary"
                onClick={async () => {
                  if (s) {
                    const { dry_run, live_enrolled, ...body } = s;
                    await act("/settings", "PUT", {
                      ...body,
                      onboarding_complete: true,
                    });
                    refresh();
                    navigate("Dashboard");
                  }
                }}
              >
                Complete setup in dry-run mode
                <Check size={16} />
              </Button>
            </>
          )}
          {["Plex", "Radarr", "Sonarr", "Seerr"].includes(tab) && (
            <IntegrationForm
              key={tab}
              kind={tab.toLowerCase()}
              existing={integrations.data?.find(
                (i) => i.kind === tab.toLowerCase(),
              )}
              refresh={refresh}
            />
          )}
          {tab === "Libraries" && (
            <>
              <p className="muted">
                New libraries are excluded until explicitly enabled. Excluded
                libraries cannot be cleaned.
              </p>
              <div className="actions">
                <Button
                  onClick={() => act("/libraries/discover").then(refresh)}
                >
                  <Search size={15} />
                  Discover from Plex
                </Button>
                <Button onClick={() => runJob("full_sync")}>
                  Run initial / full sync
                </Button>
              </div>
              {libraries.data?.map((l) => (
                <div className="library-setting" key={l.id}>
                  <Film size={20} />
                  <div>
                    <b>{l.title}</b>
                    <small>
                      {l.kind} · {l.items} catalog entries · Last sync{" "}
                      {date(l.synced_at)}
                    </small>
                  </div>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={!!l.enabled}
                      onChange={(e) =>
                        act("/libraries/" + l.id, "PUT", {
                          enabled: e.target.checked,
                        }).then(refresh)
                      }
                    />
                    Managed
                  </label>
                </div>
              ))}
            </>
          )}
          {tab === "Storage" && (
            <>
              <p className="muted">
                Use explicit mounted directories. Deletarr records their
                identity and creates a small mount marker. Paths must be
                distinct from /config.
              </p>
              {storage.data?.roots.map((r: Row) => (
                <div className="volume" key={r.id}>
                  <div className="volume-head">
                    <code>{r.path}</code>
                    <Badge tone={r.healthy ? "green" : "red"}>
                      {r.healthy ? "Validated" : "Unavailable"}
                    </Badge>
                  </div>
                  <small>
                    {r.healthy
                      ? bytes(r.available) + " available"
                      : "Cleanup is blocked for this root"}
                  </small>
                  {!r.healthy && (
                    <Button
                      onClick={async () => {
                        if (
                          window.confirm(
                            "Verify that " +
                              r.path +
                              " is the intended host volume. Reauthorization invalidates all plans and requires a full sync. Continue?",
                          )
                        ) {
                          await act(
                            "/storage/roots/" + r.id + "/reauthorize",
                            "POST",
                            {
                              path: r.path,
                              confirmation: "AUTHORIZE THIS MEDIA ROOT",
                            },
                          );
                          refresh();
                        }
                      }}
                    >
                      Reauthorize verified mount
                    </Button>
                  )}
                </div>
              ))}
              <form
                className="section-space"
                onSubmit={async (e) => {
                  e.preventDefault();
                  const form = e.currentTarget;
                  const f = new FormData(form);
                  await act("/storage/roots", "POST", {
                    path: f.get("path"),
                    confirmation: "AUTHORIZE THIS MEDIA ROOT",
                  });
                  form.reset();
                  refresh();
                }}
              >
                <Field
                  label="Authorize a mounted media root"
                  help="Example: /media/movies. This grants deletion authority only after live mode is separately enabled."
                >
                  <input name="path" required placeholder="/media/movies" />
                </Field>
                <Button type="submit">Validate & authorize root</Button>
              </form>
              <h3>Path mappings</h3>
              <MappingSuggestions refresh={refresh} />
              {storage.data?.mappings.map((m: Row) => (
                <div className="mapping" key={m.id}>
                  <Badge>{m.source}</Badge>
                  <code>{m.prefix}</code>
                  <ArrowRight size={14} />
                  <code>
                    {
                      storage.data?.roots.find((r: Row) => r.id === m.root_id)
                        ?.path
                    }
                  </code>
                </div>
              ))}
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  await act("/storage/mappings", "POST", Object.fromEntries(f));
                  refresh();
                }}
              >
                <div className="form-grid">
                  <Field label="Source application">
                    <select name="source">
                      <option>plex</option>
                      <option>radarr</option>
                      <option>sonarr</option>
                    </select>
                  </Field>
                  <Field label="Source path prefix">
                    <input name="prefix" required placeholder="/movies" />
                  </Field>
                  <Field label="Deletarr media root">
                    <select name="root_id" required>
                      <option value="">Choose authorized root</option>
                      {storage.data?.roots.map((r: Row) => (
                        <option key={r.id} value={r.id}>
                          {r.path}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Button type="submit">Add mapping</Button>
              </form>
              <p className="muted">
                Sync again after mapping paths. Never map by title or assume
                matching directory names prove identity.
              </p>
              <PressureSettings
                roots={storage.data?.roots || []}
                changed={refresh}
              />
            </>
          )}
          {tab === "Scheduler" && (
            <>
              <p className="muted">
                Five-field cron schedules use your selected IANA timezone.
                Queued work survives restarts.
              </p>
              {schedules.data?.map((s) => (
                <div key={s.id} className="schedule-row">
                  <CalendarClock size={18} />
                  <div>
                    <b>{s.name}</b>
                    <small>
                      {s.cron} · {s.timezone} · Next {date(s.next_at)}
                    </small>
                  </div>
                  <Button
                    kind="quiet"
                    onClick={() =>
                      act("/schedules/" + s.id, "DELETE", {}).then(refresh)
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const form = e.currentTarget;
                  const f = new FormData(form);
                  await act("/schedules", "POST", {
                    ...Object.fromEntries(f),
                    interval_seconds: f.get("interval_seconds")
                      ? Number(f.get("interval_seconds"))
                      : null,
                    enabled: true,
                  });
                  form.reset();
                  refresh();
                }}
              >
                <div className="form-grid">
                  <Field label="Schedule name">
                    <input name="name" required />
                  </Field>
                  <Field label="Job">
                    <select name="kind">
                      {[
                        "full_sync",
                        "incremental_sync",
                        "watch_history",
                        "health",
                        "evaluate",
                        "storage",
                        "cleanup",
                        "pressure",
                        "reconcile",
                        "notifications",
                        "maintenance",
                      ].map((k) => (
                        <option key={k}>{k}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Cron expression">
                    <input name="cron" defaultValue="0 3 * * *" required />
                  </Field>
                  <Field label="Timezone">
                    <input
                      name="timezone"
                      defaultValue={s?.timezone || "UTC"}
                      required
                    />
                  </Field>
                  <Field
                    label="Optional interval (seconds)"
                    help="Leave blank to use cron."
                  >
                    <input name="interval_seconds" type="number" min={60} />
                  </Field>
                </div>
                <Button type="submit" kind="primary">
                  Create schedule
                </Button>
              </form>
            </>
          )}
          {["General", "Appearance", "Deletion Safety"].includes(tab) && s && (
            <>
              <form key={tab + s.version} onSubmit={saveGeneral}>
                {tab === "General" && (
                  <Field
                    label="Timezone"
                    help="Dates are stored in UTC and displayed in this timezone."
                  >
                    <input name="timezone" defaultValue={s.timezone} required />
                  </Field>
                )}
                {tab === "Appearance" && (
                  <AppearanceSettings theme={s.theme} accent={s.accent} />
                )}
                {tab === "Deletion Safety" && (
                  <>
                    <div className="simulation-banner">
                      <ShieldCheck size={20} />
                      <b>
                        {s.dry_run
                          ? "Dry-run is active. No files are deleted."
                          : "Live permanent deletion is enabled."}
                      </b>
                    </div>
                    <div className="form-grid">
                      {[
                        ["minimum_age_days", "Minimum library age (days)"],
                        [
                          "recent_playback_hours",
                          "Recent playback protection (hours)",
                        ],
                        [
                          "request_protection_days",
                          "Recent request protection (days)",
                        ],
                        ["max_items", "Maximum media items"],
                        ["max_files", "Maximum physical files"],
                        ["max_logical_bytes", "Maximum logical bytes"],
                        [
                          "max_reclaimable_bytes",
                          "Maximum estimated reclaimable bytes",
                        ],
                        ["max_library_percent", "Maximum library percentage"],
                      ].map(([key, label]) => (
                        <Field key={key} label={label}>
                          <input
                            name={key}
                            type="number"
                            min={1}
                            required
                            defaultValue={s[key]}
                          />
                        </Field>
                      ))}
                    </div>
                    <p className="muted">
                      Limits are shared across cleanup submissions in a rolling
                      24-hour safety window. They cannot be bypassed by
                      splitting a job.
                    </p>
                  </>
                )}
                <Button type="submit" kind="primary">
                  Save settings
                </Button>
              </form>
              {tab === "Deletion Safety" && (
                <div className="danger-zone">
                  <h3>Permanent deletion</h3>
                  <p>
                    Files are removed from the filesystem. Deletarr has no trash
                    or recovery folder. This does not guarantee secure data
                    erasure.
                  </p>
                  {s.dry_run ? (
                    <Button kind="danger" onClick={() => setLive(true)}>
                      Enable live deletion…
                    </Button>
                  ) : (
                    <Button
                      onClick={() => act("/settings/dry-run").then(refresh)}
                    >
                      Return to dry-run immediately
                    </Button>
                  )}
                </div>
              )}
            </>
          )}
          {tab === "Authentication" && (
            <>
              <p className="muted">
                Single administrator. Local authentication remains available
                independently of Plex.
              </p>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  await act("/auth/password", "POST", {
                    current_password: f.get("current"),
                    new_password: f.get("new"),
                  });
                  location.reload();
                }}
              >
                <Field label="Current password">
                  <input
                    type="password"
                    name="current"
                    required
                    autoComplete="current-password"
                  />
                </Field>
                <Field label="New password">
                  <input
                    type="password"
                    name="new"
                    required
                    minLength={12}
                    autoComplete="new-password"
                  />
                </Field>
                <Button type="submit">
                  Change password & invalidate sessions
                </Button>
              </form>
              <h3>Plex administrator identity</h3>
              <p className="muted">
                Enroll your own Plex identity. Other Plex users will never gain
                administrator access.
              </p>
              <Button
                onClick={async () => {
                  const r = await api(
                    "/auth/plex/start?enroll=true",
                    "POST",
                    {},
                  );
                  window.open(r.url, "_blank", "noopener,noreferrer");
                  setPlexPending(true);
                }}
              >
                Enroll Plex identity
              </Button>
              {plexPending && (
                <Button
                  onClick={async () => {
                    const r = await api("/auth/plex/poll", "POST", {});
                    if (r.pending) notify("Finish the Plex sign-in first");
                    else {
                      setCsrf(r.csrf);
                      setPlexPending(false);
                      notify("Plex identity enrolled");
                    }
                  }}
                >
                  Complete enrollment
                </Button>
              )}
            </>
          )}
          {tab === "Backup & Maintenance" && (
            <>
              <p className="muted">
                Database backups are consistent SQLite snapshots stored in
                /config. They contain encrypted credentials and private viewing
                history. Protect them and back up credentials.key separately.
              </p>
              <div className="action-stack">
                <Button
                  onClick={async () => {
                    const r = await act("/backup");
                    notify("Backup created: " + r.filename);
                  }}
                >
                  <Database size={16} />
                  Create protected database backup
                </Button>
                <Button
                  onClick={async () => {
                    const r = await api("/settings/export");
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(
                      new Blob([JSON.stringify(r, null, 2)], {
                        type: "application/json",
                      }),
                    );
                    a.download = "deletarr-settings.json";
                    a.click();
                    URL.revokeObjectURL(a.href);
                  }}
                >
                  <ArrowDownToLine size={16} />
                  Export settings without secrets
                </Button>
                <Button onClick={() => runJob("maintenance")}>
                  Run database maintenance
                </Button>
              </div>
              <Field
                label="Import general settings from an export"
                help="Rules, connections and destructive mode require separate review. Import never enables live deletion."
              >
                <input
                  type="file"
                  accept="application/json"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file || !s) return;
                    try {
                      const imported = JSON.parse(await file.text());
                      if (imported.format !== 1)
                        throw new Error("Unsupported export format");
                      const { dry_run, live_enrolled, version, ...values } =
                        imported.settings;
                      await act("/settings", "PUT", {
                        ...values,
                        version: s.version,
                      });
                      refresh();
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}
                />
              </Field>
            </>
          )}
          {tab === "System Diagnostics" && (
            <>
              <p className="muted">
                Read-only runtime checks. Credentials are excluded.
              </p>
              <Button onClick={refresh}>
                <RefreshCw size={15} />
                Refresh diagnostics
              </Button>
              <pre>{JSON.stringify(diagnostics.data, null, 2)}</pre>
            </>
          )}
        </section>
      </div>
      {live && (
        <Modal title="Enable permanent deletion" close={() => setLive(false)}>
          <div className="error">
            <Trash2 size={19} />
            This enables scheduled and manually approved permanent deletion.
            There is no recovery folder.
          </div>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              setLiveError("");
              setLiveBusy(true);
              try {
                await api("/settings/live", "POST", {
                  password: f.get("password"),
                  acknowledge_permanent_deletion: f.get("acknowledge") === "on",
                });
                setLive(false);
                refresh();
              } catch (error) {
                setLiveError((error as Error).message);
              } finally {
                setLiveBusy(false);
              }
            }}
          >
            <Field label="Administrator password">
              <input
                type="password"
                name="password"
                required
                autoComplete="current-password"
              />
            </Field>
            <label className="checkbox">
              <input name="acknowledge" type="checkbox" required />I understand
              that live cleanup permanently deletes media with no recovery
              folder.
            </label>
            <ErrorBox text={liveError} />
            <div className="modal-actions">
              <Button onClick={() => setLive(false)}>Keep dry-run</Button>
              <Button kind="danger" type="submit" disabled={liveBusy}>
                {liveBusy ? "Enabling…" : "Enable live deletion"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
function PlexConnectForm({ refresh }: { refresh: () => void }) {
  const [authUrl, setAuthUrl] = useState(""),
    [waiting, setWaiting] = useState(false),
    [busy, setBusy] = useState(false),
    [servers, setServers] = useState<Row[] | null>(null),
    [serverId, setServerId] = useState(""),
    [uri, setUri] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    if (!waiting) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api("/auth/plex/poll", "POST", {});
        if (cancelled) return;
        if (result.pending) {
          timer = setTimeout(poll, 2500);
          return;
        }
        setWaiting(false);
        setServers(result.servers);
        setServerId(result.servers[0]?.id || "");
        setUri(result.servers[0]?.connections[0] || "");
      } catch (e) {
        if (!cancelled) {
          setError((e as Error).message);
          setWaiting(false);
        }
      }
    };
    timer = setTimeout(poll, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [waiting]);
  return (
    <section aria-label="Connect with Plex">
      <h3>Connect with Plex</h3>
      <p className="muted">
        Sign in on Plex’s website, then select your owned server. Your token
        stays on the backend.
      </p>
      <Button
        kind="primary"
        disabled={busy || waiting}
        onClick={async () => {
          const popup = window.open("about:blank", "deletarr-plex-connect");
          if (popup) popup.opener = null;
          setBusy(true);
          setError("");
          setServers(null);
          try {
            const result = await api(
              "/auth/plex/start?connect=true",
              "POST",
              {},
            );
            setAuthUrl(result.url);
            if (popup) popup.location.href = result.url;
            setWaiting(true);
          } catch (e) {
            popup?.close();
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {waiting ? "Waiting for Plex sign-in…" : "Sign in with Plex"}
      </Button>
      {waiting && (
        <p role="status">
          Finish signing in in the Plex tab.{" "}
          <a href={authUrl} target="_blank" rel="noopener noreferrer">
            Open Plex sign-in
          </a>
        </p>
      )}
      {servers && servers.length === 0 && (
        <p role="status">
          No owned Plex servers were found. Sign in with the server owner’s Plex
          account.
        </p>
      )}
      {servers && servers.length > 0 && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const form = new FormData(e.currentTarget);
            try {
              await api("/integrations/plex/connect", "POST", {
                server_id: serverId,
                uri,
                allow_private: form.get("private") === "on",
                enable_login: form.get("login") === "on",
              });
              setServers(null);
              setAuthUrl("");
              refresh();
              notify("Plex connected");
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Your Plex server">
            <select
              value={serverId}
              onChange={(e) => {
                setServerId(e.target.value);
                setUri(
                  servers.find((s) => s.id === e.target.value)
                    ?.connections[0] || "",
                );
              }}
            >
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Server connection"
            help="Choose an address reachable from this container. Use a valid Plex HTTPS hostname or local HTTP."
          >
            <select
              value={uri}
              onChange={(e) => setUri(e.target.value)}
              required
            >
              {(servers.find((s) => s.id === serverId)?.connections || []).map(
                (u: string) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ),
              )}
            </select>
          </Field>
          <label className="checkbox">
            <input name="private" type="checkbox" />
            Explicitly trust this private/LAN integration destination
          </label>
          <label className="checkbox">
            <input name="login" type="checkbox" defaultChecked />
            Use this Plex account to sign in to Deletarr
          </label>
          <Button kind="primary" type="submit" disabled={busy || !uri}>
            {busy ? "Checking server…" : "Connect selected server"}
          </Button>
        </form>
      )}
      <ErrorBox text={error} />
    </section>
  );
}
function IntegrationForm({
  kind,
  existing,
  refresh,
}: {
  kind: string;
  existing?: Row;
  refresh: () => void;
}) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <>
      <p className="muted">
        {kind === "plex"
          ? "Plex provides your catalog, artwork, watched status and active playback safety checks."
          : "This optional integration coordinates " +
            (
              {
                radarr: "movie monitoring and exact file deletion",
                sonarr: "episode, season and series monitoring",
                seerr: "request protection and availability",
              } as Record<string, string>
            )[kind] +
            "."}
      </p>
      {kind === "plex" && !existing?.environment_managed && (
        <>
          <PlexConnectForm refresh={refresh} />
          <h3>Manual connection</h3>
        </>
      )}
      <form
        key={existing?.url}
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          setBusy(true);
          const f = new FormData(e.currentTarget);
          try {
            await api("/integrations/" + kind, "PUT", {
              url: f.get("url"),
              secret: f.get("secret"),
              allow_private: f.get("allow_private") === "on",
              timeout: Number(f.get("timeout")),
            });
            notify("Connection settings saved");
            refresh();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field
          label="Server URL"
          help="Use the address reachable from inside the Deletarr container."
        >
          <input
            name="url"
            type="url"
            placeholder={
              kind === "plex"
                ? "http://192.168.1.10:32400"
                : "http://192.168.1.10:7878"
            }
            defaultValue={existing?.url}
            required
            disabled={existing?.environment_managed}
          />
        </Field>
        <Field
          label={kind === "plex" ? "Plex token" : "API key"}
          help={
            existing
              ? "A saved credential exists. Leave blank to preserve it."
              : "Stored encrypted on disk. Never sent back to your browser."
          }
        >
          <input
            name="secret"
            type="password"
            autoComplete="new-password"
            disabled={existing?.environment_managed}
          />
        </Field>
        <Field label="Request timeout (seconds)">
          <input
            name="timeout"
            type="number"
            min={2}
            max={60}
            defaultValue={existing?.timeout || 15}
          />
        </Field>
        <label className="checkbox">
          <input
            name="allow_private"
            type="checkbox"
            defaultChecked={existing?.allow_private}
          />
          Explicitly trust this private/LAN integration destination
        </label>
        <ErrorBox text={error} />
        <div className="actions">
          <Button
            type="submit"
            kind="primary"
            disabled={busy || existing?.environment_managed}
          >
            Save connection
          </Button>
          <Button
            disabled={!existing}
            onClick={async () => {
              setBusy(true);
              try {
                const r = await api(
                  "/integrations/" + kind + "/test",
                  "POST",
                  {},
                );
                notify("Connected" + (r.version ? " · " + r.version : ""));
                refresh();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Activity size={16} />
            Test connection
          </Button>
        </div>
      </form>
      {existing?.environment_managed && (
        <p>
          Managed by environment variables. Change deployment configuration to
          update this connection.
        </p>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
