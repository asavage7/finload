<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state";
    import { afterNavigate } from "$app/navigation";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import DetailHeader from "$lib/components/DetailHeader.svelte";
    import TrackListRow from "$lib/components/TrackListRow.svelte";
    import GenreChips from "$lib/components/GenreChips.svelte";
    import Carousel from "$lib/components/Carousel.svelte";
    import MediaCard from "$lib/components/MediaCard.svelte";
    import Rating from "$lib/components/Rating.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import EmptyState from "$lib/components/EmptyState.svelte";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import {
        IconDisc,
        IconDiscFilled,
        IconPlugConnectedX,
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
    let moreByArtist: any[] = [];
    let similarAlbums: any[] = [];
    let isLoading = true;
    let notFound = false;
    let loadFailed = false;

    onMount(async () => {
        const colorsPromise = fetchAccentColors("album", albumId);
        try {
            const res = await fetch(apiUrl(`/api/album/${albumId}`));
            if (res.status === 404) {
                notFound = true;
                isLoading = false;
                return;
            }
            if (!res.ok) {
                loadFailed = true;
                isLoading = false;
                return;
            }
            const data = await res.json();

            albumData = data.album;
            albumData.accent_colors = ["#888888", "#888888", "#1c1c1f"];
            tracks = [].concat(...data.discs.map((disc: any) => disc.tracks));
            discs = data.discs;
            moreByArtist = data.more_by_artist ?? [];
            similarAlbums = data.similar_albums ?? [];
            isLoading = false;

            const colors = await colorsPromise;
            if (colors.length > 0) {
                albumData.accent_colors = colors;
                albumData = albumData;
            }
        } catch (error) {
            console.error("Failed to load album details:", error);
            loadFailed = true;
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
        focusTrackId =
            type === "popstate" ? null : page.url.searchParams.get("track");
    });
</script>

{#if isLoading}
    <Loading />
{:else if albumData}
    {@const blendedBg = blendHex(albumData.accent_colors[2], "#18181B", 0.15)}
    <ViewLayout bgColor={blendedBg} accent={albumData.accent_colors}>
        <svelte:fragment slot="header">
            <DetailHeader
                typeLabel="ALBUM"
                title={albumData.title}
                id={albumData.id}
                onPlay={() => playAlbum(albumId, false)}
                onShuffle={() => playAlbum(albumId, true)}
                menuItems={buildCollectionMenuItems(allTrackIds, { id: albumId, type: 'album' })}
            >
                <svelte:fragment slot="meta">
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
                    <GenreChips genres={albumData.genres ?? []} />
                </svelte:fragment>
            </DetailHeader>
        </svelte:fragment>

        <!-- Horizontal container mirrors DetailHeader (outer md:px-8 + inner
             max-w-8xl mx-auto px-4 md:px-6) so header and content stay aligned
             at every width, including the 8xl band. -->
        <div slot="content" class="w-full md:px-8 pb-20">
            <div class="text-zinc-400 w-full max-w-[var(--8xl)] mx-auto px-4 md:px-6">
            {#each discs as disc}
                {#if showDiscLabels}
                    <div
                        class="flex items-center text-[var(--accent-light)] font-semibold py-2"
                    >
                        <IconDisc size={16} class="mr-2" />
                        <div>Disc {disc.disc_number}</div>
                    </div>
                {/if}

                <div class="mb-8">
                    {#each disc.tracks as track, index}
                        <TrackListRow
                            trackId={track.id}
                            title={track.title}
                            subtitle={track.artist_name !==
                            albumData.artist_name
                                ? track.artist_name
                                : ""}
                            number={track.track_number || index + 1}
                            rating={track.rating}
                            durationMs={track.duration_ms}
                            menuItems={buildTrackMenuItems(track.id)}
                            onPlay={() => playAlbumAtTrack(albumId, track.id)}
                            focused={String(track.id) === focusTrackId}
                        />
                    {/each}
                </div>
            {/each}

            <div class="flex flex-col gap-4 mt-8 mb-8">
                <Carousel title={`More by ${albumData.artist_name}`} items={moreByArtist} layout="row" let:item>
                    <MediaCard
                        id={item.id}
                        title={item.title}
                        subtitle={item.release_year}
                        imageUrl={getImageUrl(item.id, 240, "album")}
                        type="album"
                        rating={item.rating}
                    />
                </Carousel>

                <Carousel title="Similar Albums" items={similarAlbums} layout="row" let:item>
                    <MediaCard
                        id={item.id}
                        title={item.title}
                        subtitle={item.artist_name}
                        subtitleHref={item.artist_id ? `/artist/${item.artist_id}` : ""}
                        imageUrl={getImageUrl(item.id, 240, "album")}
                        type="album"
                        rating={item.rating}
                    />
                </Carousel>
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
                icon={IconDiscFilled}
                title="Album not found."
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
                title="Couldn't load this album."
                message="Lost contact with the player service. It may still be starting, or it stopped unexpectedly."
            />
        </div>
    </ViewLayout>
{/if}
