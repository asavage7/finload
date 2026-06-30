<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { get } from "svelte/store";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import MediaCard from "$lib/components/MediaCard.svelte";
  import MediaRow from "$lib/components/MediaRow.svelte";
  import Loading from "$lib/components/Loading.svelte";
  import { apiUrl } from "$lib/backend";
  import { formatTime } from "$lib/utils/formatTime";
  import { getImageUrl } from "$lib/utils/media";
  import {
    IconRefresh,
    IconLayoutGrid,
    IconLayoutList,
    IconTrack,
    IconMicrophoneFilled,
    IconPlaylistFilled,
    IconDiscFilled,
    IconPlayerPlayFilled,
    IconArrowsShuffle,
    IconPlus,
    IconSortAscending,
    IconSortDescending,
  } from "@tabler/icons-svelte";
  import PlaylistCreationModal from "$lib/components/modals/PlaylistCreationModal.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import { playTracks, buildSortMenuItems } from "$lib/utils/playback";
  import {
    libraryActiveTab,
    libraryScrollTop,
    libraryActiveView,
    libraryItemCache,
    libraryTotalCounts,
    librarySortState,
  } from "$lib/store";
  import ContextMenu from "$lib/components/ContextMenu.svelte";

  const TRACKS_LIMIT = 200;

  function getTrackIdsSlice(shuffle: boolean): string[] {
    const loaded = items.filter(Boolean);
    if (!shuffle) return loaded.slice(0, TRACKS_LIMIT).map((t: any) => t.id);
    const copy = [...loaded];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy.slice(0, TRACKS_LIMIT).map((t: any) => t.id);
  }

  const tabs = ["Albums", "Tracks", "Artists", "Playlists"] as const;
  const tabicons = [
    IconDiscFilled,
    IconTrack,
    IconMicrophoneFilled,
    IconPlaylistFilled,
  ];

  $: activeTab = $libraryActiveTab;
  // items is derived from the persistent cache so switching back to a tab is instant.
  $: items = $libraryItemCache[activeTab] ?? [];
  $: totalCount = $libraryTotalCounts[activeTab] ?? 0;

  // Tracks in-flight request start indices to avoid duplicate fetches.
  let loadingStarts: Set<number> = new Set();

  let loadError = "";
  // Don't show the spinner if we already have cached data for this tab.
  let isLoading = !get(libraryTotalCounts)[get(libraryActiveTab)];
  let scrollContainer: HTMLDivElement | null = null;
  let gridScrollContainer: HTMLDivElement | null = null;
  let gridWidth = 0;
  let contentWrapper: HTMLElement | null = null;

  function getCardType(tab: string): "artist" | "album" | "playlist" | "track" {
    if (tab === "Artists") return "artist";
    if (tab === "Playlists") return "playlist";
    if (tab === "Tracks") return "track";
    return "album";
  }

  // --- Hand-rolled list windowing (single column, fixed-height rows) ---
  let listScrollTop = 0;
  let listViewportH = 0;
  let listRowHeight = 61; // seed; corrected by one measurement
  let listMeasured = false;
  const LIST_OVERSCAN = 6;

  $: listFirstIdx = Math.max(
    0,
    Math.floor(listScrollTop / listRowHeight) - LIST_OVERSCAN,
  );
  $: listLastIdx = Math.min(
    totalCount - 1,
    Math.ceil((listScrollTop + listViewportH) / listRowHeight) + LIST_OVERSCAN,
  );
  $: listVisible = items.slice(listFirstIdx, listLastIdx + 1);
  $: listTotalHeight = totalCount * listRowHeight;

  // Chunk size scales with how many items fit on screen so we don't over/under-fetch.
  $: chunkSize = Math.max(
    100,
    ($libraryActiveView[activeTab] === "list"
      ? Math.ceil(listViewportH / Math.max(listRowHeight, 1))
      : Math.ceil(gridViewportH / Math.max(gridRowHeight, 1)) *
        Math.max(gridCols, 1)) * 2,
  );
  $: evictionBuffer = chunkSize * 3;
  $: listOffsetY = listFirstIdx * listRowHeight;

  function measureList(node: HTMLElement) {
    if (listMeasured) return;
    requestAnimationFrame(() => {
      const row = node.firstElementChild as HTMLElement | null;
      const h = row?.getBoundingClientRect().height ?? 0;
      if (h > 0) {
        listRowHeight = h;
        listMeasured = true;
      }
    });
  }

  const GRID_GAP = 8; // gap-2 (px) between cards, both axes
  const GRID_PX = 16; // px-4 (px) horizontal padding on the scroll container

  $: minCardWidth = gridWidth >= 1280 ? 200 : 160;
  $: gridContentWidth = Math.max(0, gridWidth - GRID_PX * 2);
  $: gridCols =
    gridContentWidth > 0
      ? Math.max(
          1,
          Math.floor((gridContentWidth + GRID_GAP) / (minCardWidth + GRID_GAP)),
        )
      : 1;
  $: gridColWidth =
    (gridContentWidth - GRID_GAP * (gridCols - 1)) / gridCols || minCardWidth;

  // Row height is linear in column width: height = colWidth + C, where the
  // offset C (text block + paddings) is width-independent. We measure C exactly
  // once per tab, then compute height arithmetically — no per-frame reflow.
  let rowOffsets: Record<string, number> = {};
  $: rowOffset = rowOffsets[activeTab];
  $: gridRowHeight =
    (rowOffset != null ? gridColWidth + rowOffset : gridColWidth + 70) +
    GRID_GAP;

  // Measure a single card's height once per tab to derive the width-independent
  // offset. The card is the grid's first child; its height excludes the gap.
  function measureGrid(node: HTMLElement) {
    if (rowOffsets[activeTab] != null) return;
    const tab = activeTab;
    requestAnimationFrame(() => {
      const card = node.firstElementChild as HTMLElement | null;
      const h = card?.getBoundingClientRect().height ?? 0;
      if (h > 0) rowOffsets = { ...rowOffsets, [tab]: h - gridColWidth };
    });
  }

  // --- Hand-rolled grid windowing ---
  // All cards live in ONE keyed {#each} under a single CSS-grid parent, so a
  // column change just shifts the slice + grid-template-columns and Svelte
  // reuses/moves nodes (no remount storm from a nested per-row {#each}).
  let gridScrollTop = 0;
  let gridViewportH = 0;
  const OVERSCAN_ROWS = 2;

  $: gridRowCount = Math.ceil(totalCount / gridCols);
  $: gridTotalHeight = gridRowCount * gridRowHeight;
  $: gridFirstRow = Math.max(
    0,
    Math.floor(gridScrollTop / gridRowHeight) - OVERSCAN_ROWS,
  );
  $: gridLastRow = Math.min(
    Math.max(gridRowCount - 1, 0),
    Math.ceil((gridScrollTop + gridViewportH) / gridRowHeight) + OVERSCAN_ROWS,
  );
  $: gridStartIdx = gridFirstRow * gridCols;
  $: gridEndIdx = Math.min(totalCount, (gridLastRow + 1) * gridCols);
  $: gridVisible = items.slice(gridStartIdx, gridEndIdx);
  $: gridOffsetY = gridFirstRow * gridRowHeight;

  // --- Scroll persistence ---

  function activeScrollContainer(): HTMLDivElement | null {
    return $libraryActiveView[activeTab] === "grid"
      ? gridScrollContainer
      : scrollContainer;
  }

  function saveScrollPosition() {
    const container = activeScrollContainer();
    if (container) {
      libraryScrollTop.update((m) => ({
        ...m,
        [activeTab]: container.scrollTop,
      }));
    }
  }

  async function restoreScrollPosition(tab: string) {
    const saved = get(libraryScrollTop)[tab] ?? 0;
    if (saved <= 0) return;
    await tick();
    // Wait one animation frame so the virtualizer finishes its first layout pass.
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve()),
    );
    const container =
      $libraryActiveView[tab] === "grid"
        ? gridScrollContainer
        : scrollContainer;
    if (container) container.scrollTop = saved;
  }

  let gridScrollRaf = 0;
  let listScrollRaf = 0;

  function onGridScroll(e: Event) {
    const el = e.currentTarget as HTMLElement;
    if (gridScrollRaf) return;
    gridScrollRaf = requestAnimationFrame(() => {
      gridScrollRaf = 0;
      gridScrollTop = el.scrollTop;
      triggerWindowLoad();
    });
  }

  function onListScroll(e: Event) {
    const el = e.currentTarget as HTMLElement;
    if (listScrollRaf) return;
    listScrollRaf = requestAnimationFrame(() => {
      listScrollRaf = 0;
      listScrollTop = el.scrollTop;
      triggerWindowLoad();
    });
  }

  // --- Data loading ---

  function handleSortChange(field: string, order: "asc" | "desc") {
    librarySortState.update((m) => ({ ...m, [activeTab]: { field, order } }));
    loadData(activeTab, true);
  }

  function evictFarItems(
    arr: (any | undefined)[],
    visStart: number,
    visEnd: number,
  ) {
    const keepStart = Math.max(0, visStart - evictionBuffer);
    const keepEnd = Math.min(arr.length - 1, visEnd + evictionBuffer);
    for (let i = 0; i < keepStart; i++) arr[i] = undefined;
    for (let i = keepEnd + 1; i < arr.length; i++) arr[i] = undefined;
  }

  async function loadChunk(
    tab: (typeof tabs)[number],
    startIndex: number,
    endIndex: number,
  ) {
    if (loadingStarts.has(startIndex)) return;
    const count = get(libraryTotalCounts)[tab] ?? 0;
    if (startIndex >= count) return;

    loadingStarts.add(startIndex);
    const clampedEnd = Math.min(endIndex, count - 1);
    const sort = get(librarySortState)[tab] ?? { field: "title", order: "asc" };

    try {
      const res = await fetch(
        apiUrl(
          `/api/${tab.toLowerCase()}?sort_by=${sort.field}&sort_order=${sort.order}&start_index=${startIndex}&end_index=${clampedEnd}`,
        ),
      );
      if (!res.ok) throw new Error(`Request failed: ${res.status}`);
      const data = await res.json();

      libraryItemCache.update((cache) => {
        const arr = [...(cache[tab] ?? new Array(count).fill(undefined))];
        for (let i = 0; i < data.length; i++) arr[startIndex + i] = data[i];
        evictFarItems(arr, startIndex, clampedEnd);
        return { ...cache, [tab]: arr };
      });
    } catch (error) {
      console.error(
        `Failed to load items ${startIndex}-${endIndex} for ${tab}:`,
        error,
      );
    } finally {
      loadingStarts.delete(startIndex);
    }
  }

  function triggerWindowLoad() {
    const tab = activeTab as (typeof tabs)[number];
    const count = get(libraryTotalCounts)[tab] ?? 0;
    if (count === 0) return;
    const current = get(libraryItemCache)[tab] ?? [];
    const isGrid = get(libraryActiveView)[tab] !== "list";
    const start = isGrid ? gridStartIdx : listFirstIdx;
    const end = isGrid ? gridEndIdx : listLastIdx;

    for (let i = start; i <= end; i++) {
      if (current[i] === undefined) {
        const alignedStart = Math.floor(i / chunkSize) * chunkSize;
        loadChunk(tab, alignedStart, alignedStart + chunkSize - 1);
        break;
      }
    }
  }

  async function loadData(tab: (typeof tabs)[number], force = false) {
    libraryActiveTab.set(tab);
    loadError = "";

    const hasCount = (get(libraryTotalCounts)[tab] ?? 0) > 0;
    const hasItems = (get(libraryItemCache)[tab]?.length ?? 0) > 0;

    if (!force && hasCount && hasItems) {
      // Cache hit — caller handles scroll (tab switch → top, mount → restore).
      return;
    }

    isLoading = true;
    loadingStarts = new Set();

    try {
      const countRes = await fetch(apiUrl(`/api/${tab.toLowerCase()}/count`));
      if (!countRes.ok)
        throw new Error(`Count request failed: ${countRes.status}`);
      const { count } = await countRes.json();
      libraryTotalCounts.update((m) => ({ ...m, [tab]: count }));

      libraryItemCache.update((m) => ({
        ...m,
        [tab]: new Array(count).fill(undefined),
      }));

      await loadChunk(tab, 0, Math.max(chunkSize, 100) - 1);
    } catch (error) {
      loadError =
        "Backend unavailable. Start the backend dev server and refresh.";
      console.error(`Failed to load ${tab.toLowerCase()}:`, error);
    } finally {
      isLoading = false;
    }
  }

  function handleTabClick(tab: (typeof tabs)[number]) {
    if (tab === activeTab) {
      const container = activeScrollContainer();
      if (container) container.scrollTop = 0;
      return;
    }
    const newIdx = tabs.indexOf(tab);
    const curIdx = tabs.indexOf(activeTab as (typeof tabs)[number]);
    const cls = newIdx > curIdx ? "slide-in-left" : "slide-in-right";

    loadData(tab)
      .then(() => tick())
      .then(() => {
        // Tab switches always start at the top.
        const container = activeScrollContainer();
        if (container) container.scrollTop = 0;
        if (!contentWrapper) return;
        const wrapper = contentWrapper;
        wrapper.classList.remove("slide-in-left", "slide-in-right");
        void wrapper.offsetWidth; // force reflow to restart animation
        wrapper.classList.add(cls);
        wrapper.addEventListener(
          "animationend",
          () => {
            wrapper.classList.remove("slide-in-left", "slide-in-right");
          },
          { once: true },
        );
      });
  }

  let showCreationModal = false;

  onMount(() => {
    loadData(activeTab).then(() => restoreScrollPosition(activeTab));
  });

  onDestroy(() => {
    saveScrollPosition();
    if (gridScrollRaf) cancelAnimationFrame(gridScrollRaf);
    if (listScrollRaf) cancelAnimationFrame(listScrollRaf);
  });
