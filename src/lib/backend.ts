const defaultBackendUrl = "http://127.0.0.1:8000";

function normalizeBackendUrl(value: string) {
  return value.replace(/\/$/, "");
}

export const backendUrl = normalizeBackendUrl(
  import.meta.env.VITE_BACKEND_URL ?? defaultBackendUrl,
);

const backendOrigin = new URL(backendUrl);
const websocketProtocol = backendOrigin.protocol === "https:" ? "wss:" : "ws:";
export const backendWsUrl = `${websocketProtocol}//${backendOrigin.host}`;

function joinPath(base: string, path: string) {
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function apiUrl(path: string) {
  return joinPath(backendUrl, path);
}

export function wsUrl(path: string) {
  return joinPath(backendWsUrl, path);
}
