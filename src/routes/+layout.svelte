<script lang="ts">
  import "../app.css";
  import FooterPlayer from "$lib/components/player/FooterPlayer.svelte";
  import RightPanel from "$lib/components/panels/RightPanel.svelte";
  import LeftPanel from "$lib/components/panels/LeftPanel.svelte";
  import SidePanel from "$lib/components/panels/SidePanel.svelte";
  import PlaylistPicker from "$lib/components/PlaylistPicker.svelte";
  import ConfirmModal from "$lib/components/modals/ConfirmModal.svelte";
  import PlaylistCreationModal from "$lib/components/modals/PlaylistCreationModal.svelte";
  import {
    playlistEditStore,
    playerState,
    queuePanelActive,
    leftPanelCondensed,
    leftPanelWidth,
    QUEUE_PANEL_WIDTH,
    windowWidth,
    leftPanelReserve,
    rightPanelReserve,
    panelsOverlay,
    DEFAULT_ACCENT_COLORS,
  } from "$lib/store";
  import { fade } from "svelte/transition";
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

    const handleResize = () => windowWidth.set(window.innerWidth);
    handleResize();

    window.addEventListener("player-command", handlePlayerCommand);
    window.addEventListener("resize", handleResize);
    connectSocket();

    return () => {
      window.removeEventListener("player-command", handlePlayerCommand);
      window.removeEventListener("resize", handleResize);
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

  // Now-playing is a full-screen view, so both edge panels step aside there
  // (the queue is shown inline within that page instead).
  $: isFullScreen = $page.url.pathname === "/now-playing";
  $: showQueue = $queuePanelActive && !isFullScreen;
  $: showLeft = !isFullScreen;

  // The left rail only floats once it's expanded; condensed it always sits beside
  // the content. The queue panel floats whenever it's open below the breakpoint.
  $: leftOverlay = $panelsOverlay && !$leftPanelCondensed;
  $: rightOverlay = $panelsOverlay && showQueue;

  // The footer tracks reserved space only, so it stays put when a panel overlays.
  $: footerLeft = showLeft ? `${$leftPanelReserve}px` : "0px";
  $: footerRight = `${$rightPanelReserve}px`;
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
  <div class="flex-1 flex relative overflow-hidden">
    {#key $page.url.pathname}
      <main class="flex-1 overflow-auto" in:fade={{ duration: 100 }}>
        <slot />
      </main>
    {/key}

    {#if showLeft}
      <SidePanel
        side="left"
        widthPx={$leftPanelWidth}
        duration={150}
        overlay={leftOverlay}
        onClose={() => leftPanelCondensed.set(true)}
      >
        <LeftPanel />
      </SidePanel>
    {/if}

    {#if showQueue}
      <SidePanel
        side="right"
        widthPx={QUEUE_PANEL_WIDTH}
        duration={150}
        overlay={rightOverlay}
        onClose={() => queuePanelActive.set(false)}
      >
        <RightPanel />
      </SidePanel>
    {/if}

    <div
      class="absolute bottom-4 z-1000 transition-[left,right] duration-150 ease-out"
      style="left: calc(1rem + {footerLeft}); right: calc(1rem + {footerRight})"
    >
      <FooterPlayer />
    </div>
  </div>
</div>
