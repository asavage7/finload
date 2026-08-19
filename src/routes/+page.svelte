<script lang="ts">
  import { onMount } from "svelte";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import Carousel from "$lib/components/Carousel.svelte";
  import MediaCard from "$lib/components/MediaCard.svelte";
  import MediaRow from "$lib/components/MediaRow.svelte";
  import Loading from "$lib/components/Loading.svelte";
  import EmptyState from "$lib/components/EmptyState.svelte";
  import {
    IconLibrary,
    IconPlugConnectedX,
    IconChevronLeft,
    IconChevronRight,
    IconPlayerPlayFilled,
    IconInfinity,
    IconArrowsShuffle,
  } from "@tabler/icons-svelte";
  import { apiUrl } from "$lib/backend";
  import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
  import {
    playAlbum,
    startRadio,
    playTracks,
    getTrackIds,
  } from "$lib/utils/playback";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import CoverImage from "$lib/components/CoverImage.svelte";

  let data: any = null;
  let isLoading = true;
  let loadFailed = false;

  // Index into recently_played_albums for the hero card; the left/right
  // arrows step through it, and the hero's backdrop/accent colors follow
  // whichever album is currently selected.
  let heroIndex = 0;
  let heroAccentColors = ["rgba(255,255,255,0.1)", "#4654ad", "#1c1c1f"];

  onMount(async () => {
    try {
      const res = await fetch(apiUrl("/api/home"));
      if (!res.ok) {
        loadFailed = true;
        isLoading = false;
        return;
      }
      data = await res.json();
      isLoading = false;
    } catch (error) {
      console.error("Failed to load home:", error);
      loadFailed = true;
      isLoading = false;
    }
  });

  $: isEmpty =
    data &&
    (data.hero_candidates ?? []).length === 0 &&
    (data.rows ?? []).every((row: any) => row.items.length === 0);

  $: heroCandidates = data?.hero_candidates ?? [];
  $: heroCandidate = heroCandidates[heroIndex] ?? null;
  $: heroAlbum = heroCandidate?.item ?? null;

  // Guards against a slow fetch for an album the user has already clicked
  // past resolving after a newer one, which would otherwise flash the wrong
  // accent colors onto the current hero album.
  async function loadHeroAccent(album: any) {
    const colors = await fetchAccentColors("album", album.id);
    if (heroAlbum?.id !== album.id) return;
    heroAccentColors =
      colors.length > 0
        ? colors
        : ["rgba(255,255,255,0.1)", "#4654ad", "#1c1c1f"];
  }

  $: if (heroAlbum) loadHeroAccent(heroAlbum);

  function prevHero() {
    heroIndex = (heroIndex - 1 + heroCandidates.length) % heroCandidates.length;
  }
  function nextHero() {
    heroIndex = (heroIndex + 1) % heroCandidates.length;
  }

  // "Recently Played"/"Recently Added" are facts, not recommendations —
  // shuffle is offered on every other row. Track rows are already track
  // ids; album/artist rows need each item's own tracklist fetched first.
  async function shuffleRow(row: any) {
    if (row.item_type === "track") {
      await playTracks(
        row.items.map((t: any) => t.id),
        true,
      );
      return;
    }
    const idLists = await Promise.all(
      row.items.map((item: any) => getTrackIds(item.id, row.item_type)),
    );
    await playTracks(idLists.flat(), true);
  }
</script>

