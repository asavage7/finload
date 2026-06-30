<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import ContextMenu from "$lib/components/ContextMenu.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import {
        IconPlayerPlayFilled,
        IconArrowsShuffle,
        IconMenu2Filled,
        IconCamera,
        IconPlaylistFilled,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";
    import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
    import { blendHex } from "$lib/utils/color";
    import {
        playPlaylist,
        playPlaylistAtTrack,
        buildPlaylistTrackMenuItems,
        buildPlaylistMenuItems,
    } from "$lib/utils/playback";
    import PlaylistCover from "$lib/components/PlaylistCover.svelte";
    import Rating from "$lib/components/Rating.svelte";
    import { playlistCoverTimestamps } from "$lib/store";

    const playlistId = page.params.id!;

    let playlistData: any = null;
    let tracks: any[] = [];
    let accentColors = ["#888888", "#ffffff", "#1a1a1a"];
    let isLoading = false;
    let useAlbumFallbackImage = false;

    onMount(async () => {
        try {
            const res = await fetch(apiUrl(`/api/playlist/${playlistId}`));
            const data = await res.json();

            playlistData = data.playlist;
            tracks = data.tracks;

            const colors = await fetchAccentColors("playlist", playlistId);
            if (colors.length > 0) accentColors = colors;
        } catch (error) {
            console.error("Failed to load playlist details:", error);
        } finally {
            isLoading = false;
        }
    });

    function removeTrack(itemId: number) {
        tracks = tracks.filter((t: any) => t.item_id !== itemId);
    }

    async function handleImageUpload(file: File) {
        const form = new FormData();
        form.append("file", file);
        await fetch(apiUrl(`/api/playlist/${playlistId}/image`), {
            method: "POST",
            body: form,
        });
        playlistCoverTimestamps.update((m) => ({
            ...m,
            [playlistId]: Date.now(),
        }));
        const colors = await fetchAccentColors("playlist", playlistId);
        if (colors.length > 0) accentColors = colors;
    }

    $: allTrackIds = Array.isArray(tracks) ? tracks.map((t: any) => t.id) : [];
    $: firstAlbumIds = [
        ...new Set(tracks.map((t: any) => t.album_id).filter(Boolean)),
    ].slice(0, 4) as string[];
    $: playlistHeaderImage = useAlbumFallbackImage
        ? getImageUrl(firstAlbumIds[0], 220, "album")
        : getImageUrl(playlistData?.id, 220, "playlist") ||
          getImageUrl(firstAlbumIds[0], 220, "album");

    let bgLoaded = false;
</script>

