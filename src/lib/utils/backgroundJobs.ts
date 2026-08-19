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

export const idleJobState: JobState = { status: "idle", message: "", processed: 0, total: 0 };

// Live progress for one job via its websocket. Returns an unsubscribe function.
export function subscribeJobStatus(name: string, onUpdate: (state: JobState) => void): () => void {
  const ws = new WebSocket(wsUrl(`/ws/jobs/${name}`));
  ws.onmessage = (event) => {
    onUpdate(JSON.parse(event.data));
  };
  return () => ws.close();
}

export async function startJob(name: string, force = false): Promise<void> {
  await fetch(apiUrl(`/api/jobs/${name}/start`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
}