{#if isLoading}
  <Loading />
{:else if data}
  <ViewLayout accent={heroAccentColors}>
    <div slot="content" class="w-full h-full overflow-y-auto pb-28">
      <div class=" px-4 md:px-8 pt-10 max-w-[var(--10xl)] mx-auto">
        <div
          class="relative flex flex-col md:flex-row md:items-end gap-6 mb-10 p-4 shadow-xl border border-white/10 rounded-xl overflow-hidden"
          style={`background-image: linear-gradient(to bottom, ${heroAccentColors[0]}60, ${heroAccentColors[2]}90 80%) `}
        >
          {#if heroAlbum}
            <div class="absolute inset-0 opacity-25 pointer-events-none">
              <CoverImage
                src={getImageUrl(heroAlbum.id, 240, "album")}
                alt=""
                showPlaceholder={false}
                class="w-full h-full blur-3xl"
              />
            </div>

            <div class="flex-1 z-10 m-4 min-w-0">
              {#key heroAlbum.id}
                <div
                  class="text-sm text-white/50 mb-1"
                >
                  {heroCandidate.reason_label}
                </div>
                <h1
                  class="text-2xl md:text-5xl font-black text-white truncate mb-0 pb-1"
                >
                  {heroAlbum.title}
                </h1>
                {#if heroAlbum.artist_id}
                  <a
                    href={`/artist/${heroAlbum.artist_id}`}
                    class="inline-block mb-0 pb-1 text-sm md:text-lg text-zinc-300 hover:text-white hover:underline transition"
                  >
                    {heroAlbum.artist_name}
                  </a>
                {:else}
                  <div class="mt-1 text-sm md:text-lg text-zinc-300">
                    {heroAlbum.artist_name}
                  </div>
                {/if}

                <div class="flex items-center gap-2 mt-5">
                  <IconButton
                    accent
                    text
                    on:click={() => playAlbum(heroAlbum.id)}
                  >
                    <IconPlayerPlayFilled size={16} />
                    <span>Play</span>
                  </IconButton>
                  <IconButton
                    text
                    white
                    on:click={() => playAlbum(heroAlbum.id, true)}
                  >
                    <IconArrowsShuffle size={16} />
                    <span>Shuffle</span>
                  </IconButton>
                  <IconButton
                    text
                    white
                    on:click={() => startRadio(heroAlbum.id, "album")}
                  >
                    <IconInfinity size={16} />
                    <span>Start Radio</span>
                  </IconButton>
                </div>
              {/key}
            </div>

            <div class="flex items-center gap-3 z-10">
              <button
                on:click={prevHero}
                aria-label="Previous album"
                class="p-2 rounded-full border border-transparent text-zinc-400 transition hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none"
              >
                <IconChevronLeft size={18} />
              </button>

              <a href={`/album/${heroAlbum.id}`} class="w-40 md:w-48 shrink-0">
                {#key heroAlbum.id}
                  <CoverImage
                    src={getImageUrl(heroAlbum.id, 240, "album")}
                    alt={heroAlbum.title}
                    fallbackText={heroAlbum.title}
                    class="w-full aspect-square rounded-lg shadow-lg"
                  />
                {/key}
              </a>

              <button
                on:click={nextHero}
                aria-label="Next album"
                class="p-2 rounded-full border border-transparent text-zinc-400 transition hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none"
              >
                <IconChevronRight size={18} />
              </button>
            </div>
          {/if}
        </div>

        <div class="flex flex-col gap-8">
          {#each data.rows as row, i (i)}
            <Carousel
              title={row.title}
              items={row.items}
              layout={row.item_type === "track" ? "columns" : "row"}
              onShuffle={row.reason_kind === "recently_played" ||
              row.reason_kind === "recently_added"
                ? null
                : () => shuffleRow(row)}
              let:item
            >
              {#if row.item_type === "artist"}
                <MediaCard
                  id={item.id}
                  title={item.name}
                  subtitle={`${item.album_count} album${item.album_count === 1 ? "" : "s"}`}
                  imageUrl={getImageUrl(item.id, 240, "artist")}
                  type="artist"
                />
              {:else if row.item_type === "track"}
                <MediaRow
                  id={item.id}
                  album_id={item.album_id}
                  title={item.title}
                  subtitle={item.artist_name}
                  imageUrl={getImageUrl(item.id, 240, "track")}
                  type="track"
                  queueContext={row.items.map((t: any) => t.id)}
                  compact
                />
              {:else}
                <MediaCard
                  id={item.id}
                  title={item.title}
                  subtitle={item.artist_name}
                  subtitleHref={item.artist_id
                    ? `/artist/${item.artist_id}`
                    : ""}
                  imageUrl={getImageUrl(item.id, 240, "album")}
                  type="album"
                  rating={item.rating}
                />
              {/if}
            </Carousel>
          {/each}

          {#if isEmpty}
            <EmptyState
              icon={IconLibrary}
              title="Nothing to show yet."
              message="Play some music and rate a few albums, and this page will fill in."
            />
          {/if}
          <div
            class="flex flex-col gap-4 items-center justify-center text-sm text-zinc-400 my-10"
          >
            <span>Don't see what you're looking for?</span>
            <IconButton accent text on:click={() => window.location.assign("/library")}>
              <IconLibrary size={16} />
              <span>Go to your Library</span>
            </IconButton>
          </div>
        </div>
      </div>
    </div></ViewLayout
  >
{:else}
  <ViewLayout>
    <div slot="content" class="relative w-full h-full">
      <EmptyState
        variant="error"
        icon={IconPlugConnectedX}
        title="Couldn't load your home page."
        message="Backend unavailable. Start the backend dev server and refresh."
      />
    </div>
  </ViewLayout>
{/if}
