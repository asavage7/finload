<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state"; // In Svelte 5, this gives us URL parameters
    import { afterNavigate } from "$app/navigation";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import Rating from "$lib/components/Rating.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import ContextMenu from "$lib/components/ContextMenu.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import {
        IconPlayerPlayFilled,
        IconArrowsShuffle,
        IconMenu2Filled,
        IconDisc,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";
    import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
    import { blendHex } from "$lib/utils/color";
    import {
        playAlbum,
        playAlbumAtTrack,
        buildTrackMenuItems,
        buildCollectionMenuItems,
    } from "$lib/utils/playback";

    const albumId = page.params.id!;

    let albumData: any = null;
    let tracks: any[] = [];
    let discs: any[] = [];
    let isLoading = true;
    let bgLoaded = false;

    onMount(async () => {
        const colorsPromise = fetchAccentColors("album", albumId);
        try {
            const res = await fetch(apiUrl(`/api/album/${albumId}`));
            const data = await res.json();

            albumData = data.album;
            albumData.accent_colors = ["#888888", "#888888", "#1c1c1f"];
            tracks = [].concat(...data.discs.map((disc: any) => disc.tracks));
            discs = data.discs;
            isLoading = false;

            const colors = await colorsPromise;
            if (colors.length > 0) {
                albumData.accent_colors = colors;
                albumData = albumData;
            }
        } catch (error) {
            console.error("Failed to load album details:", error);
            isLoading = false;
        }
    });

    $: showDiscLabels =
        discs.length > 1 || (discs.length === 1 && discs[0]?.disc_number !== 1);

    $: allTrackIds = tracks.map((t: any) => t.id);

    // Optional `?track=<id>` focus: the matching row scrolls into view and the
    // CSS `.track-flash` animation highlights it. Gated on navigation type so
    // back/forward (popstate) never replays it — only a fresh visit does.
    let focusTrackId: string | null = null;
    afterNavigate(({ type }) => {
        focusTrackId = type === "popstate" ? null : page.url.searchParams.get("track");
    });

    function scrollIntoViewIfFocused(node: HTMLElement, focused: boolean) {
        if (focused)
            node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
</script>

{#if isLoading}
    <Loading />
{:else if albumData}
    {@const blendedBg = blendHex(albumData.accent_colors[2], "#161616", 0.2)}
    <ViewLayout bgColor={blendedBg} accent={albumData.accent_colors}>
        <header
            slot="header"
            class="relative w-full flex items-end md:px-8 pt-8 pb-4 pt-18"
        >
            <img
                src={getImageUrl(albumData.id, 220)}
                alt=""
                class="absolute inset-0 w-full h-full blur-[1080px] object-cover pointer-events-none transition-opacity duration-700"
                style="opacity: {bgLoaded ? '0.25' : '0'}"
                on:load={() => {
                    bgLoaded = true;
                }}
            />

            <BackButton class="absolute top-4 left-4" />

            <div
                class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-6xl mx-auto pb-8 md:px-6 border-b border-white/10"
            >
                <div class="w-full px-8 md:w-auto md:p-0">
                    <img
                        src={getImageUrl(albumData.id, 220)}
                        alt={albumData.title}
                        class="w-full max-w-[40vh] md:w-55 md:h-55 mx-auto object-cover rounded-xl shadow-2xl border border-white/10 bg-zinc-800"
                    />
                </div>

                <div
                    class="flex-1 text-center md:text-left space-y-2 px-4 md:px-0"
                >
                    <span
                        class="text-xs uppercase font-black tracking-widest"
                        style="color: var(--accent-light)">ALBUM</span
                    >
                    <h1
                        class="text-2xl md:text-5xl font-black text-white line-clamp-2 mb-0 pb-1"
                    >
                        {albumData.title}
                    </h1>
                    <a
                        href={`/artist/${encodeURIComponent(albumData.artist_id)}`}
                        class="inline-block pb-0.5 text-md md:text-lg font-semibold text-zinc-400 hover:text-white hover:underline transition"
                    >
                        {albumData.artist_name}
                    </a>
                    <div class="flex justify-center md:justify-start">
                        <Rating
                            id={albumData.id}
                            itemType="album"
                            rating={albumData.rating}
                            rated_color="var(--accent-light)"
                            size={16}
                        />
                    </div>
                    <div
                        class="flex flex-wrap items-center justify-center md:justify-start gap-2 text-sm text-zinc-400 font-medium"
                    >
                        <span>{albumData.release_year}</span>
                        <span>∙</span>
                        <span>{tracks.length} tracks</span>
                        <span>∙</span>
                        <span
                            >{formatTime(
                                tracks.reduce(
                                    (acc: number, track: any) =>
                                        acc + track.duration_ms,
                                    0,
                                ),
                                true,
                            )}</span
                        >
                    </div>
                </div>

                <div
                    class="md:absolute right-0 flex justify-center items-center gap-4"
                >
                    <button
                        on:click={() => playAlbum(albumId, false)}
                        class="order-2 md:order-1 px-8 md:px-6 py-2 rounded-full text-white font-bold transition border border-white/10"
                        style="background-color: var(--accent)"
                    >
                        <div class="flex items-center gap-4">
                            <IconPlayerPlayFilled size={16} />
                            Play
                        </div>
                    </button>
                    <IconButton
                        on:click={() => playAlbum(albumId, true)}
                        class="order-1 md:order-2"
                    >
                        <IconArrowsShuffle size={16} />
                    </IconButton>
                    <ContextMenu
                        items={buildCollectionMenuItems(allTrackIds)}
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
            class="text-zinc-400 w-full max-w-6xl px-0 md:px-4 mx-auto pb-20"
        >
            <!-- {#if albumData.description}
                <p class="text-sm text-zinc-500 leading-relaxed mx-4 mt-3 mb-4">
                    {albumData.description}
                </p>
            {/if} -->

            {#each discs as disc}
                {#if showDiscLabels}
                    {@const discTrackIds = disc.tracks.map((t: any) => t.id)}
                    <div
                        class="flex items-center text-white/75 font-bold py-2 px-4 md:px-0"
                    >
                        <IconDisc size={16} class="mr-2" />
                        <div>Disc {disc.disc_number}</div>
                        <!-- <div class="flex items-center gap-2">
                            <button
                                class="p-2 rounded-full text-white border border-white/10 cursor-pointer transition"
                                style="background-color: var(--accent)"
                            >
                                <IconPlayerPlayFilled size={16} />
                            </button>
                            <IconButton><IconArrowsShuffle size={16} /></IconButton>
                            <ContextMenu
                                items={buildCollectionMenuItems(discTrackIds)}
                                let:toggle
                            >
                                <IconButton on:click={toggle}>
                                    <IconMenu2Filled size={16} />
                                </IconButton>
                            </ContextMenu>
                        </div> -->
                    </div>
                {/if}

                <div class="mb-8 px-0 mt-0">
                    {#each disc.tracks as track, index}
                        {@const focused = String(track.id) === focusTrackId}
                        <!-- svelte-ignore a11y_no_static_element_interactions -->
                        <!-- svelte-ignore a11y_click_events_have_key_events -->
                        <div
                            use:scrollIntoViewIfFocused={focused}
                            on:click={() => playAlbumAtTrack(albumId, track.id)}
                            class:track-flash={focused}
                            class="flex items-center px-4 p-2 md:pr-2 group transition duration-200 gap-4 cursor-pointer md:rounded-full min-w-0 hover:bg-white/5"
                        >
                            <div
                                class="w-6 h-6 flex-shrink-0 flex items-center justify-center relative"
                            >
                                <div
                                    class="absolute -inset-0 flex items-center justify-center opacity-100 group-hover:opacity-0 transition-opacity duration-200 text-xs"
                                    style="color: var(--accent-light)"
                                >
                                    {track.track_number || index + 1}
                                </div>
                                <IconPlayerPlayFilled
                                    size={20}
                                    class="absolute opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                                    style="color: var(--accent-light)"
                                />
                            </div>

                            <div
                                class="flex grow min-w-0 flex-col overflow-hidden h-[36px] justify-center"
                            >
                                <p class="text-white text-sm truncate min-w-0">
                                    {track.title}
                                </p>
                                {#if track.artist_name !== albumData.artist_name}
                                    <p
                                        class="text-zinc-400 text-xs truncate min-w-0"
                                    >
                                        {track.artist_name}
                                    </p>
                                {/if}
                            </div>

                            <div
                                class="flex-shrink-0 flex gap-4 justify-end items-center"
                            >
                                <Rating
                                    id={track.id}
                                    itemType="track"
                                    rating={track.rating}
                                    size={12}
                                    rated_color="var(--accent-light)"
                                />
                                <div class=" ml-4 text-xs text-zinc-400">
                                    {formatTime(track.duration_ms, true)}
                                </div>
                                <ContextMenu
                                    items={buildTrackMenuItems(track.id)}
                                    let:toggle
                                >
                                    <IconButton
                                        on:click={(e) => toggle(e)}
                                        class="text-white"
                                    >
                                        <IconMenu2Filled size={16} />
                                    </IconButton>
                                </ContextMenu>
                            </div>
                        </div>
                    {/each}
                </div>
            {/each}
        </div>
    </ViewLayout>
{:else}
    <div class="p-8 text-red-400">Album not found.</div>
{/if}

<style>
    .track-flash {
        animation: track-flash 3s ease-out;
    }
    @keyframes track-flash {
        0%,
        70% {
            background-color: rgba(255, 255, 255, 0.05);
        }
        100% {
            background-color: transparent;
        }
    }
</style>
