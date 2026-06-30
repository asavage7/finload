<script lang="ts">
  import ProgressBar from "./ProgressBar.svelte";
  import Rating from "$lib/components/Rating.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import {
    IconPlayerPlayFilled,
    IconPlayerPauseFilled,
    IconPlayerTrackPrevFilled,
    IconPlayerTrackNextFilled,
    IconArrowsShuffle,
    IconRepeat,
    IconRepeatOnce,
    IconPlaylistFilled,
    IconPictureInPictureFilled,
    IconSettings,
    IconMenu2,
    IconDisc,
    IconMicrophone,
    IconPlaylistAdd,
    IconVolume,
    IconVolume2,
    IconVolumeOff,
  } from "@tabler/icons-svelte";

  import {
    playerState,
    queuePanelActive,
    playlistPickerStore,
    type PlayerState,
  } from "$lib/store";
  import { formatTime } from "$lib/utils/formatTime";
  import { goto } from "$app/navigation";
  import { dispatch } from "$lib/utils/playback";
  import { getImageUrl } from "$lib/utils/media";
  import ContextMenu from "$lib/components/ContextMenu.svelte";
  import CoverImage from "$lib/components/CoverImage.svelte";

  let displayTimePos = 0;
  let _prevTrackId: string | number | null | undefined = undefined;
  let _prevStoredTimePos = -1;
  // Suppresses the time_pos update that fires in the same reactive tick as a
  // track change, which would apply the old track's stale position over the snap.
  let _skipNextTimePos = false;

  $: {
    const trackId = $playerState.current_track?.id ?? null;
    if (_prevTrackId !== undefined && trackId !== _prevTrackId) {
      displayTimePos = 0;
      _skipNextTimePos = true;
    }
    _prevTrackId = trackId;
  }

  $: {
    const incoming = $playerState.time_pos;
    if (_skipNextTimePos) {
      _skipNextTimePos = false;
      _prevStoredTimePos = incoming;
    } else if (incoming !== _prevStoredTimePos) {
      _prevStoredTimePos = incoming;
      displayTimePos = incoming;
    }
  }

  $: moreMenuItems = [
    {
      label: "Add to Playlist",
      icon: IconPlaylistAdd,
      action: () => {
        const id = $playerState.current_track?.id;
        if (id) playlistPickerStore.set({ open: true, trackIds: [String(id)] });
      },
      enabled: !!$playerState.current_track,
    },
    { divider: true },
    {
      label: "View Album",
      icon: IconDisc,
      action: () => {
        const id = $playerState.current_track?.album_id;
        if (id) goto(`/album/${id}`);
      },
      enabled: !!$playerState.current_track?.album_id,
    },
    {
      label: "View Artist",
      icon: IconMicrophone,
      action: () => {
        const id = $playerState.current_track?.artist_id;
        if (id) goto(`/artist/${id}`);
      },
      enabled: !!$playerState.current_track?.artist_id,
    },
  ];

  function toggleQueuePanel() {
    queuePanelActive.update((active) => !active);
  }

  function togglePause() {
    playerState.update(s => ({ ...s, is_paused: !s.is_paused }));
    dispatch("toggle_pause");
  }
  function skipNext() {
    dispatch("skip_next");
  }
  function skipPrev() {
    dispatch("skip_prev");
  }
  function seek(position: number) {
    playerState.update(s => ({ ...s, time_pos: position }));
    dispatch("seek", position);
  }
  function cycleRepeat() {
    const next = (($playerState.repeat_mode + 1) % 3) as PlayerState["repeat_mode"];
    playerState.update(s => ({ ...s, repeat_mode: next }));
    dispatch("set_repeat", next);
  }
  function toggleShuffle() {
    const next = !$playerState.shuffle;
    playerState.update(s => ({ ...s, shuffle: next }));
    dispatch("set_shuffle", next);
  }

  let volumeOpen = false;
  let volumeButtonEl: HTMLButtonElement | undefined = undefined;
  let volumePopupEl: HTMLDivElement | null = null;
  let popupStyle = '';

  $: volume = $playerState.volume ?? 100;

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return { destroy() { node.remove(); } };
  }

  function toggleVolume() {
    if (volumeOpen) { volumeOpen = false; return; }
    if (volumeButtonEl) {
      const r = volumeButtonEl.getBoundingClientRect();
      popupStyle = `bottom:${window.innerHeight - r.top + 8}px; right:${window.innerWidth - r.right}px;`;
    }
    volumeOpen = true;
  }

  function onVolumeInput(e: Event) {
    const val = Number((e.target as HTMLInputElement).value);
    playerState.update(s => ({ ...s, volume: val }));
    dispatch("set_volume", val);
  }

  function handleOutsideClick(e: MouseEvent) {
    if (!volumeOpen) return;
    const t = e.target as Node;
    if (!volumeButtonEl?.contains(t) && !volumePopupEl?.contains(t)) {
      volumeOpen = false;
    }
  }

  function syncTrackRating(newRating: number) {
    playerState.update((s) => ({
      ...s,
      current_track: s.current_track ? { ...s.current_track, rating: newRating } : null,
    }));
  }
