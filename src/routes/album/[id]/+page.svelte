<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state"; // In Svelte 5, this gives us URL parameters
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import Rating from "$lib/components/Rating.svelte";
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
    const albumId = page.params.id;

    // 2. Define state variables for data holding
    let albumData: any = null;
    let duration_ms = 0;
    let tracks: any[] = [];
    let discs: any[] = [];
    let isLoading = true;

    onMount(async () => {
        try {
            // 3. Fetch album context + track list from our Python backend
            const res = await fetch(apiUrl(`/api/album/${albumId}`));
            const data = await res.json();

            albumData = data.album;
            tracks = [].concat(...data.discs.map((disc: any) => disc.tracks));
            discs = data.discs;

            // Fetch accent colors for dynamic theming (optional)
            const colorRes = await fetch(
                apiUrl(`/api/album/${albumId}/accent-colors`),
            );
            const colors = await colorRes.json();
            albumData.accent_colors = colors;
            albumData = albumData;
        } catch (error) {
            console.error("Failed to load album details:", error);
        } finally {
            isLoading = false;
        }
    });

    // 4. Playback handlers to communicate with Python MPV
    async function playAlbum() {
        // Challenge logic: Tell backend to clear current queue and play this whole album list
        await fetch(apiUrl(`/api/playback/play_album/${albumId}`), {
            method: "POST",
        });
    }

    async function playTrack(trackId: string) {
        await fetch(
            apiUrl(`/api/playback/play_album/${albumId}?track_id=${trackId}`),
            { method: "POST" },
        );
    }

    $: showDiscLabels =
        discs.length > 1 || (discs.length === 1 && discs[0]?.disc_number !== 1);
</script>

{#if isLoading}
    <Loading />
{:else if albumData}
    <ViewLayout>
        <header
            slot="header"
            class="relative w-full flex items-end p-8 bg-gradient-to-b from-zinc-800/40 to-zinc-950 pt-18"
        >
            <img
                src={apiUrl(`/api/image/${albumData.id}?size=800`)}
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
                    src={apiUrl(`/api/image/${albumData.id}?size=400`)}
                    alt={albumData.title}
                    class="w-55 h-55 object-cover rounded-xl shadow-2xl border border-white/10 bg-zinc-900"
                />

                <div class="flex-1 text-center md:text-left space-y-2">
                    <span
                        class="text-xs uppercase font-black tracking-widest"
                        style="color: {albumData.accent_colors[1]}">ALBUM</span
                    >
                    <h1
                        class="text-2xl md:text-5xl font-black text-white line-clamp-2 mb-0 pb-1"
                    >
                        {albumData.title}
                    </h1>
                    <h3 class="text-md md:text-lg font-semibold text-zinc-400">
                        {albumData.artist_name}
                    </h3>
                    <Rating
                        rating={3}
                        rated_color={albumData.accent_colors[1]}
                        size={16}
                    ></Rating>
                    <div
                        class="flex flex-wrap items-center justify-center md:justify-start gap-2 text-sm text-zinc-400 font-medium"
                    >
                        <span>{albumData.release_year}</span>
                        <span>∙</span>
                        <span>{tracks.length} tracks</span>
                        <span>∙</span>
                        <span>{formatTime(tracks.reduce((acc: number, track: any) => acc + track.duration_ms, 0),true)}</span>
                    </div>
                </div>

                <div class="md:absolute right-0 flex items-center gap-4">
                    <button
                        on:click={playAlbum}
                        class="px-6 py-2 rounded-full text-white font-bold transition border border-white/10"
                        style="background-color: {albumData.accent_colors[0]}"
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

        <div slot="content" class="text-zinc-400 w-full max-w-6xl mx-auto pb-28">
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
                                style="background-color: {albumData
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
                                    style="color: {albumData.accent_colors[1]}"
                                >
                                    {track.track_number || index + 1}
                                </div>
                                <IconPlayerPlayFilled
                                    size={20}
                                    class="absolute opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                                    style="color: {albumData.accent_colors[1]}"
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
                                <div class="time-text text-xs right text-zinc-400 text-sm">
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
        </div>
    </ViewLayout>
{:else}
    <div class="p-8 text-red-400">Album context not found.</div>
{/if}
