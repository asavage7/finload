<script lang="ts">
    import { playerState } from "$lib/store";
    import { IconXFilled, IconTrashXFilled } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { apiUrl } from "$lib/backend";

    function dispatch(action: string, value?: unknown) {
        window.dispatchEvent(
            new CustomEvent<{ action: string; value?: unknown }>("player-command", {
                detail: { action, value },
            }),
        );
    }

    function loadData(tab: string) {
        activeTab = tab;
    }

    function jumpToQueueItem(queue_item_id: string | number) {
        dispatch("jump_to_queue_item", queue_item_id);
    }

    let tabs = ["Queue", "History", "Lyrics"];
    let activeTab = "Queue";
</script>

<div class="flex flex-col h-full w-full">
    <div
        class="mx-4 mt-4 mb-2 flex gap-1 bg-zinc-900/80 p-1 rounded-full border border-white/5"
    >
        {#each tabs as tab}
            <button
                on:click={() => loadData(tab)}
                class="px-4 py-1.5 flex-1 rounded-full text-sm font-semibold transition {activeTab ===
                tab
                    ? 'bg-zinc-800 text-white shadow-lg border border-white/10'
                    : 'text-zinc-500 hover:text-white hover:bg-white/5'}"
            >
                {tab}
            </button>
        {/each}
    </div>
    <div
        class="px-4 py-2 flex items-center justify-between gap-2 border-b border-white/10"
    >
        <h1 class="text-lg font-bold">Queue</h1>
        <div class="flex items-center gap-2">
            <span class="text-sm text-zinc-400"
                >{$playerState.queue.length}
                {$playerState.queue.length === 1 ? "track" : "tracks"} • {formatTime(
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
    <div class="overflow-y-auto p-4">
        {#each $playerState.queue as item}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <div
                on:click={() => jumpToQueueItem(item.id)}
                class="group flex items-center w-full gap-3 mb-1 p-2 rounded-lg group-hover:bg-white/5 cursor-default transition"
                style="background-color: {item.track.id ===
                $playerState.current_track?.id
                    ? 'rgba(255, 255, 255, 0.1)'
                    : 'transparent'};"
            >
                <img
                    src={apiUrl(`/api/image/${item.track.album_id}?size=62`)}
                    alt={item.track.title}
                    class="w-8 h-8 object-cover rounded-sm border border-white/10 bg-zinc-800"
                />
                <div class="flex flex-col gap-0 justify-center min-w-0 flex-1">
                    <div class="text-sm font-bold truncate">
                        {item.track.title}
                    </div>
                    <div class="text-xs text-zinc-400 truncate">
                        {item.track.artist_name} • {item.track.album_name}
                    </div>
                </div>
                <div
                    class="group relative flex items-center group w-8 h-8 justify-center rounded-md"
                >
                    <div
                        class="group-hover:opacity-0 transition-opacity duration-200 text-right text-zinc-400 text-xs"
                    >
                        {formatTime(item.track.runtime || 0, true)}
                    </div>
                    <button
                        on:click|preventDefault|stopPropagation={() => dispatch("remove_from_queue", item.id)}
                        class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-white bg-white/10 rounded-full cursor-pointer"
                    >
                        <IconXFilled size={16} />
                    </button>
                </div>
            </div>
        {/each}
    </div>
</div>
