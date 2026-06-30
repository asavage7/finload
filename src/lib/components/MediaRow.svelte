<script lang="ts">
    import { IconPlayerPlayFilled } from "@tabler/icons-svelte";
    import Rating from "./Rating.svelte";
    import CoverImage from "./CoverImage.svelte";
    import { getItemHref } from "$lib/utils/media";
    import { playAlbum, playTrackById } from "$lib/utils/playback";

    export let id: string;
    export let album_id: string = "";
    export let title: string;
    export let subtitle: string = "";
    export let imageUrl: string = "";
    export let duration: string = "";
    export let type: "artist" | "album" | "playlist" | "track" = "artist";
    export let rating: number = 0;

    // Mount the hover play-button overlay only after first hover (cheaper rows).
    let hovered = false;

    function playItem() {
        if (type === "album") playAlbum(id);
        else if (type === "track") playTrackById(id);
    }
</script>

<a
    href={getItemHref(type, id, album_id)}
    on:mouseenter={() => (hovered = true)}
    class="group flex gap-4 p-1.5 pr-4 rounded-xl hover:bg-white/5 transition duration-300 cursor-pointer items-center min-w-0 w-full"
>
    <CoverImage
        src={imageUrl}
        alt={title}
        fallbackText={title}
        class="w-12 h-12 shrink-0 {type === 'artist' ? 'rounded-full' : 'rounded-md'}"
    >
        {#if type !== "artist" && hovered}
            <div
                class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition flex items-center justify-center"
            >
                <button
                    on:click|preventDefault|stopPropagation={playItem}
                    class="p-2 bg-blue-500 rounded-full flex items-center justify-center shadow-md border border-white/10 cursor-pointer"
                >
                    <IconPlayerPlayFilled size={16} />
                </button>
            </div>
        {/if}
    </CoverImage>

    <div class="text-left my-1 flex-grow min-w-0">
        <div class="font-bold truncate w-full text-sm text-white">
            {title || "Unknown"}
        </div>
        {#if subtitle}
            <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
        {/if}
    </div>
    <div class="flex items-center gap-1 shrink-0">
        <Rating {id} itemType={type === 'track' || type === 'album' ? type : ''} {rating} size={14} />
        {#if type !== "artist"}
            <span class="w-16 text-right text-xs text-zinc-500">{duration}</span>
        {/if}
    </div>
</a>
