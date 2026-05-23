<script lang="ts">
  import { playerState } from "$lib/store";
  import ProgressBar from "$lib/components/ProgressBar.svelte";
  import Rating from "$lib/components/Rating.svelte";
  import {
    IconPlayerPlayFilled,
    IconPlayerPauseFilled,
    IconPlayerTrackPrevFilled,
    IconPlayerTrackNextFilled,
    IconArrowsShuffle,
    IconRepeat,
    IconPlaylistFilled,
  } from "@tabler/icons-svelte";
  import { formatTime } from "$lib/utils/formatTime";
  import { apiUrl } from "$lib/backend";

  let currentTrackId = "";

  $: if (currentTrackId != $playerState.current_track?.id) {
    currentTrackId = $playerState.current_track?.id?.toString() || "";
  }

  function dispatch(action: string, value?: unknown) {
    window.dispatchEvent(
      new CustomEvent<{ action: string; value?: unknown }>("player-command", {
        detail: { action, value },
      }),
    );
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
  style="background-image: linear-gradient(45deg, {$playerState.accent_colors[0]}30, {$playerState.accent_colors[1]}10); background-color: #222224;"
>
  <div class="flex items-center gap-4 w-1/3">
    <img
      src={apiUrl(`/api/image/${$playerState.current_track?.album_id || "default"}?size=62`)}
      alt="Current Track"
      class="w-16 h-16 object-cover rounded-lg border border-white/10 bg-zinc-800"
    />
    <div class="flex flex-col gap-0 justify-center min-w-0 flex-1">
      <div class="text-sm mb-0.5 font-bold truncate">
        {$playerState.current_track?.title || "No Track Playing"}
      </div>
      <div class="text-xs mb-1.5 text-zinc-400 truncate">
        {$playerState.current_track?.artist_name || "Unknown Artist"} • {$playerState.current_track?.album_name || "Unknown Album"}
      </div>
      <Rating rating={$playerState.current_track?.rating || 0} size={12} max={5} />
    </div>
  </div>

  <div class="absolute left-1/2 -translate-x-1/2 flex flex-col justify-center items-center gap-2 w-9/16">
    <div class="flex items-center gap-4 mt-1">
      <button class="p-2 rounded-full text-zinc-400 hover:text-white bg-transparent hover:bg-white/10 transition">
        <IconArrowsShuffle size={16} />
      </button>
      <button on:click={skipPrev} class="p-2 rounded-full bg-transparent hover:bg-white/10 transition">
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
      <button on:click={skipNext} class="p-2 rounded-full bg-transparent hover:bg-white/10 transition">
        <IconPlayerTrackNextFilled size={16} />
      </button>
      <button class="p-2 rounded-full text-zinc-400 hover:text-white bg-transparent hover:bg-white/10 transition">
        <IconRepeat size={16} />
      </button>
    </div>

    <div class="flex items-center w-full justify-center gap-3 px-8">
      <span class="w-8 text-right text-[11px] text-zinc-400">{formatTime($playerState.time_pos)}</span>
      <ProgressBar
        value={$playerState.time_pos}
        max={$playerState.duration}
        accentColor={$playerState.accent_colors[0]}
        onSeek={seek}
      />
      <span class="w-8 text-left text-[11px] text-zinc-400">{formatTime($playerState.duration)}</span>
    </div>
  </div>

  <div class="flex items-center gap-4 justify-end w-7/32 pr-4">
    <button class="p-2 rounded-full bg-transparent hover:bg-white/10 transition">
      <IconPlaylistFilled size={16} />
    </button>
  </div>
</div>