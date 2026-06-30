<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import MediaCard from "$lib/components/MediaCard.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import {
        IconPlayerPlayFilled,
        IconArrowsShuffle,
        IconMenu2Filled,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";
    import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
    import { blendHex } from "$lib/utils/color";
    import { playArtist, buildCollectionMenuItems } from "$lib/utils/playback";
    import ContextMenu from "$lib/components/ContextMenu.svelte";

    const artistId = page.params.id!;

    let artistData: any = null;
    let isLoading = true;
    let fanartSrc = "";
    function getFanartUrl(id: string) {
        return apiUrl(`/api/image/${id}?type=artist&variant=fanart`);
    }

    onMount(async () => {
        const colorsPromise = fetchAccentColors("artist", artistId);
        try {
            const [res, tracksRes] = await Promise.all([
                fetch(apiUrl(`/api/artist/${artistId}`)),
                fetch(apiUrl(`/api/artist/${artistId}/tracks`)),
            ]);
            const [data, tracksData] = await Promise.all([res.json(), tracksRes.json()]);

            artistData = {
                id: data.artist.id,
                name: data.artist.name,
                bio: data.artist.bio ?? null,
                albums_count: data.artist.albums_count,
                tracks_count: data.artist.tracks_count,
                total_duration_ms: data.artist.total_duration_ms,
                albums: data.albums.map((album: any) => ({
                    id: album.id,
                    title: album.title,
                    release_year: album.release_year,
                    artist_name: data.artist.name,
                })),
                track_ids: tracksData.map((track: any) => track.id),
                accent_colors: ["#888888", "#888888", "#18181b"],
            };
            // Pre-load fanart; if it exists the header banner updates reactively.
            fanartSrc = getFanartUrl(artistId);
            isLoading = false;

            // If bio has never been fetched, request enrichment in the background.
            // The bio will appear on the next visit after enrichment completes.
            if (artistData.bio === null) {
                fetch(apiUrl(`/api/artist/${artistId}/enrich`), { method: "POST" }).catch(() => {});
            }

            const colors = await colorsPromise;
            if (colors.length > 0) {
                artistData.accent_colors = colors;
                artistData = artistData;
            }
        } catch (error) {
            console.error("Failed to load artist details:", error);
            isLoading = false;
        }
    });

    let bgLoaded = false;
    let fanartLoaded = false;
    let fanartFailed = false;
    let bioModalOpen = false;
</script>

{#if isLoading}
    <Loading />
{:else if artistData}
    {@const blendedBg = blendHex(artistData.accent_colors[2], "#18181b", 0.1)}
    <ViewLayout bgColor={blendedBg} accent={artistData.accent_colors}>
        <header
            slot="header"
            class="relative w-full flex items-end p-8 pt-18 mb-8"
        >
            <img
                src={getImageUrl(artistData.id, 220)}
                alt=""
                class="absolute inset-0 w-full h-full object-cover blur-3xl pointer-events-none transition-opacity duration-700"
                style="opacity: {bgLoaded ? '0.25' : '0'}"
                on:load={() => { bgLoaded = true; }}
            />

            <BackButton class="absolute top-4 left-4 z-20" />

            <div
                class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-6xl mx-auto"
            >
                <img
                    src={getImageUrl(artistData.id, 220)}
                    alt={artistData.name}
                    class="w-55 h-55 object-cover rounded-xl shadow-2xl border border-white/10 bg-zinc-800"
                />

                <div class="flex-1 text-center md:text-left space-y-2">
                    <span
                        class="text-xs uppercase font-black tracking-widest"
                        style="color: var(--accent-light)"
                        >ARTIST</span
                    >
                    <h1
                        class="text-2xl md:text-5xl font-black text-white line-clamp-2 mb-0 pb-1"
                    >
                        {artistData.name}
                    </h1>
                    <div
                        class="flex flex-wrap items-center justify-center md:justify-start gap-2 text-sm text-zinc-400 font-medium"
                    >
                        <span>{artistData.albums_count} albums</span>
                        <span>∙</span>
                        <span>{artistData.tracks_count} tracks</span>
                        <span>∙</span>
                        <span
                            >{formatTime(
                                artistData.total_duration_ms,
                                true,
                            )}</span
                        >
                    </div>
                    {#if artistData.bio}
                        <div class="md:mr-[240px]">
                            <p class="text-sm text-zinc-400 line-clamp-2">
                                {artistData.bio}
                            </p>
                            {#if artistData.bio.length > 200}
                                <button
                                    on:click={() => (bioModalOpen = true)}
                                    class="text-xs text-zinc-500 hover:text-zinc-300 mt-1 transition-colors"
                                >
                                    Read more
                                </button>
                            {/if}
                        </div>
                    {/if}
                </div>

                <div
                    class="md:absolute right-0 flex justify-center items-center gap-4"
                >
                    <button
                        on:click={() => playArtist(artistId, false)}
                        class="order-2 md:order-1 px-8 md:px-6 py-2 rounded-full text-white font-bold transition border border-white/10"
                        style="background-color: var(--accent)"
                    >
                        <div class="flex items-center gap-4">
                            <IconPlayerPlayFilled size={16} />
                            Play
                        </div>
                    </button>
                    <IconButton
                        on:click={() => playArtist(artistId, true)}
                        class="order-1 md:order-2"
                    >
                        <IconArrowsShuffle size={16} />
                    </IconButton>
                    <ContextMenu
                        items={buildCollectionMenuItems(artistData.track_ids)}
                        let:toggle
                    >
                        <IconButton on:click={toggle} class="order-3">
                            <IconMenu2Filled size={16} />
                        </IconButton>
                    </ContextMenu>
                </div>
            </div>
        </header>

        <div
            slot="content"
            class="text-zinc-400 w-full max-w-6xl mx-auto pb-28"
        >
            <h3 class="text-xl font-bold text-white mb-2 mx-4">Albums</h3>
            <div
                class="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2 mx-4"
            >
                {#each artistData.albums as item}
                    <MediaCard
                        id={item.id}
                        title={item.title}
                        subtitle={item.release_year}
                        imageUrl={getImageUrl(item.id, 220, "album")}
                        type="album"
                        rating={item.rating}
                    />
                {/each}
            </div>
        </div>
    </ViewLayout>

    {#if bioModalOpen}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
            on:click={() => (bioModalOpen = false)}
        >
            <div
                class="bg-zinc-900 border border-white/10 rounded-2xl shadow-2xl p-6 w-full max-w-3xl mx-4 max-h-[80vh] flex flex-col"
                on:click|stopPropagation
            >
                <h2 class="text-lg font-bold text-white mb-3">
                    {artistData.name}
                </h2>
                <p
                    class="text-sm text-zinc-400 leading-relaxed overflow-y-auto"
                >
                    {artistData.bio}
                </p>
                <button
                    on:click={() => (bioModalOpen = false)}
                    class="mt-5 self-end px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
                >
                    Close
                </button>
            </div>
        </div>
    {/if}
{:else}
    <div class="p-8 text-red-400">Artist not found.</div>
{/if}
