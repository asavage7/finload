<script lang="ts">
  import type { ComponentType } from "svelte";

  export let variant: "empty" | "not-found" | "error" = "empty";
  export let icon: ComponentType;
  export let title = "";
  export let message = "";
</script>

<div
  class="w-full py-16 flex flex-col items-center gap-4 text-center {variant ===
  'error'
    ? 'text-amber-300'
    : 'text-zinc-400'}"
>
  <svelte:component this={icon} size={24} />
  {#if title}<p class="text-md font-medium">{title}</p>{/if}
  {#if message}<p class="text-sm max-w-md">{message}</p>{/if}
</div>
