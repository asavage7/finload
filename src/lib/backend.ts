const defaultBackendUrl = "http://127.0.0.1:8000";

function normalizeBackendUrl(value: string) {
  return value.replace(/\/$/, "");
}

const explicitBackendUrl = import.meta.env.VITE_BACKEND_URL?.trim();
const backendUrl = import.meta.env.DEV
  ? ""
  : normalizeBackendUrl(explicitBackendUrl || defaultBackendUrl);

function joinPath(base: string, path: string) {
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function apiUrl(path: string) {
  return joinPath(backendUrl, path);
}

export function wsUrl(path: string) {
  if (import.meta.env.DEV) {
    const protocol =
      typeof location !== "undefined" && location.protocol === "https:"
        ? "wss:"
        : "ws:";
    const host = typeof location !== "undefined" ? location.host : "localhost:1420";
    return joinPath(`${protocol}//${host}`, path);
  }

  const backendOrigin = new URL(backendUrl);
  const websocketProtocol = backendOrigin.protocol === "https:" ? "wss:" : "ws:";
  const backendWsUrl = `${websocketProtocol}//${backendOrigin.host}`;
  return joinPath(backendWsUrl, path);
}
