<script lang="ts">
    import { onDestroy } from "svelte";
    import { playerState, queuePanelActive } from "$lib/store";
    import {
        IconMenu2Filled,
        IconTrashXFilled,
        IconX,
        IconInfinity,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import {
        dispatch,
        buildQueueItemMenuItems,
        buildTrackMenuItems,
        playTrackById,
        setRadioEnabled,
    } from "$lib/utils/playback";
    import { getImageUrl } from "$lib/utils/media";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import { apiUrl } from "$lib/backend";
    import ContextMenu from "$lib/components/ContextMenu.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import { showConfirm } from "$lib/store";

    type LyricsLine = {
        time_ms: number;
        text: string;
    };

    type LyricsState =
        | { type: "none" }
        | { type: "unsynced"; text: string }
        | { type: "synced"; lines: LyricsLine[] };

    type HistoryEntry = {
        id: number;
        track_id: string;
        title: string;
        artist_name: string;
        artist_id: string | null;
        album_name: string;
        album_id: string | null;
        duration_ms: number;
        played_at: string;
    };

    let lyrics: LyricsState = { type: "none" };
    let activeLyricIndex = -1;
    let loadingLyrics = false;
    let lyricsTrackId: string | null = null;
    let currentTrackId: string | null = null;

    let lyricElements: HTMLButtonElement[] = [];

    const LYRIC_LOOKAHEAD_MS = 300;
    let lyricTimer: ReturnType<typeof setTimeout> | null = null;
    let posAnchorSec = 0;
    let posAnchorAt = 0;
    let wasPaused = true;

    function currentPosSec(): number {
        if ($playerState.is_paused) return posAnchorSec;
        return posAnchorSec + (performance.now() - posAnchorAt) / 1000;
    }

    function activeIndexAt(posSec: number): number {
        if (lyrics.type !== "synced") return -1;
        const cutoff = posSec * 1000 + LYRIC_LOOKAHEAD_MS;
        let idx = -1;
        for (const line of lyrics.lines) {
            if (line.time_ms <= cutoff) idx++;
            else break;
        }
        return idx;
    }

    function scheduleLyricTick() {
        if (lyricTimer !== null) {
            clearTimeout(lyricTimer);
            lyricTimer = null;
        }
        if (activeTab !== "Lyrics" || lyrics.type !== "synced") return;

        const pos = currentPosSec();
        activeLyricIndex = activeIndexAt(pos);
        if ($playerState.is_paused) return;

        const next = lyrics.lines[activeLyricIndex + 1];
        if (!next) return; // last line reached — nothing more to schedule

        const delayMs = next.time_ms - LYRIC_LOOKAHEAD_MS - pos * 1000;
        lyricTimer = setTimeout(scheduleLyricTick, Math.max(delayMs, 16));
    }

    onDestroy(() => {
        if (lyricTimer !== null) clearTimeout(lyricTimer);
    });

    $: if (lyrics.type === "synced" && activeLyricIndex >= 0) {
        lyricElements[activeLyricIndex]?.scrollIntoView({
            behavior: "smooth",
            block: "center",
        });
    }

    async function getLyrics(trackId: string) {
        loadingLyrics = true;
        lyricsTrackId = trackId;

        try {
            const response = await fetch(
                apiUrl(`/api/track/${trackId}/lyrics`),
            );
            if (!response.ok) {
                throw new Error(
                    `Lyrics request failed with ${response.status}`,
                );
            }

            lyrics = await response.json();
        } catch (error) {
            lyrics = { type: "none" };
            console.error("Failed to load lyrics:", error);
        } finally {
            loadingLyrics = false;
        }
    }

    function jumpToQueueItem(queue_item_id: string | number) {
        dispatch("jump_to_queue_item", queue_item_id);
    }

    function clearQueue() {
        playerState.update((s) => ({ ...s, queue: [] }));
        dispatch("clear_queue");
    }

    // --- Play history ---

    let historyItems: HistoryEntry[] = [];
    let loadingHistory = false;

    async function loadHistory() {
        loadingHistory = true;
        try {
            const res = await fetch(apiUrl("/api/history"));
            historyItems = res.ok ? await res.json() : [];
        } catch {
            historyItems = [];
        } finally {
            loadingHistory = false;
        }
    }

    function removeHistoryEntry(entryId: number) {
        historyItems = historyItems.filter((e) => e.id !== entryId);
        fetch(apiUrl(`/api/history/${entryId}`), { method: "DELETE" }).catch(
            () => {},
        );
    }

    async function clearHistory() {
        const confirmed = await showConfirm({
            title: "Clear History",
            message:
                "Are you sure you want to clear your play history? This will affect your recommendations and cannot be undone.",
            confirmLabel: "Clear History",
            destructive: true,
        });
        if (!confirmed) return;

        historyItems = [];
        fetch(apiUrl("/api/history"), { method: "DELETE" }).catch(() => {});
    }

    function historyMenuItems(entry: HistoryEntry) {
        return [
            ...buildTrackMenuItems(entry.track_id),
            { divider: true },
            {
                label: "Remove from History",
                icon: IconTrashXFilled,
                destructive: true,
                action: () => removeHistoryEntry(entry.id),
            },
        ];
    }

    function timeAgo(iso: string): string {
        const seconds = Math.max(
            0,
            (Date.now() - new Date(iso).getTime()) / 1000,
        );
        if (seconds < 60) return "now";
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        return `${Math.floor(seconds / 86400)}d ago`;
    }

    const tabs = ["Queue", "History", "Lyrics"];
    let activeTab = "Queue";

    function setTab(tab: string) {
        activeTab = tab;
        if (tab === "History") loadHistory();
    }

    $: currentTrackId =
        $playerState.current_track?.id == null
            ? null
            : String($playerState.current_track.id);

    $: if (currentTrackId !== lyricsTrackId) {
        lyrics = { type: "none" };
        lyricsTrackId = null;
        // Drop references to the old track's lyric buttons so the detached DOM
        // nodes can be garbage-collected instead of lingering in this array.
        lyricElements = [];
    }

    // Re-anchor and reschedule whenever the backend sends an authoritative
    // position. A seek also produces a time_pos update, so this covers seeking.
    $: anchorPosition($playerState.time_pos);
    function anchorPosition(timePos: number) {
        posAnchorSec = timePos;
        posAnchorAt = performance.now();
        scheduleLyricTick();
    }

    // Reschedule when pause state, the active tab, or the loaded lyrics change.
    $: reactToPlayback($playerState.is_paused, activeTab, lyrics);
    function reactToPlayback(
        paused: boolean,
        _tab: string,
        _lyrics: LyricsState,
    ) {
        if (paused && !wasPaused) {
            // play -> pause: freeze the extrapolated position
            posAnchorSec += (performance.now() - posAnchorAt) / 1000;
        } else if (!paused && wasPaused) {
            // pause -> play: restart extrapolation from the frozen anchor
            posAnchorAt = performance.now();
        }
        wasPaused = paused;
        scheduleLyricTick();
    }

    $: if (
        activeTab === "Lyrics" &&
        currentTrackId &&
        currentTrackId !== lyricsTrackId &&
        !loadingLyrics
    ) {
        void getLyrics(currentTrackId);
    }

    // Whether any queue item has queue_type === 2
    $: hasQueueType2 = $playerState.queue.some((it) => it.queue_type === 2);
</script>

<!-- Shared row layout for the queue and history lists. -->
{#snippet trackRow(
    imageId: string | null,
    title: string,
    artist: string,
    artisthref: string,
    album: string,
    albumhref: string,
    trailing: string,
    menuItems: any[],
    onSelect: () => void,
    highlighted: boolean,
)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        on:click={(e) => {
            if (!(e.target as HTMLElement).closest("a")) onSelect();
        }}
        class="group flex items-center w-full gap-3 mb-1 p-1 pr-2 rounded-xl border border-transparent {highlighted
            ? 'bg-white/5 border-white/10'
            : 'bg-transparent hover:bg-white/5'} cursor-default transition"
    >
        <CoverImage
            src={getImageUrl(imageId ?? "default", 240)}
            alt={title}
            fallbackText={title}
            class="w-11 h-11 rounded-lg shrink-0"
        />
        <div class="flex flex-col justify-center min-w-0 flex-1">
            <div class="text-sm font-bold truncate">{title}</div>
            <div class="text-xs text-zinc-400 truncate">
                <svelte:element
                    this={artisthref ? "a" : "span"}
                    href={artisthref || undefined}
                    class={artisthref ? "hover:underline hover:text-white" : ""}
                >{artist}</svelte:element>
                <span> ∙ </span>
                <svelte:element
                    this={albumhref ? "a" : "span"}
                    href={albumhref || undefined}
                    class={albumhref ? "hover:underline hover:text-white" : ""}
                >{album}</svelte:element>
            </div>
        </div>
        <div
            class="group relative flex items-center group shrink-0 h-8 min-w-8 justify-end rounded-md"
        >
            <div
                class="time-text group-hover:opacity-0 transition-opacity duration-200 text-right text-zinc-400 text-xs whitespace-nowrap pr-1"
            >
                {trailing}
            </div>
            <ContextMenu items={menuItems} let:toggle>
                <IconButton
                    on:click={toggle}
                    aria-label="Track options"
                    class="absolute right-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                >
                    <IconMenu2Filled size={16} />
                </IconButton>
            </ContextMenu>
        </div>
    </div>
{/snippet}

