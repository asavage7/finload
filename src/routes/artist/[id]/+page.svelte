<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state"; // In Svelte 5, this gives us URL parameters
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import MediaCard from "$lib/components/MediaCard.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import {
        IconPlayerPlayFilled,
        IconArrowsShuffle,
        IconMenu2Filled,
        IconArrowBack,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";

    // 1. Grab the dynamic [id] out of the current URL path
    const artistId = page.params.id;

    // 2. Define state variables for data holding
    let artistData: any = null;
    let duration_ms = 0;
    let tracks: any[] = [];
    let discs: any[] = [];
    let isLoading = true;

    onMount(async () => {
        try {
            // 3. Fetch artist details from our Python backend
            const res = await fetch(apiUrl(`/api/artist/${artistId}`));
            const data = await res.json();
            console.log("Artist data:", data), // Debug log to check artist data
                console.log(data.artist.id);

            artistData = {
                id: data.artist.id,
                name: data.artist.name,
                albums_count: data.artist.albums_count,
                tracks_count: data.artist.tracks_count,
                total_duration_ms: data.artist.total_duration_ms,
                albums: data.albums.map((album: any) => ({
                    id: album.id,
                    title: album.title,
                    release_year: album.release_year,
                    artist_name: data.artist.name, // Add artist name for easier access
                })),
            };

            // Fetch accent colors for dynamic theming (optional)
            const colorRes = await fetch(
                apiUrl(`/api/artist/${artistId}/accent-colors`),
            );
            const colors = await colorRes.json();
            artistData.accent_colors = colors;
            artistData = artistData;
        } catch (error) {
            console.error("Failed to load artist details:", error);
        } finally {
            isLoading = false;
        }
    });

    // 4. Playback handlers to communicate with Python MPV
    // async function playAlbum() {
    //     // Challenge logic: Tell backend to clear current queue and play this whole album list
    //     await fetch(apiUrl(`/api/playback/play_album/${albumId}`), {
    //         method: "POST",
    //     });
    // }

    // async function playTrack(trackId: string) {
    //     await fetch(
    //         apiUrl(`/api/playback/play_album/${albumId}?track_id=${trackId}`),
    //         { method: "POST" },
    //     );
    // }
</script>

