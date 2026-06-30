<script lang="ts">
  import { leftPanelReserve, rightPanelReserve } from "$lib/store";
  export let bgColor: string = '';
  // Contextual accent for the page being viewed: [accent, light, dark] hex.
  // Exposed to the subtree as var(--accent), var(--accent-light), var(--accent-dark).
  export let accent: string[] | null = null;

  $: accentVars = accent
    ? `--accent:${accent[0]}; --accent-light:${accent[1]}; --accent-dark:${accent[2]}; `
    : '';
</script>

<div
  class="w-full h-screen flex flex-col overflow-y-auto overflow-x-auto transition-[padding] duration-150 ease-out"
  class:bg-zinc-900={!bgColor}
  style="{bgColor ? `background-color: ${bgColor}; ` : ''}{accentVars}padding-left: {$leftPanelReserve}px; padding-right: {$rightPanelReserve}px"
>

  {#if $$slots.header}
    <div class="shrink-0">
      <slot name="header" />
    </div>
  {/if}

  {#if $$slots.toolbar}
    <div class="relative shrink-0 flex items-center justify-between w-full z-70">
      <slot name="toolbar" />
    </div>
  {/if}

  <div class="w-full flex-1 min-h-0">
    <slot name="content" />
  </div>

</div>
