<script lang="ts">
  import { IconPlayerPlayFilled } from "@tabler/icons-svelte";
  import { apiUrl } from "$lib/backend";
  import { playerState } from "$lib/store";

  export let id: string;
  export let title: string;
  export let subtitle: string = ""; // Optional (e.g., Artist name for an album)
  export let imageUrl: string = "";
  export let type: "artist" | "album" | "playlist" = "artist"; // Determines the link
  export let subtitleLink: string = ""; // Optional URL for subtitle (e.g., artist page for an album)

  let accentColors: string[] = ["#ffffff","4654ad","#000000"]; // Default accent color
  let accentColorLoaded = false;
  let accentColorLoading = false;
  const subtitleClass = "text-xs text-zinc-400 truncate w-full hover:text-white hover:underline transition-all";

  let imageFailed = false;

  function itemHref() {
    return `/${type}/${id}`;
  }

  async function playItem() {
    if (type === "album") {
      await fetch(apiUrl(`/api/playback/play_album/${id}`), { method: "POST" });
    }
  }

  async function getAccentColor() {
    if (type !== "album" || accentColorLoaded || accentColorLoading) {
      return;
    }

    accentColorLoading = true;
    try {
      const response = await fetch(apiUrl(`/api/album/${id}/accent-colors`));
      if (!response.ok) {
        return;
      }

      const colors = await response.json();
      if (Array.isArray(colors)) {
        accentColors = colors;
        accentColorLoaded = true;
      }
    } catch (error) {
      console.error("Failed to load album accent colors:", error);
    } finally {
      accentColorLoading = false;
    }
  }
</script>

<a
  href={itemHref()}
  on:mouseenter|preventDefault|stopPropagation={getAccentColor}
  class="group flex flex-col gap-2 p-2 rounded-xl hover:bg-white/5 border border-white/0 hover:border-white/10 transition duration-300 cursor-pointer"
>
  <div
    class="relative w-full aspect-square overflow-hidden border border-white/5 bg-zinc-800 flex items-center justify-center {type ===
    'artist'
      ? 'rounded-full'
      : 'rounded-md'}"
  >
    {#if imageUrl && !imageFailed}
      <img
        src={imageUrl}
        alt={title}
        on:error={() => (imageFailed = true)}
        class="w-full h-full object-cover shadow-md"
      />
    {:else}
      <span class="text-4xl text-zinc-600 font-bold shadow-md">
        {title.charAt(0).toUpperCase()}
      </span>
    {/if}

    {#if type !== "artist"}
      <div
        class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition flex items-center justify-center"
      >
        <button
          on:click|preventDefault|stopPropagation={playItem}
          class="p-3 text-white rounded-full flex items-center justify-center shadow-md border border-white/10 cursor-pointer" style="background-color: {accentColors[0]};"
        >
          <IconPlayerPlayFilled size={24} />
        </button>
      </div>
    {/if}
  </div>

  <div class="text-left my-1">
    <div class="font-bold truncate w-full text-sm text-white">
      {title || "Unknown"}
    </div>
    {#if subtitle}
      <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
    {/if}
  </div>
</a>
