<script lang="ts">
  import "../app.css";
  import FooterPlayer from "$lib/components/FooterPlayer.svelte";
  import QueuePanel from "$lib/components/right-panels/QueuePanel.svelte";
  import { IconLibraryFilled, IconPlaylistFilled } from "@tabler/icons-svelte";
  import { onMount } from "svelte";
  import { playerState } from "$lib/store";
  import { apiUrl, wsUrl } from "$lib/backend";

  onMount(() => {
    const ws = new WebSocket(wsUrl("/ws/playback"));
    ws.onmessage = (event) => {
      const incomingState = JSON.parse(event.data);

      playerState.update((currentState) => ({
        ...currentState,
        ...incomingState,
      }));
    };

    window.addEventListener("player-command", (event) => {
      const e = event as CustomEvent<{ action: string; value?: unknown }>;
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(e.detail));
      }
    });
  });

  let currentTrackId = "";

  $: if (currentTrackId != $playerState.current_track?.id) {
    currentTrackId = $playerState.current_track?.id?.toString() || "";
    fetchColors($playerState.current_track?.album_id);
  }

  async function fetchColors(albumId: string | number | null | undefined) {
    if (!albumId) return;
    try {
      const res = await fetch(apiUrl(`/api/album/${albumId}/accent-colors`));
      if (!res.ok) throw new Error("No colors found");

      const colors = await res.json();
      if (colors && colors.length > 0) {
        // Safely update ONLY accent_colors
        playerState.update((state) => ({
          ...state,
          accent_colors: [`${colors[0]}`, `${colors[1]}`],
        }));
      }
    } catch (e) {
      // Safely update fallback colors
      playerState.update((state) => ({
        ...state,
        accent_colors: ["rgb(59, 130, 246)", "rgb(255, 255, 255)"],
      }));
    }
  }
</script>

<div class="flex h-screen w-full bg-zinc-950 text-white overflow-hidden">
  <!-- <aside
    class="hidden xl:flex w-64 bg-zinc-900 border-r border-white/5 flex flex-col"
  >
    <div class="p-6 font-bold text-xl">Music Player</div>
    <nav class="flex-1 px-4 space-y-2">
      <a
        href="/"
        class="block px-4 py-2 rounded-lg bg-zinc-800 text-white font-bold"
      >
        <div class="flex items-center gap-3 text-sm">
          <IconLibraryFilled size={16} />
          Library
        </div>
      </a>
      <a
        href="/playlists"
        class="block px-4 py-2 rounded-lg text-zinc-400 hover:bg-zinc-800/50 hover:text-white transition"
      >
        <div class="flex items-center gap-3 text-sm">
          <IconPlaylistFilled size={16} />
          Playlists
        </div>
      </a>
    </nav>
  </aside> -->

  <div class="flex-1 flex relative overflow-hidden">
    <main class="flex-1 overflow-y-auto">
      <slot />
    </main>

    <div class="absolute bottom-4 left-4 right-4 z-1000">
      <FooterPlayer />
    </div>
  </div>

  <aside class="hidden xl:flex w-80 bg-zinc-900 border-l border-white/5">
    <QueuePanel />
  </aside>
</div>
