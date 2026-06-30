<script lang="ts">
  import { playerState, queuePanelActive } from "$lib/store";
  import CoverImage from "$lib/components/CoverImage.svelte";
  import Rating from "$lib/components/Rating.svelte";
  import RightPanel from "$lib/components/panels/RightPanel.svelte";
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import { getImageUrl } from "$lib/utils/media";
  import { slide } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
</script>

<div
  class="relative isolate flex h-full w-full overflow-hidden p-12 text-white select-none"
  style="background-color: var(--player-accent-dark);"
>
  <BackButton class="absolute top-4 left-4" />
  <img
    src={getImageUrl($playerState.current_track?.album_id || "default", 800)}
    alt=""
    class="absolute inset-0 w-full object-cover blur-3xl opacity-25 scale-110 pointer-events-none z-0"
  />
  <div
    class="flex-1 flex flex-col text-center items-center pb-24 pt-4 z-10 gap-2"
  >
    <CoverImage
      src={getImageUrl($playerState.current_track?.album_id || "default", 1000)}
      alt="{$playerState.current_track?.title ||
        'No Track Playing'} by {$playerState.current_track?.artist_name ||
        'Unknown Artist'}"
      fallbackText={$playerState.current_track?.title || "No Track Playing"}
      class="aspect-square h-[75%] my-auto object-cover rounded-2xl shadow-2xl border border-white/10 bg-zinc-700 mb-8"
    />
    <div class="truncate text-3xl font-bold">
      {$playerState.current_track?.title || "No Track Playing"}
    </div>
    <div class="truncate text-xl text-zinc-400 mb-4">
      {$playerState.current_track?.artist_name || "Unknown Artist"} ∙ {$playerState
        .current_track?.album_name || "Unknown Album"}
    </div>
    {#if $playerState.current_track}
      <Rating
        id={String($playerState.current_track.id)}
        itemType="track"
        rating={$playerState.current_track.rating}
        rated_color="var(--player-accent-light)"
        onrate={(r) =>
          playerState.update((s) => ({
            ...s,
            current_track: s.current_track
              ? { ...s.current_track, rating: r }
              : null,
          }))}
      />
    {/if}
  </div>
  {#if $queuePanelActive}
    <div
      transition:slide={{ axis: "x", duration: 150, easing: cubicOut }}
      class="flex h-full pb-16 w-1/2 max-w-2xl mx-auto justify-center overflow-hidden"
    >
      <RightPanel />
    </div>
  {/if}
</div>
