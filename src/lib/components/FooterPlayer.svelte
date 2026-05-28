<script lang="ts">
  import ProgressBar from "./ProgressBar.svelte";
  import Rating from "./Rating.svelte";
  import {
    IconPlayerPlayFilled,
    IconPlayerPauseFilled,
    IconPlayerTrackPrevFilled,
    IconPlayerTrackNextFilled,
    IconArrowsShuffle,
    IconRepeat,
    IconPlaylistFilled,
    IconPictureInPictureFilled,
  } from "@tabler/icons-svelte";

  import { playerState, queuePanelActive } from "$lib/store";
  import { formatTime } from "$lib/utils/formatTime";
  import { apiUrl } from "$lib/backend";

  function dispatch(action: string, value?: unknown) {
    window.dispatchEvent(
      new CustomEvent<{ action: string; value?: unknown }>("player-command", {
        detail: { action, value },
      }),
    );
  }

  function toggleQueuePanel() {
    queuePanelActive.update((active) => !active);
  }

  function togglePause() {
    dispatch("toggle_pause");
  }
  function skipNext() {
    dispatch("skip_next");
  }
  function skipPrev() {
    dispatch("skip_prev");
  }
  function seek(position: number) {
    dispatch("seek", position);
  }
</script>

<div
  class="w-full max-w-6xl mx-auto h-full flex items-center justify-between bg-transparent p-2 text-white select-none h-20 border border-white/10 rounded-2xl shadow-lg flex items-center overflow-hidden relative isolate"
  style="background-image: linear-gradient(45deg, {$playerState
    .accent_colors[1]}25, {$playerState
    .accent_colors[0]}20); background-color: {$playerState.accent_colors[2]};"
>
  <img
    src={`http://127.0.0.1:8000/api/image/${$playerState.current_track?.album_id || "default"}?size=62`}
    alt=""
    class="absolute left-0 w-1/3 h-full object-cover blur-3xl opacity-25 scale-110 pointer-events-none"
  />
  <div class="flex items-center gap-4 w-1/3">
    <a class="relative flex items-center group" href={`/now-playing`}>
      <div
        class="absolute inset-0 bg-black/30 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity duration-200"
      >
        <IconPictureInPictureFilled
          size={24}
          class="text-white opacity-0 group-hover:opacity-100 transition-opacity duration-200 absolute inset-0 m-auto"
        />
      </div>
      <img
        src={apiUrl(
          `/api/image/${$playerState.current_track?.album_id || "default"}?size=62`,
        )}
        alt="Current Track"
        class="w-16 h-16 object-cover rounded-lg border border-white/10 bg-zinc-800"
      />
    </a>
    <div class="flex flex-col gap-0 justify-center min-w-0 flex-1">
      <span class="text-sm mb-0.5 font-bold truncate">
        {$playerState.current_track?.title || "No Track Playing"}
      </span>
      <div class="text-xs mb-1.5 text-zinc-400 truncate z-10">
        <a href={`/artist/${$playerState.current_track?.artist_id}`} class="hover:underline hover:text-white">
          {$playerState.current_track?.artist_name || "Unknown Artist"}
        </a>
        ∙
        <a href={`/album/${$playerState.current_track?.album_id}`} class="hover:underline hover:text-white">
          {$playerState.current_track?.album_name || "Unknown Album"}
        </a>
      </div>
      <Rating rating={$playerState.current_track?.rating || 0} size={12} />
    </div>
  </div>

  <div
    class="absolute left-1/2 -translate-x-1/2 flex flex-col justify-center items-center gap-2 w-9/16"
  >
    <div class="flex items-center gap-4 mt-1">
      <button
        class="p-2 rounded-full text-zinc-400 hover:text-white bg-transparent hover:bg-white/10 transition"
      >
        <IconArrowsShuffle size={16} />
      </button>
      <button
        on:click={skipPrev}
        class="p-2 rounded-full bg-transparent hover:bg-white/10 transition"
      >
        <IconPlayerTrackPrevFilled size={16} />
      </button>
      <button
        on:click={togglePause}
        class="p-2 rounded-full transition-all duration-500 border border-white/10"
        style="background-color: {$playerState.accent_colors[0]}"
      >
        {#if $playerState.is_paused}
          <IconPlayerPlayFilled size={20} />
        {:else}
          <IconPlayerPauseFilled size={20} />
        {/if}
      </button>
      <button
        on:click={skipNext}
        class="p-2 rounded-full bg-transparent hover:bg-white/10 transition"
      >
        <IconPlayerTrackNextFilled size={16} />
      </button>
      <button
        class="p-2 rounded-full text-zinc-400 hover:text-white bg-transparent hover:bg-white/10 transition"
      >
        <IconRepeat size={16} />
      </button>
    </div>

    <div class="flex items-center w-full justify-center gap-3 px-8">
      <span class="time-text w-8 text-right text-[11px] text-zinc-400"
        >{formatTime($playerState.time_pos)}</span
      >
      <ProgressBar
        value={$playerState.time_pos}
        max={$playerState.duration}
        accentColor={$playerState.accent_colors[0]}
        onSeek={seek}
      />
      <span class="time-text w-8 text-left text-[11px] text-zinc-400"
        >{formatTime($playerState.duration)}</span
      >
    </div>
  </div>

  <div class="flex items-center gap-4 justify-end w-7/32 pr-4">
    <button on:click={() => toggleQueuePanel()}
      class="p-2 rounded-full bg-transparent hover:bg-white/10 transition"
    >
      <IconPlaylistFilled size={16} />
    </button>
  </div>
</div>
