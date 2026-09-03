// Generic client for the backend's background-job routes (routers/jobs.py)
import { apiUrl, wsUrl } from "$lib/backend";

export type JobStatus = "idle" | "running" | "complete" | "error";

export type JobState = {
  status: JobStatus;
  message: string;
  processed: number;
  total: number;
  [key: string]: unknown; // job-specific extras
};

// How the backend returns job info
export type JobInfo = {
  name: string;
  supports_force: boolean;
  state: JobState;
};

// Live progress for one job via its websocket.
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

export async function stopJob(name: string): Promise<void> {
  await fetch(apiUrl(`/api/jobs/${name}/stop`), {
    method: "POST",
  });
}
