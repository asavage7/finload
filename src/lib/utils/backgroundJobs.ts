// Generic client for the backend's background-job routes (routers/jobs.py):
// one status shape, one websocket pattern, for sync / metadata / genre
// enrichment (and any future job) instead of bespoke wiring per job.
import { apiUrl, wsUrl } from "$lib/backend";

export type JobStatus = "idle" | "running" | "complete" | "error";

export type JobState = {
  status: JobStatus;
  message: string;
  processed: number;
  total: number;
  [key: string]: unknown; // job-specific extras, e.g. sync's added/removed
};

// Shape of one entry from GET /api/jobs. Display metadata (label,
// description, which settings key gates it) lives in the frontend's
// settings schema instead — this is only what the backend actually owns.
export type JobInfo = {
  name: string;
  supports_force: boolean;
  state: JobState;
};

// Live progress for one job via its websocket. Returns an unsubscribe function.
//
// Reconnects on any close: the sidecar restarting (a dev --reload, or a real
// respawn) or the machine sleeping/waking drops the socket, and with no
// reconnect the UI would freeze on whatever state it last received -- e.g. a
// spinner stuck on "running" forever even after the job actually finished or
// got turned off backend-side. A fresh connection's add_listener() replies
// with the job's current state immediately, so reconnecting also self-heals
// that stale state.
export function subscribeJobStatus(name: string, onUpdate: (state: JobState) => void): () => void {
  let unsubscribed = false;
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connect() {
    ws = new WebSocket(wsUrl(`/ws/jobs/${name}`));
    ws.onmessage = (event) => {
      onUpdate(JSON.parse(event.data));
    };
    ws.onclose = () => {
      if (unsubscribed) return;
      reconnectTimer = setTimeout(connect, 1000);
    };
  }
  connect();

  return () => {
    unsubscribed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
  };
}

export async function startJob(name: string, force = false): Promise<void> {
  await fetch(apiUrl(`/api/jobs/${name}/start`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}
