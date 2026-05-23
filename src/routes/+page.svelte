<script lang="ts">
  import { onMount } from 'svelte';
  import ViewLayout from '$lib/components/ViewLayout.svelte';
  import MediaCard from '$lib/components/MediaCard.svelte';
  import { apiUrl } from '$lib/backend';
  import { IconRefresh, IconLayoutGrid, IconLayoutList, IconAlbum, IconTrack, IconMicrophone, IconPlaylist, IconMicrophoneFilled, IconPlaylistFilled, IconDiscFilled} from '@tabler/icons-svelte';

  let tabs = ["Albums", "Tracks", "Artists", "Playlists"];
  let tabicons = [IconDiscFilled, IconTrack, IconMicrophoneFilled, IconPlaylistFilled];
  let activeTab = "Albums";
  let items: any[] = [];
  let loadError = "";

  function getCardType(tab: string): "artist" | "album" | "playlist" {
    if (tab === "Artists") return "artist";
    if (tab === "Playlists") return "playlist";
    return "album";
  }

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
      loadError = "Backend unavailable. Start the backend dev server and refresh.";
      console.error(`Failed to load ${tab.toLowerCase()}:`, error);
    }
  }

  onMount(() => {
    loadData(activeTab);
  });
</script>

<ViewLayout>
  <div slot="toolbar" class="flex items-center justify-between w-full bg-zinc-950 backdrop-blur-[512px] border-b border-white/10 p-4 z-7000">
    <div class="flex items-center">
      <button on:click={() => loadData(activeTab)} class="p-2 text-zinc-400 hover:text-white rounded-lg hover:bg-white/5 transition">
        <IconRefresh size={20} />
      </button>
    </div>

    <div class="flex gap-1 bg-zinc-900/80 p-1 rounded-full border border-white/5">
      {#each tabs as tab}
        <button 
          on:click={() => loadData(tab)}
          class="px-4 py-1.5 rounded-full text-sm font-semibold transition {activeTab === tab ? 'bg-zinc-800 text-white shadow-lg border border-white/10' : 'text-zinc-500 hover:text-white hover:bg-white/5'}"
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
      <button class="p-2 text-zinc-400 hover:text-white rounded-md"><IconLayoutGrid size={18} /></button>
      <button class="p-2 text-zinc-500 hover:text-white rounded-md"><IconLayoutList size={18} /></button>
    </div>
  </div>

  <div slot="content" class="grid px-4 gap-2 w-full h-full grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">
    {#if loadError}
      <div class="col-span-full px-2 pt-2 text-sm text-amber-300">
        {loadError}
      </div>
    {/if}

    {#each items as item}
          <MediaCard 
            id={item.id} 
            title={item.name || item.title} 
            subtitle={item.artist_name} 
            imageUrl={apiUrl(`/api/image/${item.id}?size=400`)}
            type={getCardType(activeTab)} 
          />
    {/each}
  </div>
</ViewLayout>