{#if isLoading}
    <Loading />
{:else if playlistData}
    {@const blendedBg = blendHex(accentColors[2], "#18181b", 0.25)}
    <ViewLayout bgColor={blendedBg} accent={accentColors}>
        <header
            slot="header"
            class="relative w-full flex items-end md:px-8 pt-8 pb-2 md:pb-0 pt-18"
        >
            <img
                src={playlistHeaderImage}
                alt=""
                class="absolute inset-0 w-full h-full object-cover blur-3xl pointer-events-none transition-opacity duration-700"
                style="opacity: {bgLoaded ? '0.25' : '0'}"
                on:load={() => { bgLoaded = true; }}
            />

            <BackButton class="absolute top-4 left-4" />

            <div
                class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-6xl mx-auto pb-8 md:px-6 border-b border-white/10"
            >
                <div class="w-full px-8 md:w-auto md:p-0">
                    <!-- svelte-ignore a11y_click_events_have_key_events -->
                    <!-- svelte-ignore a11y_no_static_element_interactions -->
                    <div
                        class="relative w-full max-w-[40vh] md:w-55 md:h-55 mx-auto aspect-square rounded-xl shadow-2xl border border-white/10 overflow-hidden group/cover cursor-pointer"
                        on:click={() => {
                            const input = document.createElement("input");
                            input.type = "file";
                            input.accept = "image/*";
                            input.onchange = (e) => {
                                const file = (e.target as HTMLInputElement)
                                    .files?.[0];
                                if (file) handleImageUpload(file);
                            };
                            input.click();
                        }}
                    >
                        <PlaylistCover
                            playlistId={playlistData.id}
                            name={playlistData.name}
                            albumIds={firstAlbumIds}
                            size={220}
                        />
                        <div
                            class="absolute inset-0 flex items-center justify-center opacity-0 group-hover/cover:opacity-100 transition-opacity duration-200 bg-black/50"
                        >
                            <IconCamera size={28} class="text-white" />
                        </div>
                    </div>
                </div>

                <div class="flex-1 text-center md:text-left space-y-2">
                    <span
                        class="text-xs uppercase font-black tracking-widest"
                        style="color: var(--accent-light)">PLAYLIST</span
                    >
                    <h1
                        class="text-2xl md:text-5xl font-black text-white line-clamp-2 mb-0 pb-1"
                    >
                        {playlistData.name}
                    </h1>
                    <div
                        class="flex flex-wrap items-center justify-center md:justify-start gap-2 text-sm text-zinc-400 font-medium"
                    >
                        <span>{tracks.length} tracks</span>
                        <span>∙</span>
                        <span
                            >{formatTime(
                                Array.isArray(tracks)
                                    ? tracks.reduce(
                                          (acc: number, track: any) =>
                                              acc + (track.duration_ms || 0),
                                          0,
                                      )
                                    : 0,
                                true,
                            )}</span
                        >
                    </div>
                </div>

                <div
                    class="md:absolute right-0 flex justify-center items-center gap-4"
                >
                    <button
                        on:click={() => playPlaylist(playlistData.id, false)}
                        class="px-6 py-2 rounded-full text-white font-bold transition border border-white/10"
                        style="background-color: var(--accent)"
                    >
                        <div class="flex items-center gap-2">
                            <IconPlayerPlayFilled size={16} />
                            Play
                        </div>
                    </button>
                    <IconButton on:click={() => playPlaylist(playlistId, true)}>
                        <IconArrowsShuffle size={16} />
                    </IconButton>
                    <ContextMenu
                        items={buildPlaylistMenuItems(
                            playlistId,
                            playlistData.name,
                        )}
                        let:toggle
                    >
                        <IconButton on:click={toggle}>
                            <IconMenu2Filled size={16} />
                        </IconButton>
                    </ContextMenu>
                </div>
            </div>
        </header>

        <div
            slot="content"
            class="text-zinc-400 w-full max-w-6xl pt-4 px-0 md:px-4 mx-auto pb-20"
        >
            <div class="mb-8 px-0 mt-0">
                {#if tracks.length > 0}
                    {#each tracks as track, index}
                        <!-- svelte-ignore a11y_no_static_element_interactions -->
                        <!-- svelte-ignore a11y_click_events_have_key_events -->
                        <div
                            on:click={() =>
                                playPlaylistAtTrack(playlistData.id, track.id)}
                            class="flex items-center pl-4 p-2.5 hover:bg-white/5 group transition duration-200 gap-4 cursor-pointer md:rounded-full min-w-0"
                        >
                            <div
                                class="w-[36px] h-[36px] flex-shrink-0 flex items-center justify-center relative"
                            >
                                <div
                                    class="absolute -inset-0 flex items-center justify-center opacity-100 group-hover:brightness-50 transition-brightness duration-200 text-xs"
                                    style="color: var(--accent-light)"
                                >
                                    <img
                                        src={getImageUrl(track.id, 220, "track")}
                                        alt=""
                                        class="w-full h-full object-cover rounded-sm"
                                    />
                                </div>
                                <IconPlayerPlayFilled
                                    size={20}
                                    class="absolute opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-white"
                                />
                            </div>

                            <div
                                class="flex grow min-w-0 flex-col overflow-hidden h-[36px] justify-center"
                            >
                                <p class="text-white text-sm truncate min-w-0">
                                    {track.title}
                                </p>
                                <p
                                    class="text-zinc-400 text-xs truncate min-w-0"
                                >
                                    {track.artist_name}
                                </p>
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
                                <div class="ml-4 text-xs text-zinc-400">
                                    {formatTime(track.duration_ms, true)}
                                </div>
                                <ContextMenu
                                    items={buildPlaylistTrackMenuItems(
                                        track.id,
                                        playlistId,
                                        track.item_id,
                                        () => removeTrack(track.item_id),
                                    )}
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
                {:else}
                    <div
                        class="w-full py-16 flex flex-col items-center gap-4 text-zinc-400"
                    >
                        <IconPlaylistFilled size={24} />
                        <p class="text-md">This playlist has no tracks.</p>
                        <p class="text-sm">
                            To add tracks to this playlist, click the menu
                            button on an album or track, select "Add to
                            Playlist", and choose this playlist from the list.
                        </p>
                    </div>
                {/if}
            </div>
        </div></ViewLayout
    >
{:else}
    <div class="p-8 text-red-400">Playlist not found.</div>
{/if}
