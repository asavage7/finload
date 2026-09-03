<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import DetailHeader from "$lib/components/DetailHeader.svelte";
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import Carousel from "$lib/components/Carousel.svelte";
  import MediaCard from "$lib/components/MediaCard.svelte";
  import MediaRow from "$lib/components/MediaRow.svelte";
  import Loading from "$lib/components/Loading.svelte";
  import EmptyState from "$lib/components/EmptyState.svelte";
  import { IconTagFilled, IconPlugConnectedX } from "@tabler/icons-svelte";
  import { apiUrl } from "$lib/backend";
  import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
  import { playTracks, buildCollectionMenuItemsWithSources } from "$lib/utils/playback";

  const genreId = page.params.id!;
  // Matches the library page's own "Shuffle All": shuffle the full pool
  // client-side, then cap how many actually go in the queue.
  const TRACKS_LIMIT = 200;

  let data: any = null;
  let isLoading = true;
  let notFound = false;
  let loadFailed = false;
  let genreColors = ["#888888", "#888888", "#1c1c1f"];

  $: queueTrackIds = data?.queue_track_ids ?? [];
  $: allTrackIds = data?.all_track_ids ?? [];

  function shuffledTrackIds(ids: string[]): string[] {
    const copy = [...ids];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy.slice(0, TRACKS_LIMIT);
  }

  onMount(async () => {
    const colorsPromise = fetchAccentColors("genre", genreId);
    try {
      const res = await fetch(apiUrl(`/api/genre/${genreId}`));
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
      data = await res.json();
      isLoading = false;

      const colors = await colorsPromise;
      if (colors.length > 0) genreColors = colors;
    } catch (error) {
      console.error("Failed to load genre:", error);
      loadFailed = true;
      isLoading = false;
    }
  });
</script>

{#if isLoading}
  <Loading />
{:else if data}
  <ViewLayout accent={genreColors}>
    <svelte:fragment slot="header">
      <DetailHeader
        typeLabel="GENRE"
        title={data.genre.name}
        primaryAction="shuffle"
        onPlay={() => playTracks(queueTrackIds, false)}
        onShuffle={() => playTracks(shuffledTrackIds(allTrackIds), true)}
        menuItems={buildCollectionMenuItemsWithSources(queueTrackIds, shuffledTrackIds(allTrackIds), queueTrackIds)}
      >
        <svelte:fragment slot="meta">
          <p class="text-zinc-400 text-sm">
            {data.albums.length} albums ∙ {data.artists.length} artists ∙ {allTrackIds.length} tracks
          </p>
        </svelte:fragment>
      </DetailHeader>
    </svelte:fragment>

    <!-- Padding mirrors DetailHeader (outer md:px-8 + inner md:px-6) so the
         carousels line up with the genre title at every breakpoint. -->
    <div slot="content" class="w-full md:px-8 pb-28">
      <div class="w-full max-w-[var(--8xl)] mx-auto px-4 md:px-6 flex flex-col gap-4">
        <Carousel title="Albums" items={data.albums} layout="row" let:item>
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

        <Carousel title="Artists" items={data.artists} layout="row" let:item>
          <MediaCard
            id={item.id}
            title={item.name}
            imageUrl={getImageUrl(item.id, 240, "artist")}
            type="artist"
          />
        </Carousel>

        <Carousel
          title="Tracks"
          items={data.tracks}
          layout="columns"
          let:item
        >
          <MediaRow
            id={item.id}
            album_id={item.album_id}
            title={item.title}
            subtitle={item.artist_name}
            imageUrl={getImageUrl(item.id, 240, "track")}
            type="track"
            queueContext={data.tracks.map((t: any) => t.id)}
            compact
          />
        </Carousel>

        {#if data.albums.length === 0 && data.artists.length === 0 && data.tracks.length === 0}
          <EmptyState
            icon={IconTagFilled}
            title="Nothing tagged with this genre yet."
            message="Genre enrichment may still be running in the background."
          />
        {/if}
      </div>
    </div>
  </ViewLayout>
{:else if notFound}
  <ViewLayout>
    <div slot="content" class="relative w-full h-full">
      <BackButton class="absolute top-4 left-4 z-20" />
      <EmptyState
        variant="not-found"
        icon={IconTagFilled}
        title="Genre not found."
        message="It may have been removed or the link is incorrect."
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
        title="Couldn't load this genre."
        message="Lost contact with the player service. It may still be starting, or it stopped unexpectedly."
      />
    </div>
  </ViewLayout>
{/if}
