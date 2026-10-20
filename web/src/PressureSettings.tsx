import { useEffect, useState, type FormEvent } from "react";
import { api, bytes } from "./api";
type Row = Record<string, any>;
export function PressureSettings({
  roots,
  changed,
}: {
  roots: Row[];
  changed: () => void;
}) {
  const [policies, setPolicies] = useState<Row[]>([]),
    [preview, setPreview] = useState<Row | null>(null),
    [error, setError] = useState("");
  const reload = () =>
    api<Row[]>("/storage/pressure")
      .then(setPolicies)
      .catch((e) => setError(e.message));
  useEffect(() => {
    reload();
  }, []);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    const scale: Record<string, number> = {
      gb: 1e9,
      tb: 1e12,
      gib: 1024 ** 3,
      tib: 1024 ** 4,
      bytes: 1,
      percent: 1,
    };
    const factor = scale[String(data.unit)];
    try {
      await api("/storage/pressure", "POST", {
        ...data,
        unit: data.unit === "percent" ? "percent" : "bytes",
        threshold: Number(data.threshold) * factor,
        target: Number(data.target) * factor,
        enabled: true,
      });
      form.reset();
      reload();
      changed();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <section className="section-space">
      <h3>Storage pressure policies</h3>
      <p className="muted">
        Choose a recovery target for this volume. Only retention-eligible media
        can be selected. Approvals and grace periods still apply.
      </p>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {policies.map((p) => (
        <div className="schedule-row" key={p.id}>
          <div>
            <b>{p.name}</b>
            <small>
              Below{" "}
              {p.unit === "percent" ? p.threshold + "%" : bytes(p.threshold)} →
              recover to{" "}
              {p.unit === "percent" ? p.target + "%" : bytes(p.target)}
            </small>
          </div>
          <button
            className="button"
            onClick={async () => {
              try {
                setPreview(
                  await api(
                    "/storage/pressure/" + p.id + "/preview",
                    "POST",
                    {},
                  ),
                );
                setError("");
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Preview
          </button>
          <button
            className="button quiet"
            onClick={async () => {
              await api("/storage/pressure/" + p.id, "DELETE", {});
              reload();
            }}
          >
            Remove
          </button>
        </div>
      ))}
      {preview && (
        <div className="section-space">
          <p>
            <b>
              {preview.under_pressure
                ? "Below threshold"
                : "No storage pressure"}
            </b>{" "}
            · {preview.plans.length} plans ·{" "}
            {bytes(preview.estimated_reclaimable)} estimated reclaimable
          </p>
          <p className="muted">
            {preview.target_achievable
              ? "Recovery target is covered by the estimated savings."
              : "The recovery target cannot be reached with currently eligible candidates."}
          </p>
          {preview.plans.map((p: Row) => (
            <p key={p.id}>
              {p.title} · {bytes(p.estimated_reclaimable)} · review in Cleanup
              Queue
            </p>
          ))}
          {preview.blocked.map((b: Row) => (
            <p key={b.candidate_id} className="muted">
              Blocked: {b.reason}
            </p>
          ))}
        </div>
      )}
      <form onSubmit={save}>
        <div className="form-grid">
          <label className="field">
            <span>Policy name</span>
            <input name="name" required />
          </label>
          <label className="field">
            <span>Volume root</span>
            <select name="root_id" required>
              <option value="">Choose root</option>
              {roots.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.path}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Unit</span>
            <select name="unit">
              <option value="gb">GB (1,000,000,000 bytes)</option>
              <option value="tb">TB (1,000,000,000,000 bytes)</option>
              <option value="gib">GiB (1,073,741,824 bytes)</option>
              <option value="tib">TiB (1,099,511,627,776 bytes)</option>
              <option value="bytes">Bytes</option>
              <option value="percent">Percentage of total capacity</option>
            </select>
          </label>
          <label className="field">
            <span>Minimum available space</span>
            <input
              name="threshold"
              type="number"
              min="0.001"
              step="any"
              required
            />
          </label>
          <label className="field">
            <span>Recovery target</span>
            <input
              name="target"
              type="number"
              min="0.001"
              step="any"
              required
            />
          </label>
          <label className="field">
            <span>Never-watched ordering</span>
            <select name="never_order">
              <option value="exclude">Exclude</option>
              <option value="oldest_added">
                Oldest added first, after watched content
              </option>
              <option value="newest_added">
                Newest added first, after watched content
              </option>
            </select>
          </label>
        </div>
        <button className="button" type="submit">
          Add pressure policy
        </button>
      </form>
      <p className="muted">
        Schedule a “pressure” job to evaluate enabled policies automatically. It
        stops at the recovery target or the global deletion limits.
      </p>
    </section>
  );
}
