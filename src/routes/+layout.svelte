<script lang="ts">
  import "../app.css";
  import FooterPlayer from "$lib/components/player/FooterPlayer.svelte";
  import QueuePanel from "$lib/components/player/QueuePanel.svelte";
  import PlaylistPicker from "$lib/components/PlaylistPicker.svelte";
  import ConfirmModal from "$lib/components/modals/ConfirmModal.svelte";
  import PlaylistCreationModal from "$lib/components/modals/PlaylistCreationModal.svelte";
  import {
    playlistEditStore,
    playerState,
    queuePanelActive,
    DEFAULT_ACCENT_COLORS,
  } from "$lib/store";
  import { slide, fade } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
  import { onDestroy, onMount } from "svelte";
  import { apiUrl, wsUrl } from "$lib/backend";
  import { updatePlayerState } from "$lib/utils/store";
  import { page } from "$app/stores";

  let ws: WebSocket | null = null;
  let currentFetchController: AbortController | null = null;

  // WebSocket reconnection state. The backend can take ~30s to come up (and may
  // restart), so the socket must reconnect instead of dying on first failure.
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectDelay = 1000;
  const maxReconnectDelay = 15000;
  let stopped = false;

  function connectSocket() {
    if (stopped) return;

    const socket = new WebSocket(wsUrl("/ws/playback"));
    ws = socket;

    socket.onopen = () => {
      reconnectDelay = 1000; // reset backoff once we have a live connection
    };

    socket.onmessage = (event) => {
      const incomingState = JSON.parse(event.data);
      updatePlayerState(incomingState);
    };

    socket.onclose = () => {
      if (ws === socket) ws = null;
      scheduleReconnect();
    };

    socket.onerror = () => {
      // onclose fires after an error and handles the retry; close to be safe.
      socket.close();
    };
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectSocket();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectDelay);
  }

  onMount(() => {
    const handlePlayerCommand = (event: Event) => {
      const e = event as CustomEvent<{ action: string; value?: unknown }>;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(e.detail));
      }
    };

    window.addEventListener("player-command", handlePlayerCommand);
    connectSocket();

    return () => {
      window.removeEventListener("player-command", handlePlayerCommand);
    };
  });

  onDestroy(() => {
    stopped = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    currentFetchController?.abort();
    currentFetchController = null;
    ws?.close();
    ws = null;
  });

  let currentTrackId = "";

  $: if (currentTrackId != $playerState.current_track?.id) {
    currentTrackId = $playerState.current_track?.id?.toString() || "";
    fetchColors($playerState.current_track?.album_id);
  }

  // Mirror the playing track's accent onto :root so the whole UI can reference
  // it via var(--player-accent*), independent of the per-page contextual accent.
  $: if (typeof document !== "undefined") {
    const [accent, light, dark] = $playerState.accent_colors;
    const root = document.documentElement.style;
    root.setProperty("--player-accent", accent);
    root.setProperty("--player-accent-light", light);
    root.setProperty("--player-accent-dark", dark);
  }

  async function fetchColors(albumId: string | number | null | undefined) {
    if (!albumId) {
      updatePlayerState({ accent_colors: DEFAULT_ACCENT_COLORS });
      return;
    }

    currentFetchController?.abort();
    const controller = new AbortController();
    currentFetchController = controller;

    try {
      const res = await fetch(apiUrl(`/api/album/${albumId}/accent-colors`), {
        signal: controller.signal,
      });
      if (!res.ok) throw new Error("No colors found");

      if (controller.signal.aborted) return;

      const colors = await res.json();
      if (controller.signal.aborted) return;

      if (colors && colors.length > 0) {
        updatePlayerState({
          accent_colors: [`${colors[0]}`, `${colors[1]}`, `${colors[2]}`],
        });
      }
    } catch (e) {
      if (controller.signal.aborted) return;
      updatePlayerState({ accent_colors: DEFAULT_ACCENT_COLORS });
    }
  }
</script>

<PlaylistPicker />
<ConfirmModal />
<PlaylistCreationModal
  edit={true}
  playlist={$playlistEditStore.playlist}
  open={$playlistEditStore.open}
  onCancel={() => playlistEditStore.set({ open: false, playlist: null })}
  onCreate={() => playlistEditStore.set({ open: false, playlist: null })}
/>

<div class="flex h-screen w-full bg-zinc-900 text-white overflow-hidden">
  <div class="flex-1 flex relative overflow-y-auto">
    {#key $page.url.pathname}
      <main class="flex-1 overflow-auto" in:fade={{ duration: 100 }}>
        <slot />
      </main>
    {/key}

    {#if $queuePanelActive && $page.url.pathname !== '/now-playing'}
      <div
        transition:slide={{ axis: "x", duration: 150, easing: cubicOut }}
        class="overflow-hidden z-10 p-2 absolute top-0 right-0 h-full w-80"
      >
        <div
          class="flex rounded-xl bg-white/5 border border-white/5 transition-all duration-500 h-full"
        >
          <QueuePanel />
        </div>
      </div>
    {/if}

    <div
      class="absolute bottom-4 left-4 z-1000 transition-[right] duration-150 ease-out"
      style="right: calc(1rem + {$queuePanelActive && $page.url.pathname !== '/now-playing' ? '320px' : '0px'})"
    >
      <FooterPlayer />
    </div>
  </div>
</div>
