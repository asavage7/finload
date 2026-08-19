<script lang="ts">
  import { IconLoader2, IconPlugConnectedX } from "@tabler/icons-svelte";
  import { onMount } from "svelte";

  // Shown while the backend is still starting, and again if it never answers.
  // `failed` swaps the spinner for a retry prompt; the parent owns the polling.
  export let failed = false;
  export let onRetry: () => void;

  // The sidecar usually answers fast enough that a spinner would only flash.
  // Nothing is drawn until this elapses, so a normal launch looks instant and
  // only a genuinely slow start shows progress.
  let visible = false;
  onMount(() => {
    const t = setTimeout(() => (visible = true), 400);
    return () => clearTimeout(t);
  });
</script>

{#if visible || failed}
  <div
    class="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-zinc-900 text-center"
  >
    {#if failed}
      <IconPlugConnectedX size={32} class="text-amber-300" />
      <div class="space-y-1">
        <p class="text-md font-medium text-amber-300">Couldn't reach the player service.</p>
        <p class="max-w-sm text-sm text-zinc-400">
          Finload's audio service didn't start. Restarting the app usually fixes
          this. If it keeps happening, please report it.
        </p>
      </div>
      <button
        on:click={onRetry}
        class="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm font-medium text-white transition hover:cursor-pointer hover:bg-white/10"
      >
        Try again
      </button>
    {:else}
      <IconLoader2 size={32} class="animate-spin text-zinc-500" />
      <p class="text-lg font-medium text-zinc-500">Starting Finload...</p>
    {/if}
  </div>
{/if}
