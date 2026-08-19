<script lang="ts">
    import { onMount } from "svelte";
    import { page } from "$app/state";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import DetailHeader from "$lib/components/DetailHeader.svelte";
    import GenreChips from "$lib/components/GenreChips.svelte";
    import Carousel from "$lib/components/Carousel.svelte";
    import MediaCard from "$lib/components/MediaCard.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import EmptyState from "$lib/components/EmptyState.svelte";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import {
        IconMicrophoneFilled,
        IconPlugConnectedX,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";
    import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
    import { blendHex } from "$lib/utils/color";
    import { playArtist, buildCollectionMenuItems } from "$lib/utils/playback";

    const artistId = page.params.id!;

    let artistData: any = null;
    let appearsOn: any[] = [];
    let similarArtists: any[] = [];
    let isLoading = true;
    let bioModalOpen = false;
    let notFound = false;
    let loadFailed = false;

    onMount(async () => {
        const colorsPromise = fetchAccentColors("artist", artistId);
        try {
            const [res, tracksRes] = await Promise.all([
                fetch(apiUrl(`/api/artist/${artistId}`)),
                fetch(apiUrl(`/api/artist/${artistId}/tracks`)),
            ]);
            if (res.status === 404) {
                notFound = true;
                isLoading = false;
                return;
            }
            if (!res.ok || !tracksRes.ok) {
                loadFailed = true;
                isLoading = false;
                return;
            }
            const [data, tracksData] = await Promise.all([
                res.json(),
                tracksRes.json(),
            ]);

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
                    rating: album.rating,
                    artist_name: data.artist.name,
                })),
                track_ids: tracksData.map((track: any) => track.id),
                genres: data.artist.genres ?? [],
                accent_colors: ["#888888", "#888888", "#18181b"],
            };
            appearsOn = data.appears_on ?? [];
            similarArtists = data.similar_artists ?? [];
            isLoading = false;

            // No bio means the artist was never enriched; kick that off in the
            // background so it's there on the next visit.
            if (!artistData.bio) {
                fetch(apiUrl(`/api/artist/${artistId}/enrich`), {
                    method: "POST",
                }).catch(() => {});
            }

            const colors = await colorsPromise;
            if (colors.length > 0) {
                artistData.accent_colors = colors;
                artistData = artistData;
            }
        } catch (error) {
            console.error("Failed to load artist details:", error);
            loadFailed = true;
            isLoading = false;
        }
    });
</script>

{#if isLoading}
    <Loading />
{:else if artistData}
    {@const blendedBg = blendHex(artistData.accent_colors[2], "#18181B", 0.15)}
    <ViewLayout bgColor={blendedBg} accent={artistData.accent_colors}>
        <svelte:fragment slot="header">
            <DetailHeader
                typeLabel="ARTIST"
                title={artistData.name}
                bgSrc={getImageUrl(artistData.id, 240)}
                onPlay={() => playArtist(artistId, false)}
                onShuffle={() => playArtist(artistId, true)}
                menuItems={buildCollectionMenuItems(artistData.track_ids, { id: artistId, type: 'artist' })}
                primaryAction="shuffle"
            >
                <CoverImage
                    slot="cover"
                    src={getImageUrl(artistData.id, 240)}
                    alt={artistData.name}
                    fallbackText={artistData.name}
                    class="w-full max-w-[40vh] aspect-square md:w-55 md:h-55 mx-auto rounded-xl shadow-2xl border border-white/10 bg-zinc-800"
                />
                <svelte:fragment slot="meta">
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
                    <div>
                        <GenreChips genres={artistData.genres} />
                    </div>

                    {#if artistData.bio}
                        <div>
                            <p class="text-sm text-zinc-400 line-clamp-2">
                                {artistData.bio}
                            </p>
                            {#if artistData.bio.length > 200}
                                <button
                                    on:click={() => (bioModalOpen = true)}
                                    class="text-xs text-zinc-400 hover:text-white hover:underline hover:cursor-pointer mt-1 transition-colors"
                                >
                                    Read more
                                </button>
                            {/if}
                        </div>
                    {/if}
                </svelte:fragment>
            </DetailHeader>
        </svelte:fragment>

        <!-- Horizontal container mirrors DetailHeader (outer md:px-8 + inner
             max-w-8xl mx-auto px-4 md:px-6) so header and content stay aligned
             at every width, including the 8xl band. -->
        <div slot="content" class="w-full md:px-8 pt-4 pb-28">
            <div
                class="text-zinc-400 w-full max-w-[var(--8xl)] mx-auto px-4 md:px-6 flex flex-col gap-8"
            >
            <Carousel title="Albums" items={artistData.albums} layout="row" let:item>
                <MediaCard
                    id={item.id}
                    title={item.title}
                    subtitle={item.release_year}
                    imageUrl={getImageUrl(item.id, 240, "album")}
                    type="album"
                    rating={item.rating}
                />
            </Carousel>

            <Carousel title="Appears On" items={appearsOn} layout="row" let:item>
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

            <Carousel title="Similar Artists" items={similarArtists} layout="row" let:item>
                <MediaCard
                    id={item.id}
                    title={item.name}
                    imageUrl={getImageUrl(item.id, 240, "artist")}
                    type="artist"
                />
            </Carousel>
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
{:else if notFound}
    <ViewLayout>
        <div slot="content" class="relative w-full h-full">
            <BackButton class="absolute top-4 left-4 z-20" />
            <EmptyState
                variant="not-found"
                icon={IconMicrophoneFilled}
                title="Artist not found."
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
                title="Couldn't load this artist."
                message="Lost contact with the player service. It may still be starting, or it stopped unexpectedly."
            />
        </div>
    </ViewLayout>
{/if}
