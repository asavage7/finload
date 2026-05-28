<script lang="ts">
  import { playerState } from "$lib/store";
  import Rating from "$lib/components/Rating.svelte";
  import QueuePanel from "$lib/components/right-panels/QueuePanel.svelte";
  import { apiUrl } from "$lib/backend";
  import { IconArrowBack } from "@tabler/icons-svelte";
</script>

<div
  class="relative isolate flex h-full w-full overflow-hidden p-12 text-white select-none"
  style="background-color: {$playerState.accent_colors[2]};"
>
  <button
    on:click={() => history.back()}
    class="absolute top-4 left-4 p-2 rounded-full bg-transparent text-zinc-400 hover:text-white hover:bg-white/10 cursor-pointer transition"
  >
    <div class="flex items-center gap-2 px-2">
      <IconArrowBack size={16} />
      Back
    </div>
  </button>
  <img
    src={apiUrl(`/api/image/${$playerState.current_track?.album_id}?size=800`)}
    alt=""
    class="absolute inset-0 w-full object-cover blur-3xl opacity-25 scale-110 pointer-events-none z-0"
  />
  <div
    class="mx-auto flex h-full w-full max-w-[2000px] items-center justify-between gap-12 pb-24 pt-4 z-10"
  >
    <div
      class="flex h-full w-full flex-col max-h-[1000px] my-auto items-center justify-center p-4 gap-8"
    >
      <img
        src={apiUrl(
          `/api/image/${$playerState.current_track?.album_id || "default"}?size=2000`,
        )}
        alt="Current Track"
        class="aspect-square m-auto object-cover rounded-2xl shadow-2xl border border-white/10 bg-zinc-800"
      />
      <div
        class="flex w-full max-w-md flex-1 min-w-0 flex-col justify-center text-center"
      >
        <div class="truncate text-3xl font-bold">
          {$playerState.current_track?.title || "No Track Playing"}
        </div>
        <div class="truncate text-xl text-zinc-400">
          {$playerState.current_track?.artist_name || "Unknown Artist"} ∙ {$playerState.current_track?.album_name || "Unknown Album"}
        </div>
        <Rating rating={$playerState.current_track?.rating || 0} size={24} />
      </div>
    </div>
    <div
      class="flex h-full w-full max-w-2xl mx-auto justify-center max-h-[1000px] overflow-hidden"
    >
      <QueuePanel />
    </div>
  </div>
</div>