<div class="flex flex-col h-full w-full">
    <div class="mx-2.5 flex gap-2 items-center my-1">
        <div
            class="flex rounded-full flex-1 my-2 bg-white/5 rounded-full"
        >
            {#each tabs as tab}
                <button
                    on:click={() => setTab(tab)}
                    class="border px-2 py-1.5 flex-1 rounded-full text-sm font-semibold transition {activeTab ===
                    tab
                        ? 'bg-white/10 text-white shadow-lg border-white/10'
                        : 'text-zinc-400 hover:text-white hover:bg-white/5 border-transparent'}"
                >
                    {tab}
                </button>
            {/each}
        </div>
        <IconButton
            white
            on:click={() => queuePanelActive.set(false)}
            aria-label="Close queue panel"
            class="bg-white/5 hover:bg-white/10"
        >
            <IconX size={16} />
        </IconButton>
    </div>
    {#if activeTab === "Queue"}
        <div class="overflow-y-auto px-2 pb-1 flex-1 flex flex-col">
            {#each $playerState.queue as item, i (item.id)}
                {#if item.queue_type === 2 && (i === 0 || $playerState.queue[i - 1].queue_type !== 2)}
                    <div
                        class="mt-3 mb-2
                 border-b border-white/10 text-md font-bold text-white"
                    >Autoplay<p class="text-xs text-zinc-400 font-normal pb-1">New tracks will queue up as you listen</p></div>
                {/if}
                {@render trackRow(
                    item.track.album_id,
                    item.track.title,
                    item.track.artist_name,
                    item.track.artist_id
                        ? `/artist/${item.track.artist_id}`
                        : "",
                    item.track.album_name,
                    item.track.album_id
                        ? `/album/${item.track.album_id}`
                        : "",
                    formatTime(item.track.runtime || 0, true),
                    buildQueueItemMenuItems(item.id),
                    () => jumpToQueueItem(item.id),
                    item.is_current,
                )}
            {/each}
        </div>
        <div class="flex items-center gap-3 p-2 border-t border-white/10">
            <IconButton
                active={$playerState.radio_enabled}
                on:click={() => setRadioEnabled(!$playerState.radio_enabled)}
                title={$playerState.radio_enabled
                    ? "Infinite queue: on"
                    : "Infinite queue: off"}
            >
                <IconInfinity size={16} />
            </IconButton>
            <span class="time-text text-xs text-zinc-400 flex-1 text-right"
                >{$playerState.queue.length}
                {$playerState.queue.length === 1 ? "track" : "tracks"} ∙ {formatTime(
                    $playerState.queue.reduce(
                        (acc, item) => acc + (item.track.runtime || 0),
                        0,
                    ),
                    true,
                )}</span
            >
            <IconButton destructive on:click={clearQueue} aria-label="Clear queue">
                <IconTrashXFilled size={16} />
            </IconButton>
        </div>
    {:else if activeTab === "History"}
        <div class="overflow-y-auto px-2 pb-4 flex-1">
            {#if loadingHistory}
                <div class="p-4 text-sm text-zinc-400">Loading history...</div>
            {:else if historyItems.length === 0}
                <div class="p-4 text-sm text-zinc-400">Nothing played yet.</div>
            {:else}
                {#each historyItems as entry (entry.id)}
                    {@render trackRow(
                        entry.album_id,
                        entry.title,
                        entry.artist_name,
                        `${entry.artist_id ? `/artist/${entry.artist_id}` : ""}`,
                        entry.album_name,
                        `${entry.album_id ? `/album/${entry.album_id}` : ""}`,
                        timeAgo(entry.played_at),
                        historyMenuItems(entry),
                        () => playTrackById(entry.track_id),
                        false,
                    )}
                {/each}
            {/if}
        </div>
        {#if historyItems.length > 0}
            <div class="flex items-center gap-3 p-2 border-t border-white/10">
                <span class="time-text text-xs text-zinc-400 flex-1 text-right"
                    >{historyItems.length}
                    {historyItems.length === 1 ? "play" : "plays"}</span
                >
                <IconButton destructive on:click={clearHistory} aria-label="Clear history">
                    <IconTrashXFilled size={16} />
                </IconButton>
            </div>
        {/if}
    {:else if activeTab === "Lyrics"}
        {#if loadingLyrics}
            <div class="p-4 text-sm text-zinc-400">Searching for lyrics...</div>
        {:else if lyrics.type === "unsynced"}
            <div
                class="block w-full pt-1 pb-2 text-left cursor-pointer transition-all duration-200 transform-origin origin-top-left font-bold text-xl text-white overflow-y-auto flex-1 min-h-0"
            >
                {#each lyrics.text
                    .split("\n")
                    .filter((line) => line.trim()) as line}
                    <div class="py-2 scale-90">{line}</div>
                {/each}
            </div>
        {:else if lyrics.type === "synced"}
            <div
                class="p-4 text-2xl text-white/50 overflow-y-auto flex-1 min-h-0"
            >
                {#each lyrics.lines as line, index}
                    <button
                        type="button"
                        bind:this={lyricElements[index]}
                        on:click={() => dispatch("seek", line.time_ms / 1000)}
                        class="font-bold block w-full pt-1 pb-3 text-left cursor-pointer transition-all duration-200 transform-origin origin-top-left {index ===
                        activeLyricIndex
                            ? 'text-white'
                            : 'scale-90 text-white/50 hover:text-white'}"
                    >
                        {line.text}
                    </button>
                {/each}
            </div>
        {:else}
            <div
                class="p-4 text-sm text-zinc-400 overflow-y-auto flex-1 min-h-0"
            >
                No lyrics available.
            </div>
        {/if}
    {/if}
</div>
