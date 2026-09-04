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
  <div class="absolute inset-0 w-full opacity-20 pointer-events-none z-0">
    <CoverImage
      src={getImageUrl($playerState.current_track?.album_id || "default", 800)}
      alt=""
      showPlaceholder={false}
      class="w-full h-full scale-135 blur-3xl animate-spin-bg"
    />
  </div>
  <div
    class="w-full max-w-[1920px] mx-auto flex items-center justify-center h-full pb-24 pt-8 gap-16"
  >
    <div
      class="flex w-5/8 min-w-0 flex-1 flex-col items-center gap-2 text-center z-10"
      transition:slide={{ axis: "x", duration: 150, easing: cubicOut }}
    >
      <CoverImage
        src={getImageUrl(
          $playerState.current_track?.album_id || "default",
          1000,
        )}
        alt="{$playerState.current_track?.title ||
          'No Track Playing'} by {$playerState.current_track?.artist_name ||
          'Unknown Artist'}"
        fallbackText={$playerState.current_track?.title || "No Track Playing"}
        class="block w-full max-w-[min(calc(70vh-200px),100%)] aspect-square flex-none rounded-2xl shadow-2xl border border-white/10 bg-zinc-700 mb-8"
      />
      <div class="truncate text-3xl font-bold">
        {$playerState.current_track?.title || "No Track Playing"}
      </div>
      <div class="truncate text-xl text-zinc-400 mb-4">
        <a
          href={`/artist/${$playerState.current_track?.artist_id}`}
          class="hover:text-white hover:underline"
        >
          {$playerState.current_track?.artist_name || "Unknown Artist"}
        </a>
        ∙
        <a
          href={`/album/${$playerState.current_track?.album_id}`}
          class="hover:text-white hover:underline"
        >
          {$playerState.current_track?.album_name || "Unknown Album"}
        </a>
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
      <div class="flex h-full w-3/8 justify-center overflow-hidden py-16">
        <div
          transition:slide={{ axis: "x", duration: 150, easing: cubicOut }}
          class="flex h-full w-full max-w-xl mx-auto justify-center overflow-hidden"
        >
          <RightPanel />
        </div>
      </div>
    {/if}
  </div>
</div>
