<script lang="ts">
  import { onMount } from "svelte";
  import { createVirtualizer } from "@tanstack/svelte-virtual";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import MediaCard from "$lib/components/MediaCard.svelte";
  import MediaRow from "$lib/components/MediaRow.svelte";
  import { apiUrl } from "$lib/backend";
  import { formatTime } from "$lib/utils/formatTime";
  import {
    IconRefresh,
    IconLayoutGrid,
    IconLayoutList,
    IconTrack,
    IconMicrophoneFilled,
    IconPlaylistFilled,
    IconDiscFilled,
  } from "@tabler/icons-svelte";

  const tabs = ["Albums", "Tracks", "Artists", "Playlists"];
  const tabicons = [
    IconDiscFilled,
    IconTrack,
    IconMicrophoneFilled,
    IconPlaylistFilled,
  ];

  let activeTab = "Albums";
  let activeView: Record<string, string> = {
    Albums: "grid",
    Tracks: "list",
    Artists: "grid",
    Playlists: "grid",
  };
  let items: any[] = [];
  let loadError = "";
  let scrollContainer: HTMLDivElement | null = null;

  function getCardType(tab: string): "artist" | "album" | "playlist" | "track" {
    if (tab === "Artists") return "artist";
    if (tab === "Playlists") return "playlist";
    if (tab === "Tracks") return "track";
    return "album";
  }

  $: virtualizer =
    (scrollContainer,
    createVirtualizer({
      count: items.length,
      getScrollElement: () => scrollContainer,
      estimateSize: () => 70,
      overscan: 5,
    }));

  async function loadData(tab: string) {
    activeTab = tab;
    loadError = "";

    try {
      const res = await fetch(apiUrl(`/api/${tab.toLowerCase()}`));
      if (!res.ok) {
        throw new Error(`Request failed with status ${res.status}`);
      }

      items = await res.json();
    } catch (error) {
      items = [];
      loadError =
        "Backend unavailable. Start the backend dev server and refresh.";
      console.error(`Failed to load ${tab.toLowerCase()}:`, error);
    }
  }

  onMount(() => {
    loadData(activeTab);
  });
</script>

<ViewLayout>
  <div
    slot="toolbar"
    class="flex items-center justify-between w-full bg-zinc-950 border-b border-white/10 p-4 z-7000"
  >
    <div class="flex items-center">
      <button
        on:click={() => loadData(activeTab)}
        class="p-2 text-zinc-400 hover:text-white rounded-lg hover:bg-white/5 transition"
      >
        <IconRefresh size={20} />
      </button>
    </div>

    <div
      class="absolute left-1/2 -translate-x-1/2 flex items-center gap-1 bg-zinc-900/80 p-1 rounded-full border border-white/5"
    >
      {#each tabs as tab}
        <button
          on:click={() => loadData(tab)}
          class="px-4 py-1.5 rounded-full text-sm font-semibold transition border {activeTab ===
          tab
            ? 'bg-zinc-800 text-white shadow-lg border-white/10'
            : 'text-zinc-500 hover:text-white hover:bg-white/5 border-transparent'}"
        >
          <div class="flex items-center gap-2">
            {#if tabicons[tabs.indexOf(tab)]}
              <svelte:component this={tabicons[tabs.indexOf(tab)]} size={16} />
            {/if}
            {tab}
          </div>
        </button>
      {/each}
    </div>
    <div class="flex items-center gap-2">
      {#if activeTab !== "Tracks"}
        <button
          on:click={() => (activeView[activeTab] = "grid")}
          class="p-2 text-zinc-400 hover:text-white rounded-md"
        >
          <IconLayoutGrid size={18} />
        </button>
        <button
          on:click={() => (activeView[activeTab] = "list")}
          class="p-2 text-zinc-500 hover:text-white rounded-md"
        >
          <IconLayoutList size={18} />
        </button>
      {/if}
    </div>
  </div>

  <div slot="content" class="w-full h-full flex flex-col min-h-0">
    {#if loadError}
      <div class="px-4 pt-2 text-sm text-amber-300">{loadError}</div>
    {/if}

    {#if activeView[activeTab] === "grid"}
      <div
        class="px-4 pt-4 pb-28 overflow-y-auto grid gap-2 w-full grid-cols-[repeat(auto-fill,minmax(180px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(200px,1fr))]"
      >
        {#each items as item}
          <MediaCard
            id={item.id}
            title={item.name || item.title}
            subtitle={item.artist_name}
            imageUrl={apiUrl(`/api/image/${item.id}?size=400&type=${getCardType(activeTab)}`)}
            type={getCardType(activeTab)}
          />
        {/each}
      </div>
    {:else}
      <div class="flex-1 w-full min-h-0 flex flex-col">
        <div
          bind:this={scrollContainer}
          class="flex-1 w-full h-full overflow-y-auto px-4 pt-4 pb-28"
        >
          <div
            style="height: {$virtualizer.getTotalSize()}px; width: 100%; position: relative;"
          >
            {#each $virtualizer.getVirtualItems() as virtualRow (virtualRow.index)}
              {@const item = items[virtualRow.index]}

              <div
                style="position: absolute; top: 0; left: 0; width: 100%; transform: translateY({virtualRow.start}px);"
              >
                <div class="pb-1 h-[70px]">
                  <MediaRow
                    id={item.id}
                    album_id={item.album_id || ""}
                    title={item.name || item.title}
                    subtitle={item.artist_name}
                    imageUrl={apiUrl(`/api/image/${item.id}?size=48&type=${getCardType(activeTab)}`)}
                    type={getCardType(activeTab)}
                    duration={formatTime(item.duration_ms / 1000)}
                  />
                </div>
              </div>
            {/each}
          </div>
        </div>
      </div>
    {/if}
  </div>
</ViewLayout>
