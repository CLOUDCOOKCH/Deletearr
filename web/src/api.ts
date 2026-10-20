let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (response.status === 401)
    window.dispatchEvent(new Event("deletarr-session-expired"));
  if (!response.ok)
    throw new Error(data.error || data.detail || "Request failed");
  return data;
}
export function bytes(value?: number | null) {
  if (value == null) return "Unavailable";
  if (value === 0) return "0 B";
  const unit = Math.min(
    4,
    Math.floor(Math.log(Math.abs(value)) / Math.log(1024)),
  );
  return `${(value / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
let timezone = "UTC";
export function setTimezone(value: string) {
  timezone = value;
}
export function date(value?: number | null) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: timezone,
      }).format(value * 1000)
    : "Not available";
}