{#if isLoading}
    <Loading />
{:else if artistData}
    <ViewLayout>
        <header
            slot="header"
            class="relative w-full flex items-end p-8 bg-gradient-to-b from-zinc-800/40 to-zinc-950 pt-18"
        >
            <img
                src={apiUrl(`/api/image/${artistData.id}?size=400`)}
                alt=""
                class="absolute inset-0 w-full h-full object-cover blur-3xl opacity-20 scale-110 pointer-events-none"
            />

            <button
                on:click={() => history.back()}
                class="absolute top-4 left-4 p-2 rounded-full bg-transparent text-zinc-400 hover:text-white hover:bg-white/10 cursor-pointer transition"
            >
                <div class="flex items-center gap-2 px-2">
                    <IconArrowBack size={16} />
                    Back
                </div>
            </button>

            <div
                class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-6xl mx-auto"
            >
                <img
                    src={apiUrl(`/api/image/${artistData.id}?size=400`)}
                    alt={artistData.name}
                    class="w-55 h-55 object-cover rounded-xl shadow-2xl border border-white/10 bg-zinc-900"
                />

                <div class="flex-1 text-center md:text-left space-y-2">
                    <span
                        class="text-xs uppercase font-black tracking-widest"
                        style="color: {artistData.accent_colors[1]}"
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
                </div>

                <div class="md:absolute right-0 flex items-center gap-4">
                    <button
                        on:click={playAlbum}
                        class="px-6 py-2 rounded-full text-white font-bold transition border border-white/10"
                        style="background-color: {artistData.accent_colors[0]}"
                    >
                        <div class="flex items-center gap-2">
                            <IconPlayerPlayFilled size={16} />
                            Play
                        </div>
                    </button>
                    <button
                        class="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 transition"
                    >
                        <IconArrowsShuffle size={16} />
                    </button>
                    <button
                        class="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 transition"
                    >
                        <IconMenu2Filled size={16} />
                    </button>
                </div>
            </div>
        </header>

        <div
            slot="content"
            class="text-zinc-400 w-full max-w-6xl mx-auto pb-28"
        >
            {#each discs as disc}
                {#if showDiscLabels}
                    <div
                        class="flex justify-between items-center text-zinc-400 font-bold py-2 px-4 md:px-4 md:pt-4 md:pb-0 sticky top-0 border-b md:border-none border-white/10 z-10"
                    >
                        <div>Disc {disc.disc_number}</div>
                        <div>
                            <button
                                on:click={() => history.back()}
                                class="p-2 rounded-full text-white border border-white/10 cursor-pointer transition"
                                style="background-color: {artistData
                                    .accent_colors[0]}"
                            >
                                <IconPlayerPlayFilled size={16} />
                            </button>
                            <button
                                on:click={() => history.back()}
                                class="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 cursor-pointer transition"
                            >
                                <IconArrowsShuffle size={16} />
                            </button>
                            <button
                                on:click={() => history.back()}
                                class="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 cursor-pointer transition"
                            >
                                <IconMenu2Filled size={16} />
                            </button>
                        </div>
                    </div>
                {/if}

                <div class="mb-8 px-0 mt-0 md:mt-4">
                    {#each disc.tracks as track, index}
                        <!-- svelte-ignore a11y_no_static_element_interactions -->
                        <!-- svelte-ignore a11y_click_events_have_key_events -->
                        <div
                            on:click={() => playTrack(track.id)}
                            class="flex items-center px-4 py-2.5 hover:bg-white/5 group transition duration-200 gap-4 border-b border-white/10 first:border-t cursor-pointer"
                        >
                            <div
                                class="w-6 h-6 flex items-center justify-center relative"
                            >
                                <div
                                    class="absolute -inset-0 flex items-center justify-center opacity-100 group-hover:opacity-0 transition-opacity duration-200 text-xs"
                                    style="color: {artistData.accent_colors[1]}"
                                >
                                    {track.track_number || index + 1}
                                </div>
                                <IconPlayerPlayFilled
                                    size={20}
                                    class="absolute opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                                    style="color: {artistData.accent_colors[1]}"
                                />
                            </div>

                            <div
                                class="flex flex-1 flex-col min-w-0 h-[36px] items-left justify-center"
                            >
                                <p class="text-white text-sm truncate">
                                    {track.title}
                                </p>
                                {#if track.artist_name !== albumData.artist_name}
                                    <p class="text-zinc-400 text-xs truncate">
                                        {track.artist_name}
                                    </p>
                                {/if}
                            </div>

                            <div class="flex gap-4 justify-end items-center">
                                <div
                                    class="time-text text-xs right text-zinc-400 text-sm"
                                >
                                    {formatTime(track.duration_ms, true)}
                                </div>
                                <button
                                    on:click={() => playTrack(track.id)}
                                    class="p-2 rounded-full hover:bg-white/10 transition text-zinc-400 hover:text-white"
                                >
                                    <IconMenu2Filled size={16} />
                                </button>
                            </div>
                        </div>
                    {/each}
                </div>
            {/each}
            <h3 class="text-xl font-bold text-white mb-2 mx-4">Albums</h3>
            <div
                class="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4 mx-4"
            >
                {#each artistData.albums as item}
                    <MediaCard
                        id={item.id}
                        title={item.title}
                        subtitle={item.release_year}
                        imageUrl={apiUrl(
                            `/api/image/${item.id}?size=400&type=album`,
                        )}
                        type="album"
                    />
                {/each}
            </div>
        </div>
    </ViewLayout>
{:else}
    <div class="p-8 text-red-400">Album context not found.</div>
{/if}
