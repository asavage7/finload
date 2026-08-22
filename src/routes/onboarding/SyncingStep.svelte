<script lang="ts">
  import StepButton from "./StepButton.svelte";

  type SyncState = {
    status: "idle" | "running" | "complete" | "error";
    message: string;
    processed: number;
    total: number;
    added: number;
    removed: number;
  };

  export let syncState: SyncState;
  export let syncPercent: number;
  export let onRetry: () => void;
  export let onBack: () => void;
</script>

<h1 class="text-2xl font-bold">Syncing your library</h1>
<p class="text-zinc-400 -mt-2">
  {#if syncState.status === "running"}
    {syncState.message}{syncState.total > 0 ? ` — ${syncState.processed}/${syncState.total}` : ""}
  {:else if syncState.status === "error"}
    <span class="text-red-400">{syncState.message}</span>
  {:else}
    Starting…
  {/if}
</p>

<div class="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden">
  <div
    class="h-full bg-blue-500 transition-all duration-300"
    class:animate-pulse={syncState.total === 0 && syncState.status === "running"}
    style="width: {syncState.status === 'running' ? (syncState.total > 0 ? syncPercent : 100) : 0}%"
  ></div>
</div>

<div class="flex gap-3 mt-4">
  <StepButton on:click={onBack}>Back to configuration</StepButton>
  {#if syncState.status === "error"}
    <StepButton variant="primary" on:click={onRetry}>Retry sync</StepButton>
  {/if}
</div>
