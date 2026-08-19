<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import DetailHeader from "$lib/components/DetailHeader.svelte";
    import TrackListRow from "$lib/components/TrackListRow.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import EmptyState from "$lib/components/EmptyState.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import { IconCamera, IconPlaylistFilled, IconPlugConnectedX } from "@tabler/icons-svelte";
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
    import { playlistCoverTimestamps } from "$lib/store";

    const playlistId = page.params.id!;

    let playlistData: any = null;
    let tracks: any[] = [];
    let accentColors = ["#888888", "#ffffff", "#1a1a1a"];
    let isLoading = true;
    let notFound = false;
    let loadFailed = false;

    onMount(async () => {
        try {
            const res = await fetch(apiUrl(`/api/playlist/${playlistId}`));
            if (res.status === 404) {
                notFound = true;
                return;
            }
            if (!res.ok) {
                loadFailed = true;
                return;
            }
            const data = await res.json();

            playlistData = data.playlist;
            tracks = data.tracks;

            const colors = await fetchAccentColors("playlist", playlistId);
            if (colors.length > 0) accentColors = colors;
        } catch (error) {
            console.error("Failed to load playlist details:", error);
            loadFailed = true;
        } finally {
            isLoading = false;
        }
    });

    function removeTrack(itemId: number) {
        tracks = tracks.filter((t: any) => t.item_id !== itemId);
    }

    function pickCoverImage() {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = "image/*";
        input.onchange = (e) => {
            const file = (e.target as HTMLInputElement).files?.[0];
            if (file) handleImageUpload(file);
        };
        input.click();
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

    $: firstAlbumIds = [
        ...new Set(tracks.map((t: any) => t.album_id).filter(Boolean)),
    ].slice(0, 4) as string[];
    $: headerBgSrc = apiUrl(`/api/playlist/${playlistId}/image`);
</script>

{#if isLoading}
    <Loading />
{:else if playlistData}
    {@const blendedBg = blendHex(accentColors[2], "#18181b", 0.15)}
    <ViewLayout bgColor={blendedBg} accent={accentColors}>
        <svelte:fragment slot="header">
            <DetailHeader
                typeLabel="PLAYLIST"
                title={playlistData.name}
                bgSrc={headerBgSrc}
                onPlay={() => playPlaylist(playlistData.id, false)}
                onShuffle={() => playPlaylist(playlistId, true)}
                menuItems={buildPlaylistMenuItems(playlistId, playlistData.name)}
            >
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    slot="cover"
                    class="relative w-full max-w-[40vh] md:w-55 md:h-55 mx-auto aspect-square rounded-xl shadow-2xl border border-white/10 overflow-hidden group/cover cursor-pointer"
                    on:click={pickCoverImage}
                >
                    <PlaylistCover
                        playlistId={playlistData.id}
                        name={playlistData.name}
                        albumIds={firstAlbumIds}
                        size={240}
                    />
                    <div
                        class="absolute inset-0 flex items-center justify-center opacity-0 group-hover/cover:opacity-100 transition-opacity duration-200 bg-black/50"
                    >
                        <IconCamera size={28} class="text-white" />
                    </div>
                </div>
                <svelte:fragment slot="meta">
                    <div
                        class="flex flex-wrap items-center justify-center md:justify-start gap-2 text-sm text-zinc-400 font-medium"
                    >
                        <span>{tracks.length} tracks</span>
                        <span>∙</span>
                        <span
                            >{formatTime(
                                tracks.reduce(
                                    (acc: number, track: any) =>
                                        acc + (track.duration_ms || 0),
                                    0,
                                ),
                                true,
                            )}</span
                        >
                    </div>
                </svelte:fragment>
            </DetailHeader>
        </svelte:fragment>

        <!-- Horizontal container mirrors DetailHeader (outer md:px-8 + inner
             max-w-8xl mx-auto px-4 md:px-6) so header and content stay aligned
             at every width, including the 8xl band. -->
        <div slot="content" class="w-full md:px-8 pt-4 pb-20">
            <div class="text-zinc-400 w-full max-w-[var(--8xl)] mx-auto px-4 md:px-6">
            <div class="mb-8">
                {#if tracks.length > 0}
                    {#each tracks as track (track.item_id)}
                        <TrackListRow
                            trackId={track.id}
                            title={track.title}
                            subtitle={track.artist_name}
                            imageSrc={getImageUrl(track.id, 240, "track")}
                            rating={track.rating}
                            durationMs={track.duration_ms}
                            menuItems={buildPlaylistTrackMenuItems(
                                track.id,
                                playlistId,
                                track.item_id,
                                () => removeTrack(track.item_id),
                            )}
                            onPlay={() =>
                                playPlaylistAtTrack(playlistData.id, track.id)}
                        />
                    {/each}
                {:else}
                    <EmptyState
                        icon={IconPlaylistFilled}
                        title="This playlist has no tracks."
                        message={`To add tracks to this playlist, click the menu button on an album or track, select "Add to Playlist", and choose this playlist from the list.`}
                    />
                {/if}
            </div>
            </div>
        </div>
    </ViewLayout>
{:else if notFound}
    <ViewLayout>
        <div slot="content" class="relative w-full h-full">
            <BackButton class="absolute top-4 left-4 z-20" />
            <EmptyState
                variant="not-found"
                icon={IconPlaylistFilled}
                title="Playlist not found."
                message="It may have been deleted or the link is incorrect."
            />
        </div>
    </ViewLayout>
{:else}
    <ViewLayout>
        <div slot="content" class="relative w-full h-full">
            <BackButton class="absolute top-4 left-4 z-20" />
            <EmptyState
                variant="error"
                icon={IconPlugConnectedX}
                title="Couldn't load this playlist."
                message="Backend unavailable. Start the backend dev server and refresh."
            />
        </div>
    </ViewLayout>
{/if}
