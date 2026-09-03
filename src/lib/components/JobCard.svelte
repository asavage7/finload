<script lang="ts">
  import {
    IconPlayerPlayFilled,
    IconPlayerStopFilled,
    IconRefresh,
    IconLoader2,
  } from "@tabler/icons-svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import type { JobState } from "$lib/utils/backgroundJobs";

  export let job: {
    // From the settings schema
    label: string;
    description: string;
    enabled: boolean;
    disabled_reason: string | null;
    // From the backend
    supports_force: boolean;
    state: JobState;
  };
  export let onRun: (force: boolean) => void;
  export let onStop: () => void;

  function percent(s: JobState): number {
    return s.total > 0
      ? Math.min(100, Math.round((s.processed / s.total) * 100))
      : 0;
  }

  function formatEta(seconds: unknown): string {
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
      return "Unknown time remaining";
    }

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    return `${String(hours)}h ${String(minutes).padStart(2, "0")}m ${String(secs).padStart(2, "0")}s`;
  }
</script>

<div class="flex flex-col gap-3" class:opacity-60={!job.enabled}>
  <div class="flex items-center justify-between gap-4">
    <div class="min-w-0">
      <div class="text-sm font-medium text-white">{job.label}</div>
      <div class="text-xs text-zinc-500 mt-0.5">
        {#if !job.enabled}
          <span class="text-zinc-400">{job.disabled_reason}</span>
        {:else if job.state.status === "running"}
          {job.state.message}
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
      {#if job.state.status === "running" && job.enabled}
        <span class="text-xs text-zinc-500 text-mono">{formatEta(job.state.eta_seconds)}</span>
        <div class="group relative h-8 w-8">
          <IconButton
            disabled
            title="Running"
            class="absolute inset-0 opacity-60 transition-opacity group-hover:opacity-0 cursor-not-allowed"
          >
            <IconLoader2 size={16} class="animate-spin" />
          </IconButton>
          <IconButton
            title="Running"
            class="absolute inset-0 opacity-0 transition-opacity group-hover:opacity-100"
            on:click={() => onStop()}
          >
            <IconPlayerStopFilled size={16} />
          </IconButton>
        </div>
      {:else if job.enabled}
        <IconButton
          accent
          on:click={() => onRun(false)}
          disabled={!job.enabled}
          title="Run"
          aria-label="Run {job.label}"
          class="bg-blue-500! {!job.enabled
            ? 'opacity-50 cursor-not-allowed'
            : ''}"
        >
          <IconPlayerPlayFilled size={16} />
        </IconButton>
        {#if job.supports_force}
          <IconButton
            on:click={() => onRun(true)}
            disabled={!job.enabled}
            title="Re-run all"
            aria-label="Re-run {job.label}"
            class={!job.enabled ? "opacity-50 cursor-not-allowed" : ""}
          >
            <IconRefresh size={16} />
          </IconButton>
        {/if}
      {/if}
    </div>
  </div>

  {#if job.state.status === "running"}
    <div class="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden">
      <div
        class="h-full bg-blue-500 transition-all duration-300"
        style="width: {job.state.total > 0 ? percent(job.state) : 100}%"
      ></div>
    </div>
  {/if}
</div>
