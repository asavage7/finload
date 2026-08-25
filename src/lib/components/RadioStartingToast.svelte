<script lang="ts">
  import { IconLoader2 } from "@tabler/icons-svelte";
  import { radioStarting } from "$lib/store";
  import { fade } from "svelte/transition";

  // Album/artist/playlist radio builds its first batch on the request, and on a
  // fresh library that includes analyzing the seed audio. Held back briefly so
  // an already-analyzed seed (the common case) never flashes a toast.
  let visible = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  $: {
    if (timer) clearTimeout(timer);
    if ($radioStarting) {
      timer = setTimeout(() => (visible = true), 250);
    } else {
      visible = false;
    }
  }
</script>

{#if visible}
  <div
    class="fixed top-4 left-1/2 z-1000 -translate-x-1/2"
    transition:fade={{ duration: 120 }}
  >
    <div
      class="flex items-center gap-2.5 rounded-full border border-white/10 bg-zinc-800/95 px-4 py-2 text-sm font-medium text-zinc-200 shadow-lg backdrop-blur"
    >
      <IconLoader2 size={16} class="animate-spin text-zinc-400" />
      Starting radio...
    </div>
  </div>
{/if}
