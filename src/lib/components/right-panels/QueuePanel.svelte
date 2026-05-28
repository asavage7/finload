<script lang="ts">
    import { onMount } from "svelte";
    import { playerState } from "$lib/store";
    import { IconXFilled, IconTrashXFilled } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";

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
    let lyricPlaybackPosition = 0;
    let lyricClockHandle: ReturnType<typeof setInterval> | null = null;

    let lyricsContainer: HTMLDivElement;
    let lyricElements: HTMLButtonElement[] = [];

    $: if (lyrics.type === "synced" && activeLyricIndex >= 0) {
        lyricElements[activeLyricIndex]?.scrollIntoView({
            behavior: "smooth",
            block: "center",
        });
    }

    function dispatch(action: string, value?: unknown) {
        window.dispatchEvent(
            new CustomEvent<{ action: string; value?: unknown }>(
                "player-command",
                {
                    detail: { action, value },
                },
            ),
        );
    }

    function loadData(tab: string) {
        activeTab = tab;
    }

    onMount(() => {
        lyricClockHandle = window.setInterval(() => {
            if (activeTab === "Lyrics" && lyrics.type === "synced") {
                if (!$playerState.is_paused) {
                    lyricPlaybackPosition += 0.1;
                }
            }
        }, 100);

        return () => {
            if (lyricClockHandle !== null) {
                clearInterval(lyricClockHandle);
            }
        };
    });

    async function getLyrics(trackId: string) {
        loadingLyrics = true;
        lyricsTrackId = trackId;

        try {
            const response = await fetch(
                apiUrl(`/api/album/${trackId}/lyrics`),
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

    let tabs = ["Queue", "History", "Lyrics"];
    let activeTab = "Queue";

    $: currentTrackId =
        $playerState.current_track?.id == null
            ? null
            : String($playerState.current_track.id);

    $: if (currentTrackId !== lyricsTrackId) {
        lyrics = { type: "none" };
        lyricsTrackId = null;
    }

    $: lyricPlaybackPosition = $playerState.time_pos;

    $: if (
        activeTab === "Lyrics" &&
        currentTrackId &&
        currentTrackId !== lyricsTrackId &&
        !loadingLyrics
    ) {
        void getLyrics(currentTrackId);
    }

    $: activeLyricIndex =
        lyrics.type === "synced"
            ? lyrics.lines.reduce(
                  (index, line, currentIndex) =>
                      line.time_ms <= lyricPlaybackPosition * 1000 + 300
                          ? currentIndex
                          : index,
                  -1,
              )
            : -1;
</script>

<div class="flex flex-col h-full w-full">
    <div class="m-4 flex gap-1 p-1 rounded-full border border-white/5">
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
    {#if activeTab === "Queue"}
        <div
            class="px-4 pb-2 flex items-center justify-between gap-2 h-12"
        >
            <h1 class="text-md font-bold">Queue</h1>
            <div class="flex items-center gap-2">
                <span class="time-text text-xs text-zinc-400"
                    >{$playerState.queue.length}
                    {$playerState.queue.length === 1 ? "track" : "tracks"} ∙ {formatTime(
                        $playerState.queue.reduce(
                            (acc, item) => acc + (item.track.runtime || 0),
                            0,
                        ),
                        true,
                    )}</span
                >
                <button
                    class="p-2 text-zinc-400 hover:text-red-400 hover:bg-red-400/10 rounded-full transition w-8 h-8"
                >
                    <IconTrashXFilled size={16} />
                </button>
            </div>
        </div>
        <div class="overflow-y-auto px-2 pb-4">
            {#each $playerState.queue as item}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    on:click={() => jumpToQueueItem(item.id)}
                    class="group flex items-center w-full gap-3 mb-1.5 p-1 pr-2 rounded-lg border border-transparent {item
                        .track.id === $playerState.current_track?.id
                        ? 'bg-white/10 border-white/10'
                        : 'bg-transparent hover:bg-white/5'} cursor-default transition"
                >
                    <img
                        src={apiUrl(
                            `/api/image/${item.track.album_id}?size=62`,
                        )}
                        alt={item.track.title}
                        class="w-11 h-11 object-cover rounded-md border border-white/10 bg-zinc-800"
                        loading="lazy"
                        decoding="async"
                    />
                    <div
                        class="flex flex-col gap-0 justify-center min-w-0 flex-1"
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
                        <button
                            on:click|preventDefault|stopPropagation={() =>
                                dispatch("remove_from_queue", item.id)}
                            class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-white bg-white/10 rounded-full cursor-pointer"
                        >
                            <IconXFilled size={16} />
                        </button>
                    </div>
                </div>
            {/each}
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
                            : 'scale-90 text-zinc-500 hover:text-white'}"
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
