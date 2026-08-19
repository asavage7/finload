<!--
  Lists the backend's background jobs (from GET /api/jobs) as re-runnable tasks,
  each with live progress over its websocket. Purely data-driven: the backend's
  _JOB_META table decides which tasks appear, their labels, and when a settings
  gate disables one. To change the task list, edit _JOB_META in routers/jobs.py.

  "Run" fills in whatever isn't done yet; "Re-run all" (force) reprocesses the
  whole library and asks for confirmation first.
-->
<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    IconPlayerPlayFilled,
    IconRefresh,
    IconLoader2,
  } from "@tabler/icons-svelte";
  import { apiUrl } from "$lib/backend";
  import { showConfirm } from "$lib/store";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import {
    subscribeJobStatus,
    startJob,
    type JobState,
  } from "$lib/utils/backgroundJobs";

  // Jobs to hide here. Sync has its own dedicated card on the settings page.
  export let exclude: string[] = ["sync"];

  type JobInfo = {
    name: string;
    label: string;
    description: string;
    enabled: boolean;
    disabled_reason: string | null;
    state: JobState;
  };

  let jobs: JobInfo[] = [];
  let loaded = false;
  let unsubscribers: Array<() => void> = [];

  function percent(s: JobState): number {
    return s.total > 0 ? Math.min(100, Math.round((s.processed / s.total) * 100)) : 0;
  }

  function setState(name: string, state: JobState) {
    jobs = jobs.map((job) => (job.name === name ? { ...job, state } : job));
  }

  async function run(job: JobInfo, force: boolean) {
    if (!job.enabled || job.state.status === "running") return;
    if (force) {
      const ok = await showConfirm({
        title: `Re-run ${job.label}?`,
        message: "This reprocesses your whole library and can take a while.",
        confirmLabel: "Re-run",
      });
      if (!ok) return;
    }
    // Optimistic flip so the button reacts instantly; the socket corrects it.
    setState(job.name, { ...job.state, status: "running", message: "Starting…", processed: 0, total: 0 });
    try {
      await startJob(job.name, force);
    } catch {
      setState(job.name, { ...job.state, status: "error", message: "Could not reach backend" });
    }
  }

  onMount(async () => {
    try {
      const res = await fetch(apiUrl("/api/jobs"));
      if (res.ok) {
        const data = await res.json();
        jobs = (data.jobs as JobInfo[]).filter((job) => !exclude.includes(job.name));
        unsubscribers = jobs.map((job) =>
          subscribeJobStatus(job.name, (state) => setState(job.name, state)),
        );
      }
    } catch {
      // backend unavailable; leave the list empty
    }
    loaded = true;
  });

  onDestroy(() => unsubscribers.forEach((unsubscribe) => unsubscribe()));
</script>

{#if !loaded}
  <div class="text-xs text-zinc-500 px-1">Loading tasks…</div>
{:else}
  <div class="flex flex-col gap-2">
    {#each jobs as job (job.name)}
      <div
        class="bg-zinc-800 rounded-xl border border-white/5 p-4 flex flex-col gap-3"
        class:opacity-60={!job.enabled}
      >
        <div class="flex items-center justify-between gap-4">
          <div class="min-w-0">
            <div class="text-sm font-medium text-white">{job.label}</div>
            <div class="text-xs text-zinc-500 mt-0.5">
              {#if !job.enabled}
                <span class="text-amber-400/80">{job.disabled_reason}</span>
              {:else if job.state.status === "running"}
                {job.state.message}{job.state.total > 0
                  ? ` — ${job.state.processed}/${job.state.total}`
                  : ""}
              {:else if job.state.status === "error"}
                <span class="text-red-400">{job.state.message}</span>
              {:else if job.state.status === "complete"}
                {job.state.message}
              {:else}
                {job.description}
              {/if}
            </div>
          </div>

          <div class="flex items-center gap-2 shrink-0">
            {#if job.state.status === "running"}
              <IconButton disabled title="Running" class="opacity-60 cursor-not-allowed">
                <IconLoader2 size={16} class="animate-spin" />
              </IconButton>
            {:else}
              <IconButton
                accent
                on:click={() => run(job, false)}
                disabled={!job.enabled}
                title="Run"
                aria-label="Run {job.label}"
                class="bg-blue-500! {!job.enabled ? 'opacity-50 cursor-not-allowed' : ''}"
              >
                <IconPlayerPlayFilled size={16} />
              </IconButton>
              <IconButton
                on:click={() => run(job, true)}
                disabled={!job.enabled}
                title="Re-run all"
                aria-label="Re-run {job.label}"
                class={!job.enabled ? "opacity-50 cursor-not-allowed" : ""}
              >
                <IconRefresh size={16} />
              </IconButton>
            {/if}
          </div>
        </div>

        {#if job.state.status === "running"}
          <div class="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden">
            <div
              class="h-full bg-blue-500 transition-all duration-300"
              class:animate-pulse={job.state.total === 0}
              style="width: {job.state.total > 0 ? percent(job.state) : 100}%"
            ></div>
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/if}
