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
    class="mx-auto flex flex-col text-center w-1/2 items-center pb-24 pt-4 z-10 gap-2"
  >
    <img
      src={apiUrl(
        `/api/image/${$playerState.current_track?.album_id || "default"}?size=2000`,
      )}
      alt="Current Track"
      class="aspect-square h-[75%] my-auto object-cover rounded-2xl shadow-2xl border border-white/10 bg-zinc-800 mb-8"
    />
    <div class="truncate text-3xl font-bold">
      {$playerState.current_track?.title || "No Track Playing"}
    </div>
    <div class="truncate text-xl text-zinc-400 mb-8">
      {$playerState.current_track?.artist_name || "Unknown Artist"} ∙ {$playerState
        .current_track?.album_name || "Unknown Album"}
    </div>
  </div>
  <div
    class="flex h-full w-1/2 max-w-2xl mx-auto justify-center overflow-hidden"
  >
    <QueuePanel />
  </div>
</div>
