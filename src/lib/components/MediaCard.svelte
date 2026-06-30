<script lang="ts">
  import { IconPlayerPlayFilled, IconMenu2Filled } from "@tabler/icons-svelte";
  import ContextMenu from "./ContextMenu.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import PlaylistCover from "./PlaylistCover.svelte";
  import CoverImage from "./CoverImage.svelte";
  import Rating from "./Rating.svelte";
  import { goto } from "$app/navigation";
  import { getItemHref, fetchAccentColors } from "$lib/utils/media";
  import { playItem, buildItemMenuItems } from "$lib/utils/playback";

  export let id: string;
  export let title: string;
  export let subtitle: string = "";
  export let subtitleHref: string = "";
  export let imageUrl: string = "";
  export let type: "artist" | "album" | "playlist" = "artist";
  export let coverAlbumIds: string[] = [];
  export let rating: number | undefined = undefined;

  let accentColors: string[] = ["rgba(255,255,255,0.1)", "#4654ad", "#000000"];
  let accentColorLoaded = false;
  let accentColorLoading = false;

  let hovered = false;

  function onEnter() {
    getAccentColor();
    hovered = true;
  }

  async function getAccentColor() {
    if ((type !== "album" && type !== "playlist") || accentColorLoaded || accentColorLoading) return;
    accentColorLoading = true;
    const colors = await fetchAccentColors(type, id);
    if (colors.length > 0) {
      accentColors = colors;
      accentColorLoaded = true;
    }
    accentColorLoading = false;
  }
</script>

<a
  href={getItemHref(type, id)}
  on:mouseenter|preventDefault|stopPropagation={onEnter}
  class="group flex flex-col gap-2 rounded-xl transition duration-300 cursor-pointer hover:bg-white/5 p-2"
>
  {#if type === "playlist"}
    <div class="relative w-full aspect-square overflow-hidden border border-white/5 bg-zinc-700 rounded-lg flex items-center justify-center">
      <PlaylistCover playlistId={id} name={title} albumIds={coverAlbumIds} />
      {#if hovered}
      <div
        class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition flex items-center justify-center"
      >
        <button
          on:click|preventDefault|stopPropagation={() => playItem(id, type)}
          class="p-3 text-white rounded-full flex items-center justify-center shadow-md border border-white/10 cursor-pointer backdrop-blur-xl transition-all"
          style="background-color: {accentColors[0]}; border-color: {accentColors[1]}33;"
        >
          <IconPlayerPlayFilled size={24} />
        </button>
        <div class="absolute bottom-2 w-full pointer-events-auto flex items-center justify-between px-2">
          <ContextMenu items={buildItemMenuItems(id, type)} let:toggle>
            <IconButton
              on:click={(e) => toggle(e)}
              class="text-white hover:backdrop-blur-xl hover:shadow-md"
            >
              <IconMenu2Filled size={16} />
            </IconButton>
          </ContextMenu>
        </div>
      </div>
      {/if}
    </div>
  {:else}
    <CoverImage
      src={imageUrl}
      alt={title}
      fallbackText={title}
      class="w-full aspect-square {type === 'artist' ? 'rounded-full' : 'rounded-lg'}"
    >
      {#if type !== "artist" && hovered}
        <div
          class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition flex items-center justify-center"
        >
          <button
            on:click|preventDefault|stopPropagation={() => playItem(id, type)}
            class="p-3 text-white rounded-full flex items-center justify-center shadow-md border border-white/10 cursor-pointer backdrop-blur-xl transition-all"
            style="background-color: {accentColors[0]}; border-color: {accentColors[1]}33;"
          >
            <IconPlayerPlayFilled size={24} />
          </button>
          <div class="absolute bottom-2 w-full pointer-events-auto flex items-center justify-between px-2">
            {#if type === 'album'}
              <Rating {id} itemType="album" {rating} size={16} rated_color={accentColors[1]} />
            {/if}
            <ContextMenu items={buildItemMenuItems(id, type)} let:toggle>
              <IconButton
                on:click={(e) => toggle(e)}
                class="text-white hover:backdrop-blur-xl hover:shadow-md"
              >
                <IconMenu2Filled size={16} />
              </IconButton>
            </ContextMenu>
          </div>
        </div>
      {/if}
    </CoverImage>
  {/if}

  <div class="my-1 mx-2 {type === 'artist' ? 'text-center' : 'text-left'}">
    <div class="font-bold truncate w-full text-sm text-white">
      {title || "Unknown"}
    </div>
    {#if subtitle}
      {#if subtitleHref}
        <button
          on:click|preventDefault|stopPropagation={() => goto(subtitleHref)}
          class="text-xs text-zinc-400 hover:text-white hover:underline transition bg-none border-none flex items-start cursor-pointer"
        >
          <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
        </button>
      {:else}
        <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
      {/if}
    {/if}
  </div>
</a>