</script>

<svelte:document on:mousedown={handleOutsideClick} />

<div
  class="w-full max-w-6xl mx-auto h-full flex items-center justify-between bg-transparent p-1 text-white select-none h-20 border border-white/10 rounded-2xl shadow-lg flex items-center overflow-hidden relative isolate"
  style="background-image: linear-gradient(45deg, {$playerState
    .accent_colors[1]}25, {$playerState
    .accent_colors[0]}20); background-color: {$playerState.accent_colors[2]};"
>
  <img
    src={getImageUrl($playerState.current_track?.album_id || "default", 220)}
    alt=""
    class="absolute left-0 w-1/3 h-full object-cover blur-3xl opacity-25 scale-110 pointer-events-none"
  />
  <div class="flex items-center gap-3 w-1/3">
    <svelte:element
      this={$playerState.current_track ? 'a' : 'div'}
      href={$playerState.current_track ? '/now-playing' : undefined}
      class="relative flex items-center group"
    >
      {#if $playerState.current_track}
        <div
          class="absolute inset-0 rounded-xl opacity-0 group-hover:opacity-100 backdrop-blur-[4px] transition-opacity duration-200 z-10"
          style="background-color: {$playerState.accent_colors[2]}33; border: 1px solid {$playerState.accent_colors[1]}33;"
        >
          <IconPictureInPictureFilled
            size={24}
            class="text-white opacity-0 group-hover:opacity-100 transition-opacity duration-200 absolute inset-0 m-auto"
          />
        </div>
        <CoverImage
          src={getImageUrl($playerState.current_track.album_id || "default", 220)}
          alt="Current Track"
          fallbackText={$playerState.current_track.album_name}
          class="w-18 h-18 rounded-xl shadow-md"
          style="border-color: {$playerState.accent_colors[1]}33;"
        />
      {:else}
        <div class="w-18 h-18 rounded-xl border border-white/10 bg-neutral-800 shadow-md flex items-center justify-center">
          <IconDisc size={28} class="text-zinc-600" />
        </div>
      {/if}
    </svelte:element>
    <div class="flex flex-col gap-0 justify-center min-w-0 flex-1">
      <span class="text-sm mb-0.5 font-bold truncate">
        {$playerState.current_track?.title || "No Track Playing"}
      </span>
      <div class="text-xs mb-1.5 text-white/60 truncate z-10">
        <svelte:element
          this={$playerState.current_track?.artist_id ? 'a' : 'span'}
          href={$playerState.current_track?.artist_id ? `/artist/${$playerState.current_track.artist_id}` : undefined}
          class={$playerState.current_track?.artist_id ? 'hover:underline hover:text-white' : ''}
        >
          {$playerState.current_track?.artist_name || "Unknown Artist"}
        </svelte:element>
        ∙
        <svelte:element
          this={$playerState.current_track?.album_id ? 'a' : 'span'}
          href={$playerState.current_track?.album_id ? `/album/${$playerState.current_track.album_id}` : undefined}
          class={$playerState.current_track?.album_id ? 'hover:underline hover:text-white' : ''}
        >
          {$playerState.current_track?.album_name || "Unknown Album"}
        </svelte:element>
      </div>
      <Rating
        id={String($playerState.current_track?.id ?? '')}
        itemType="track"
        rating={$playerState.current_track?.rating ?? 0}
        rated_color="var(--player-accent-light)"
        size={12}
        onrate={syncTrackRating}
      />
    </div>
  </div>

  <div
    class="absolute left-1/2 -translate-x-1/2 flex flex-col justify-center items-center gap-2 w-9/16"
  >
    <div class="flex items-center gap-4 mt-1">
      <IconButton active={$playerState.shuffle} on:click={toggleShuffle}><IconArrowsShuffle size={16} /></IconButton>
      <IconButton white on:click={skipPrev}
        ><IconPlayerTrackPrevFilled size={16} /></IconButton
      >
      <button
        on:click={togglePause}
        class="p-2 rounded-full transition-all duration-250 border border-white/10 shadow-md hover:brightness-110 hover:shadow-lg"
        style="background-color: var(--player-accent);"
      >
        {#if $playerState.is_paused}
          <IconPlayerPlayFilled size={20} />
        {:else}
          <IconPlayerPauseFilled size={20} />
        {/if}
      </button>
      <IconButton white on:click={skipNext}
        ><IconPlayerTrackNextFilled size={16} /></IconButton
      >
      <IconButton active={$playerState.repeat_mode > 0} on:click={cycleRepeat}>
        {#if $playerState.repeat_mode === 2}
          <IconRepeatOnce size={16} />
        {:else}
          <IconRepeat size={16} />
        {/if}
      </IconButton>
    </div>

    <div class="flex items-center w-full justify-center gap-3 px-8">
      <span class="time-text w-8 text-right text-[11px] text-zinc-400"
        >{formatTime(displayTimePos)}</span
      >
      <ProgressBar
        value={displayTimePos}
        max={$playerState.duration}
        accentColor="var(--player-accent)"
        onSeek={seek}
      />
      <span class="time-text w-8 text-left text-[11px] text-zinc-400"
        >{formatTime($playerState.duration)}</span
      >
    </div>
  </div>

  <div class="flex items-center gap-2 justify-end w-7/32 pr-4">
    <IconButton white bind:el={volumeButtonEl} on:click={toggleVolume}>
      {#if volume === 0}
        <IconVolumeOff size={16} />
      {:else if volume < 50}
        <IconVolume2 size={16} />
      {:else}
        <IconVolume size={16} />
      {/if}
    </IconButton>

    {#if volumeOpen}
      <div
        use:portal
        bind:this={volumePopupEl}
        class="fixed bg-zinc-800 border border-white/10 rounded-full p-2 px-4 flex items-center gap-2 shadow-xl z-[9999] w-36"
        style={popupStyle}
      >
        <span class="text-xs text-zinc-400 tabular-nums">{volume}%</span>
        <div class="relative w-full flex items-center group p-2 select-none">
          <div class="absolute left-0 right-0 h-1 bg-white/10 rounded-full overflow-hidden pointer-events-none">
            <div class="h-full rounded-full bg-white" style="width: {volume}%;"></div>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            on:input={onVolumeInput}
            class="w-full h-1 opacity-0 cursor-pointer absolute inset-0 z-10"
          />
          <div
            class="absolute w-3 h-3 bg-white rounded-full shadow-md pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-20"
            style="left: calc({volume}% - 6px);"
          ></div>
        </div>
      </div>
    {/if}

    <IconButton white active={$queuePanelActive} on:click={() => toggleQueuePanel()}
      ><IconPlaylistFilled size={16} /></IconButton
    >
    <ContextMenu items={moreMenuItems} let:toggle>
      <IconButton white on:click={toggle}><IconMenu2 size={16} /></IconButton>
    </ContextMenu>
    <a
      href="/settings"
      class="p-2 rounded-full hover:bg-white/10 transition text-zinc-400 hover:text-white"
    >
      <IconSettings size={16} />
    </a>
  </div>
</div>
