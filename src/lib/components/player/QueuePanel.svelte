<script lang="ts">
    import { onDestroy } from "svelte";
    import { playerState, queuePanelActive } from "$lib/store";
    import {
        IconMenu2Filled,
        IconTrashXFilled,
        IconX,
    } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { dispatch, buildQueueItemMenuItems } from "$lib/utils/playback";
    import { getImageUrl } from "$lib/utils/media";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import { apiUrl } from "$lib/backend";
    import ContextMenu from "$lib/components/ContextMenu.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";

    type LyricsLine = {
        time_ms: number;
        text: string;
    };

    type LyricsState =
        | { type: "none" }
        | { type: "unsynced"; text: string }
        | { type: "synced"; lines: LyricsLine[] };

    let lyrics: LyricsState = { type: "none" };
    let activeLyricIndex = -1;
    let loadingLyrics = false;
    let lyricsTrackId: string | null = null;
    let currentTrackId: string | null = null;

    let lyricsContainer: HTMLDivElement;
    let lyricElements: HTMLButtonElement[] = [];

    // Synced-lyric highlighting is driven entirely by each line's stored
    // `time_ms` — no polling clock. The backend sends time_pos roughly once per
    // second, so we anchor it to a wall-clock timestamp, extrapolate the current
    // position between updates, and schedule a single timeout for the *next* line
    // boundary. The timer only runs while synced lyrics are visible and playing.
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

    function loadData(tab: string) {
        activeTab = tab;
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

    let tabs = ["Queue", "Lyrics"];
    let activeTab = "Queue";

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
</script>

<div class="flex flex-col h-full w-full">
    <div class="mx-2.5 flex gap-2 items-center my-1">
        <div
            class="flex gap-1 rounded-full flex-1 my-2 bg-white/5 rounded-full"
        >
            {#each tabs as tab}
                <button
                    on:click={() => loadData(tab)}
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
            class="bg-white/5"
        >
            <IconX size={16} />
        </IconButton>
    </div>
    {#if activeTab === "Queue"}
        <div class="overflow-y-auto px-2 pb-4 flex-1">
            {#each $playerState.queue as item}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    on:click={() => jumpToQueueItem(item.id)}
                    class="group flex items-center w-full gap-3 mb-1 p-1 pr-2 rounded-xl border border-transparent {item
                        .track.id === $playerState.current_track?.id
                        ? 'bg-white/5 border-white/10'
                        : 'bg-transparent hover:bg-white/5'} cursor-default transition"
                >
                    <CoverImage
                        src={getImageUrl(item.track.album_id ?? "default", 220)}
                        alt={item.track.title}
                        fallbackText={item.track.album_name || item.track.title}
                        class="w-11 h-11 rounded-lg shrink-0"
                    />
                    <div
                        class="flex flex-col justify-center min-w-0 flex-1"
                    >
                        <div class="text-sm font-bold truncate">
                            {item.track.title}
                        </div>
                        <div class="text-xs text-zinc-400 truncate">
                            {item.track.artist_name} ∙ {item.track.album_name}
                        </div>
                    </div>
                    <div
                        class="group relative flex items-center group w-8 h-8 justify-center rounded-md"
                    >
                        <div
                            class="time-text group-hover:opacity-0 transition-opacity duration-200 text-right text-zinc-400 text-xs"
                        >
                            {formatTime(item.track.runtime || 0, true)}
                        </div>
                        <ContextMenu
                            items={buildQueueItemMenuItems(item.id)}
                            let:toggle
                        >
                            <IconButton
                                on:click={toggle}
                                class="absolute opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                            >
                                <IconMenu2Filled size={16} />
                            </IconButton>
                        </ContextMenu>
                    </div>
                </div>
            {/each}
        </div>
        <div class="flex items-center gap-3 p-2 border-t border-white/10">
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
            <IconButton destructive on:click={clearQueue}>
                <IconTrashXFilled size={16} />
            </IconButton>
        </div>
    {:else if activeTab === "History"}
        <div class="p-4 text-sm text-zinc-400">History content goes here.</div>
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
                bind:this={lyricsContainer}
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
