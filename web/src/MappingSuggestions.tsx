import { useState } from "react";
import { api } from "./api";
type Row = Record<string, any>;
export function MappingSuggestions({ refresh }: { refresh: () => void }) {
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <section className="section-space">
      <button
        className="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            setData(await api("/storage/suggestions"));
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Checking source paths…" : "Find mapping suggestions"}
      </button>
      <p className="muted">
        Read-only sample checks compare relative paths and file sizes against
        your authorized roots. You must confirm the applications use the same
        underlying storage.
      </p>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {data && !data.suggestions.length && (
        <p className="muted">
          No verified sample matches. Synchronize Plex metadata first or enter a
          mapping manually.
        </p>
      )}
      {data?.unavailable.length > 0 && (
        <p className="muted">
          Could not inspect: {data?.unavailable.join(", ")}
        </p>
      )}
      {data?.suggestions.map((s: Row) => (
        <div className="mapping" key={s.source + s.prefix + s.root_id}>
          <span className="badge">{s.source}</span>
          <code>
            {s.prefix} → {s.root_path}
          </code>
          <span>{s.matched_samples.length} matching sample(s)</span>
          <button
            className="button"
            onClick={async () => {
              if (
                !window.confirm(
                  `Confirm that ${s.source}:${s.prefix} and ${s.root_path} are the same underlying storage. File-size matches alone are not proof. Add this mapping?`,
                )
              )
                return;
              try {
                await api("/storage/mappings", "POST", {
                  source: s.source,
                  prefix: s.prefix,
                  root_id: s.root_id,
                });
                setData(null);
                refresh();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Confirm mapping
          </button>
        </div>
      ))}
    </section>
  );
}
