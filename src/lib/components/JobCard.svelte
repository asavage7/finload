<script lang="ts">
  import {
    IconPlayerPlayFilled,
    IconRefresh,
    IconLoader2,
  } from "@tabler/icons-svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import type { JobState } from "$lib/utils/backgroundJobs";

  // Label/description/enabled/disabled_reason come from the settings
  // schema (see +page.svelte); state/supports_force come from the backend.
  export let job: {
    label: string;
    description: string;
    enabled: boolean;
    disabled_reason: string | null;
    supports_force: boolean;
    state: JobState;
  };
  export let onRun: (force: boolean) => void;

  function percent(s: JobState): number {
    return s.total > 0 ? Math.min(100, Math.round((s.processed / s.total) * 100)) : 0;
  }
</script>

<div class="flex flex-col gap-3" class:opacity-60={!job.enabled}>
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
          on:click={() => onRun(false)}
          disabled={!job.enabled}
          title="Run"
          aria-label="Run {job.label}"
          class="bg-blue-500! {!job.enabled ? 'opacity-50 cursor-not-allowed' : ''}"
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
