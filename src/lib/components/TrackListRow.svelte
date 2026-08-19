<script lang="ts">
  import Rating from "$lib/components/Rating.svelte";
  import ContextMenu from "$lib/components/ContextMenu.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import CoverImage from "$lib/components/CoverImage.svelte";
  import { IconPlayerPlayFilled, IconMenu2Filled } from "@tabler/icons-svelte";
  import { formatTime } from "$lib/utils/formatTime";

  // One track row in a detail-page list (album, playlist, and future
  // collection views). The leading cell shows either a track number or a
  // cover image; hovering swaps it for a play icon.
  export let trackId: string;
  export let title: string;
  export let subtitle: string = "";
  export let number: number | null = null;
  export let imageSrc: string = "";
  export let rating: number = 0;
  export let durationMs: number = 0;
  export let menuItems: any[] = [];
  export let onPlay: () => void;
  // Scrolls into view and plays the highlight flash once (used by ?track= links).
  export let focused: boolean = false;

  function scrollIntoViewIfFocused(node: HTMLElement, isFocused: boolean) {
    if (isFocused) node.scrollIntoView({ behavior: "smooth", block: "center" });
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  use:scrollIntoViewIfFocused={focused}
  on:click={onPlay}
  class:track-flash={focused}
  class="flex items-center px-4 p-2 md:pr-2 group transition duration-200 gap-4 cursor-pointer overflow-hidden md:rounded-full min-w-0 hover:bg-white/5"
>
  <div
    class="{imageSrc
      ? 'w-9 h-9'
      : 'w-6 h-6'} flex-shrink-0 flex items-center justify-center relative"
  >
    {#if imageSrc}
      <CoverImage
        src={imageSrc}
        class="absolute inset-0 w-full h-full object-cover rounded-sm group-hover:brightness-50 transition duration-200"
        fallbackText={title}
      />
    {:else}
      <div
        class="absolute inset-0 flex items-center justify-center opacity-100 group-hover:opacity-0 transition-opacity duration-200 text-xs"
        style="color: var(--accent-light)"
      >
        {number}
      </div>
    {/if}
    <IconPlayerPlayFilled
      size={20}
      class="absolute inset-0 m-auto opacity-0 group-hover:opacity-100 transition-opacity duration-200 {imageSrc
        ? 'text-white'
        : ''}"
      style={imageSrc ? "" : "color: var(--accent-light)"}
    />
  </div>

  <div class="flex grow min-w-0 flex-col overflow-hidden h-[36px] justify-center">
    <p class="text-white text-sm truncate min-w-0">{title}</p>
    {#if subtitle}
      <p class="text-zinc-400 text-xs truncate min-w-0">{subtitle}</p>
    {/if}
  </div>

  <div class="flex-shrink-0 flex gap-4 justify-end items-center">
  <div class="hidden md:flex items-center gap-4">
    <Rating
      id={trackId}
      itemType="track"
      {rating}
      size={12}
      rated_color="var(--accent-light)"
    />
    <div class="ml-4 text-xs text-zinc-400">
      {formatTime(durationMs, true)}
    </div>
    </div>
    <ContextMenu items={menuItems} let:toggle>
      <IconButton on:click={(e) => toggle(e)} aria-label="Track options" class="text-white">
        <IconMenu2Filled size={16} />
      </IconButton>
    </ContextMenu>
  </div>
</div>

<style>
  .track-flash {
    animation: track-flash 3s ease-out;
  }
  @keyframes track-flash {
    0%,
    70% {
      background-color: rgba(255, 255, 255, 0.05);
    }
    100% {
      background-color: transparent;
    }
  }
</style>
