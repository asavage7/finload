<script lang="ts">
    import { IconPlayerPlayFilled } from "@tabler/icons-svelte";
    import Rating from "./Rating.svelte";
    import { apiUrl } from "$lib/backend";

    export let id: string;
    export let album_id: string = ""; // Only for tracks
    export let title: string;
    export let subtitle: string = ""; // Optional (e.g., Artist name for an album)
    export let imageUrl: string = "";
    export let duration: string = ""; // Optional, e.g., "3:45"
    export let type: "artist" | "album" | "playlist" | "track" = "artist"; // Determines the link

    let imageFailed = false;

    async function playItem() {
        if (type === "album") {
            await fetch(apiUrl(`/api/playback/play_album/${id}`), {
                method: "POST",
            });
        } else if (type === "track") {
            await fetch(apiUrl(`/api/playback/play_track/${id}`), {
                method: "POST",
            });
        }
    }
</script>

<a
    href={type === "track" ? `/album/${album_id}` : `/${type}/${id}`}
    class="group flex gap-4 p-2 pr-4 rounded-xl hover:bg-white/5 border border-white/0 hover:border-white/10 transition duration-300 cursor-pointer items-center"
>
    <div
        class="relative w-12 h-12 overflow-hidden border border-white/5 bg-zinc-800 flex shrink-0 items-center justify-center {type ===
        'artist'
            ? 'rounded-full'
            : 'rounded-md'}"
    >
        {#if imageUrl && !imageFailed}
            <img
                src={imageUrl}
                alt={title}
                on:error={() => (imageFailed = true)}
                class="w-12 h-12 shrink-0object-cover shadow-md lazyload rounded-md"
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
                    class="p-2 bg-blue-500 rounded-full flex items-center justify-center shadow-md border border-white/10 cursor-pointer"
                >
                    <IconPlayerPlayFilled size={16} />
                </button>
            </div>
        {/if}
    </div>

    <div class="text-left my-1 flex-grow min-w-0">
        <div class="font-bold truncate w-full text-sm text-white">
            {title || "Unknown"}
        </div>
        {#if subtitle}
            <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
        {/if}
    </div>
    <div class="flex items-center gap-1">
        <Rating rating={3} size={12} />
        {#if type !== "artist"}
            <span class="w-16 text-right text-xs text-zinc-500">{duration}</span
            >
        {/if}
    </div>
</a>
