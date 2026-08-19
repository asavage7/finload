<script lang="ts">
    import { getImageUrl } from "$lib/utils/media";
    import { apiUrl } from "$lib/backend";
    import { playlistCoverTimestamps } from "$lib/store";
    import { IconPlaylistFilled } from "@tabler/icons-svelte";
    import CoverImage from "$lib/components/CoverImage.svelte";

    export let playlistId: string = "";
    export let name: string = "";
    export let albumIds: string[] = [];
    export let size: number = 240;

    let mainFailed = false;
    let fetchedIds: string[] = [];

    let lastFetchedFor = "";
    $: if (!albumIds.length && playlistId !== lastFetchedFor) {
        lastFetchedFor = playlistId;
        fetch(apiUrl(`/api/playlist/${playlistId}/tracks`))
            .then((r) => (r.ok ? r.json() : []))
            .then((tracks: any[]) => {
                fetchedIds = [
                    ...new Set(
                        tracks.map((t: any) => t.album_id).filter(Boolean),
                    ),
                ] as string[];
            })
            .catch(() => {});
    }

    $: cacheBust = $playlistCoverTimestamps[playlistId] ?? 0;
    $: if (cacheBust) mainFailed = false;

    $: src =
        getImageUrl(playlistId, size, "playlist") +
        (cacheBust ? `&_ts=${cacheBust}` : "");
    $: ids = (albumIds.length ? albumIds : fetchedIds).filter(Boolean);
    $: grid = ids.length ? 
        ids.length === 2 
        ? [ids[0], ids[1], ids[1], ids[0]]
        : Array.from({ length: 4 }, (_, i) => ids[i % ids.length])
        : [];
    $: letterSize = Math.max(16, Math.round(size * 0.15));
</script>

<div
    class="w-full h-full overflow-hidden flex items-center justify-center bg-zinc-800"
>
    <CoverImage
        {src}
        alt={name}
        bind:failed={mainFailed}
        showPlaceholder={false}
        class="w-full h-full"
    >
        {#if mainFailed}
            {#if grid.length}
                <div class="grid grid-cols-2 absolute inset-0">
                    {#each grid as albumId}
                        <img
                            src={getImageUrl(albumId, size)}
                            alt=""
                            class="w-full h-full object-cover"
                        />
                    {/each}
                </div>
            {:else}
                <div
                    class="flex items-center justify-center w-full h-full text-zinc-600"
                >
                {#if name}
                    <span class="font-semibold" style="font-size: {letterSize}px;"
                        >{name[0]?.toUpperCase() ?? ""}</span
                    >
                {:else}
                    <IconPlaylistFilled size={letterSize * 1.5} />
                {/if}
                </div>
            {/if}
        {/if}
    </CoverImage>
</div>
