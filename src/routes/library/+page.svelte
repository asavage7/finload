<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { get } from "svelte/store";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import MediaCard from "$lib/components/MediaCard.svelte";
  import MediaRow from "$lib/components/MediaRow.svelte";
  import Loading from "$lib/components/Loading.svelte";
  import EmptyState from "$lib/components/EmptyState.svelte";
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
    IconPlugConnectedX,
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
  const viewOptions = [
    { view: "grid", Icon: IconLayoutGrid },
    { view: "list", Icon: IconLayoutList },
  ] as const;

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

  const cardTypeByTab: Record<
    string,
    "artist" | "album" | "playlist" | "track"
  > = {
    Artists: "artist",
    Playlists: "playlist",
    Tracks: "track",
    Albums: "album",
  };
  $: cardType = cardTypeByTab[activeTab] ?? "album";

  // --- Hand-rolled list windowing (single column, fixed-height rows) ---
  let listScrollTop = 0;
  let listViewportH = 0;
  let listRowHeight = 61; // seed; corrected by one measurement
  let listMeasured = false;
  const LIST_OVERSCAN = 6;

  // Chunk size scales with how many items fit on screen so we don't over/under-fetch.
  $: chunkSize = Math.max(
    100,
    (isGrid
      ? Math.ceil(gridViewportH / Math.max(gridRowHeight, 1)) *
        Math.max(gridCols, 1)
      : Math.ceil(listViewportH / Math.max(listRowHeight, 1))) * 2,
  );
  $: evictionBuffer = chunkSize * 3;

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

  $: minCardWidth = gridWidth >= 720 ? 200 : 140;
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

  let gridScrollTop = 0;
  let gridViewportH = 0;
  const OVERSCAN_ROWS = 2;

  function computeWindow(
    scrollTop: number,
    viewportH: number,
    rowHeight: number,
    cols: number,
    overscan: number,
    total: number,
  ) {
    const rowCount = Math.ceil(total / cols);
    const firstRow = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const lastRow = Math.min(
      Math.max(rowCount - 1, 0),
      Math.ceil((scrollTop + viewportH) / rowHeight) + overscan,
    );
    return {
      startIdx: firstRow * cols,
      endIdx: Math.min(total, (lastRow + 1) * cols),
      offsetY: firstRow * rowHeight,
      totalHeight: rowCount * rowHeight,
    };
  }

  $: isGrid = $libraryActiveView[activeTab] === "grid";
  $: win = computeWindow(
    isGrid ? gridScrollTop : listScrollTop,
    isGrid ? gridViewportH : listViewportH,
    isGrid ? gridRowHeight : listRowHeight,
    isGrid ? gridCols : 1,
    isGrid ? OVERSCAN_ROWS : LIST_OVERSCAN,
    totalCount,
  );
  $: visible = items.slice(win.startIdx, win.endIdx);
  // Re-check the loaded range whenever the window moves for any reason, not
  // just on scroll — e.g. gridViewportH/listViewportH only get their real
  // (non-zero) value after mount, which can grow the window past what the
  // initial chunk fetched.
  $: if (totalCount > 0) {
    win;
    triggerWindowLoad();
  }

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
    const isGridTab = $libraryActiveView[tab] === "grid";
    const container = isGridTab ? gridScrollContainer : scrollContainer;
    if (!container) return;
    container.scrollTop = saved;
    // Drive the virtualizer window from here directly instead of waiting on
    // the container's native scroll event to reach the RAF-throttled handler:
    // that round-trip isn't guaranteed to land before first paint, so the grid
    // can render its initial (top-of-list) window against a range the earlier
    // deep-scroll already evicted from the cache — a blank screen until the
    // user's own scroll happens to fix it up.
    if (isGridTab) gridScrollTop = saved;
    else listScrollTop = saved;
    triggerWindowLoad();
  }

  // RAF-throttled scroll: update the active view's scrollTop, then top up the
  // loaded window. Each handler owns its own pending-frame guard.
  function makeScrollHandler(apply: (top: number) => void) {
    let raf = 0;
    const handler = (e: Event) => {
      const el = e.currentTarget as HTMLElement;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        apply(el.scrollTop);
        triggerWindowLoad();
      });
    };
    return Object.assign(handler, {
      cancel: () => raf && cancelAnimationFrame(raf),
    });
  }

  // Persist on every (throttled) scroll so the store is always current. Relying
  // only on onDestroy is unreliable — the bound scroll-container ref can already
  // be torn down by the time the page unmounts, so the position never gets saved.
  function persistScroll(top: number) {
    libraryScrollTop.update((m) => ({ ...m, [activeTab]: top }));
  }

  const onGridScroll = makeScrollHandler((t) => {
    gridScrollTop = t;
    persistScroll(t);
  });
  const onListScroll = makeScrollHandler((t) => {
    listScrollTop = t;
    persistScroll(t);
  });

  // --- Data loading ---

  function handleSortChange(field: string, order: "asc" | "desc") {
    librarySortState.update((m) => ({ ...m, [activeTab]: { field, order } }));
    loadData(activeTab, true);
  }

  // Range of cache indices currently populated per tab. Eviction only walks
  // the previous range, so scroll cost stays proportional to the window size
  // instead of the total item count.
  let keepRanges: Record<string, [number, number]> = {};

  function evictOutside(
    arr: (any | undefined)[],
    tab: string,
    keepStart: number,
    keepEnd: number,
  ) {
    const prev = keepRanges[tab];
    if (prev) {
      for (let i = prev[0]; i <= prev[1]; i++) {
        if (i < keepStart || i > keepEnd) arr[i] = undefined;
      }
    }
    keepRanges[tab] = [keepStart, keepEnd];
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
    const sort = get(librarySortState)[tab];

    try {
      const res = await fetch(
        apiUrl(
          `/api/${tab.toLowerCase()}?sort_by=${sort.field}&sort_order=${sort.order}&start_index=${startIndex}&end_index=${clampedEnd}`,
        ),
      );
      if (!res.ok) throw new Error(`Request failed: ${res.status}`);
      const data = await res.json();

      // The array is mutated in place; update() still notifies subscribers.
      libraryItemCache.update((cache) => {
        const arr = cache[tab] ?? new Array(count).fill(undefined);
        for (let i = 0; i < data.length; i++) arr[startIndex + i] = data[i];
        evictOutside(
          arr,
          tab,
          Math.max(0, startIndex - evictionBuffer),
          Math.min(arr.length - 1, clampedEnd + evictionBuffer),
        );
        cache[tab] = arr;
        return cache;
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

    for (let i = win.startIdx; i < win.endIdx; i++) {
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
    delete keepRanges[tab];

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
    onGridScroll.cancel();
    onListScroll.cancel();
  });
</script>

<ViewLayout>
  <div
    slot="toolbar"
    class="flex items-center justify-between w-full bg-zinc-900 border-b border-white/10 p-2 z-7000"
  >
    <div class="flex items-center">
      <IconButton on:click={() => loadData(activeTab, true)} aria-label="Refresh library">
        <IconRefresh size={16} />
      </IconButton>
    </div>

    <div
      class="absolute left-1/2 -translate-x-1/2 flex items-center bg-white/5 rounded-full"
    >
      {#each tabs as tab, i}
        <button
          on:click={() => handleTabClick(tab)}
          class="px-3.5 py-1.5 rounded-full text-sm font-semibold transition border {activeTab ===
          tab
            ? 'bg-zinc-700 text-white shadow-lg border-white/10'
            : 'text-zinc-400 hover:text-white hover:bg-white/5 border-transparent'}"
        >
          <div class="flex items-center gap-2">
            <svelte:component this={tabicons[i]} size={16} />
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
          $librarySortState[activeTab],
          handleSortChange,
        )}
        let:toggle
      >
        <IconButton
          on:click={(e) => toggle(e)}
          aria-label="Sort"
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
          {#each viewOptions as { view, Icon }}
            <button
              on:click={() =>
                libraryActiveView.update((m) => ({ ...m, [activeTab]: view }))}
              class="p-2 rounded-full border transition
              {$libraryActiveView[activeTab] === view
                ? 'bg-white/10 text-white border-white/10'
                : 'text-zinc-400 border-transparent hover:text-white hover:bg-white/5'}"
            >
              <svelte:component this={Icon} size={16} />
            </button>
          {/each}
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
      <EmptyState
        variant="error"
        icon={IconPlugConnectedX}
        title="Couldn't load your library."
        message={loadError}
      />
    {:else if isLoading}
      <Loading />
    {:else if totalCount === 0}
      <EmptyState
        icon={tabicons[tabs.indexOf(activeTab)]}
        title={`No ${activeTab.toLowerCase()} yet.`}
        message="Items you add to your library will show up here."
      />
    {:else if $libraryActiveView[activeTab] === "grid"}
      <div
        bind:this={gridScrollContainer}
        bind:clientWidth={gridWidth}
        bind:clientHeight={gridViewportH}
        on:scroll={onGridScroll}
        class="flex-1 w-full h-full overflow-y-auto px-4 pt-4 pb-28"
      >
        <div
          style="height: {win.totalHeight}px; width: 100%; position: relative;"
        >
          <div
            use:measureGrid
            style="position: absolute; top: 0; left: 0; width: 100%; transform: translateY({win.offsetY}px); display: grid; gap: {GRID_GAP}px; grid-template-columns: repeat({gridCols}, minmax(0, 1fr));"
          >
            {#each visible as item, i (win.startIdx + i)}
              {#if item}
                <MediaCard
                  id={item.id}
                  title={item.name || item.title}
                  subtitle={activeTab === "Playlists"
                    ? `${item.track_count ?? 0} tracks`
                    : item.artist_name}
                  imageUrl={getImageUrl(item.id, 240, cardType)}
                  type={cardType as "artist" | "album" | "playlist"}
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
          class="flex items-center gap-2 py-2 pl-4 pr-2 justify-between border-b border-white/10"
        >
          <span class="text-sm text-zinc-400">{totalCount} tracks</span>
          <div class="flex items-center gap-2">
            <IconButton
              white
              on:click={() => playTracks(getTrackIdsSlice(false), false)}
              aria-label="Play all"
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
            style="height: {win.totalHeight}px; width: 100%; position: relative;"
          >
            <div
              use:measureList
              style="position: absolute; top: 0; left: 0; width: 100%; transform: translateY({win.offsetY}px);"
            >
              {#each visible as item, i (win.startIdx + i)}
                {#if item}
                  <MediaRow
                    id={item.id}
                    album_id={item.album_id || ""}
                    title={item.name || item.title}
                    subtitle={item.artist_name}
                    imageUrl={getImageUrl(item.id, 240, cardType)}
                    type={cardType}
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