</script>

<ViewLayout>
  <div
    slot="toolbar"
    class="flex items-center justify-between w-full bg-zinc-900 border-b border-white/10 p-2 z-7000"
  >
    <div class="flex items-center">
      <IconButton on:click={() => loadData(activeTab, true)}>
        <IconRefresh size={16} />
      </IconButton>
    </div>

    <div
      class="absolute left-1/2 -translate-x-1/2 flex items-center gap-1 bg-white/5 rounded-full"
    >
      {#each tabs as tab}
        <button
          on:click={() => handleTabClick(tab)}
          class="px-3.5 py-1.5 rounded-full text-sm font-semibold transition border {activeTab ===
          tab
            ? 'bg-zinc-700 text-white shadow-lg border-white/10'
            : 'text-zinc-500 hover:text-white hover:bg-white/5 border-transparent'}"
        >
          <div class="flex items-center gap-2">
            {#if tabicons[tabs.indexOf(tab)]}
              <svelte:component this={tabicons[tabs.indexOf(tab)]} size={16} />
            {/if}
            {tab}
          </div>
        </button>
      {/each}
    </div>

    <div class="flex items-center gap-2">
      {#if activeTab === "Playlists"}
        <IconButton text on:click={() => (showCreationModal = true)}>
          <IconPlus size={16} /> <span>New</span>
        </IconButton>
      {/if}
      <ContextMenu
        items={buildSortMenuItems(
          activeTab,
          $librarySortState[activeTab] ?? { field: "title", order: "asc" },
          handleSortChange,
        )}
        let:toggle
      >
        <IconButton
          on:click={(e) => toggle(e)}
          class="text-zinc-400 hover:text-white hover:backdrop-blur-xl hover:shadow-md"
        >
          <svelte:component
            this={$librarySortState[activeTab]?.order === "desc"
              ? IconSortDescending
              : IconSortAscending}
            size={16}
          />
        </IconButton>
      </ContextMenu>
      {#if activeTab !== "Tracks"}
        <div class="flex items-center rounded-full bg-white/5 overflow-hidden">
          <button
            on:click={() =>
              libraryActiveView.update((m) => ({ ...m, [activeTab]: "grid" }))}
            class="p-2 rounded-full border transition
            {$libraryActiveView[activeTab] === 'grid'
              ? 'bg-white/10 text-white border-white/10'
              : 'text-zinc-400 border-transparent hover:text-white hover:bg-white/5'}"
          >
            <IconLayoutGrid size={16} />
          </button>
          <button
            on:click={() =>
              libraryActiveView.update((m) => ({ ...m, [activeTab]: "list" }))}
            class="p-2 rounded-full border transition
            {$libraryActiveView[activeTab] === 'list'
              ? 'bg-white/10 text-white border-white/10'
              : 'text-zinc-400 border-transparent hover:text-white hover:bg-white/5'}"
          >
            <IconLayoutList size={16} />
          </button>
        </div>
      {/if}
    </div>
  </div>

  <div
    slot="content"
    bind:this={contentWrapper}
    class="w-full h-full flex flex-col min-h-0"
  >
    {#if loadError}
      <div class="px-4 pt-2 text-sm text-amber-300">{loadError}</div>
    {:else if isLoading}
      <Loading />
    {:else if $libraryActiveView[activeTab] === "grid"}
      <div
        bind:this={gridScrollContainer}
        bind:clientWidth={gridWidth}
        bind:clientHeight={gridViewportH}
        on:scroll={onGridScroll}
        class="flex-1 w-full h-full overflow-y-auto px-4 pt-4 pb-28"
      >
        <div
          style="height: {gridTotalHeight}px; width: 100%; position: relative;"
        >
          <div
            use:measureGrid
            style="position: absolute; top: 0; left: 0; width: 100%; transform: translateY({gridOffsetY}px); display: grid; gap: {GRID_GAP}px; grid-template-columns: repeat({gridCols}, minmax(0, 1fr));"
          >
            {#each gridVisible as item, i (gridStartIdx + i)}
              {#if item}
                <MediaCard
                  id={item.id}
                  title={item.name || item.title}
                  subtitle={activeTab === "Playlists"
                    ? `${item.track_count ?? 0} tracks`
                    : item.artist_name}
                  imageUrl={getImageUrl(item.id, 220, getCardType(activeTab))}
                  type={getCardType(activeTab) as
                    | "artist"
                    | "album"
                    | "playlist"}
                  coverAlbumIds={activeTab === "Playlists"
                    ? (item.first_album_ids ?? [])
                    : []}
                  subtitleHref={activeTab === "Albums" && item.artist_id
                    ? `/artist/${item.artist_id}`
                    : ""}
                />
              {:else}
                <div style="width:100%;height:{gridColWidth}px"></div>
              {/if}
            {/each}
          </div>
        </div>
      </div>
    {:else}
      {#if activeTab === "Tracks"}
        <div
          class="flex items-center gap-2 py-2 px-4 justify-between border-b border-white/10"
        >
          <span class="text-sm text-zinc-400">{totalCount} tracks</span>
          <div class="flex items-center gap-2">
            <IconButton
              white
              on:click={() => playTracks(getTrackIdsSlice(false), false)}
            >
              <IconPlayerPlayFilled size={16} />
            </IconButton>
            <IconButton
              text
              white
              on:click={() => playTracks(getTrackIdsSlice(true), true)}
              class="flex gap-2 items-center px-4"
            >
              <IconArrowsShuffle size={16} />
              <span>Shuffle All</span>
            </IconButton>
          </div>
        </div>
      {/if}
      <div class="flex-1 w-full min-h-0 flex flex-col">
        <div
          bind:this={scrollContainer}
          bind:clientHeight={listViewportH}
          on:scroll={onListScroll}
          class="flex-1 w-full h-full overflow-y-auto px-4 pt-4 pb-28"
        >
          <div
            style="height: {listTotalHeight}px; width: 100%; position: relative;"
          >
            <div
              use:measureList
              style="position: absolute; top: 0; left: 0; width: 100%; transform: translateY({listOffsetY}px);"
            >
              {#each listVisible as item, i (listFirstIdx + i)}
                {#if item}
                  <MediaRow
                    id={item.id}
                    album_id={item.album_id || ""}
                    title={item.name || item.title}
                    subtitle={item.artist_name}
                    imageUrl={getImageUrl(item.id, 220, getCardType(activeTab))}
                    type={getCardType(activeTab)}
                    duration={formatTime(item.duration_ms, true)}
                    rating={item.rating ?? 0}
                  />
                {:else}
                  <div style="height:{listRowHeight}px" class="mb-1"></div>
                {/if}
              {/each}
            </div>
          </div>
        </div>
      </div>
    {/if}
  </div>
</ViewLayout>

<PlaylistCreationModal
  bind:open={showCreationModal}
  onCreate={() => loadData("Playlists", true)}
  onCancel={() => (showCreationModal = false)}
/>
