<script lang="ts">
    import { playlistPickerStore } from "$lib/store";
    import { apiUrl } from "$lib/backend";
    import PlaylistCover from "$lib/components/PlaylistCover.svelte";
    import PlaylistCreationModal from "$lib/components/modals/PlaylistCreationModal.svelte";
    import { IconPlus, IconX } from "@tabler/icons-svelte";

    let playlists: any[] = [];
    let loading = false;
    let showCreationModal = false;

    $: if ($playlistPickerStore.open) {
        loadPlaylists();
    }

    async function loadPlaylists() {
        loading = true;
        try {
            const res = await fetch(apiUrl("/api/playlists"));
            playlists = res.ok ? await res.json() : [];
        } finally {
            loading = false;
        }
    }

    async function selectPlaylist(playlistId: string) {
        await fetch(apiUrl(`/api/playlist/${playlistId}/tracks`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ track_ids: $playlistPickerStore.trackIds }),
        });
        close();
    }

    function close() {
        playlistPickerStore.set({ open: false, trackIds: [] });
        showCreationModal = false;
    }

    function handleBackdrop(e: MouseEvent) {
        if (e.target === e.currentTarget) close();
    }
</script>

<PlaylistCreationModal
    bind:open={showCreationModal}
    onCreate={(pl) => selectPlaylist(pl.id)}
    onCancel={() => (showCreationModal = false)}
/>

{#if $playlistPickerStore.open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="fixed inset-0 z-[9998] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        on:click={handleBackdrop}
    >
        <div
            class="bg-zinc-800 border border-white/10 rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
        >
            <div class="flex items-center justify-between px-5 pt-5 pb-3">
                <h2 class="text-base font-bold text-white">Add to Playlist</h2>
                <button
                    on:click={close}
                    class="p-1 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 transition"
                >
                    <IconX size={18} />
                </button>
            </div>

            {#if loading}
                <div class="px-5 pb-5 text-sm text-zinc-500">Loading...</div>
            {:else}
                <div class="max-h-72 overflow-y-auto">
                    {#each playlists as pl}
                        <button
                            on:click={() => selectPlaylist(pl.id)}
                            class="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition text-left"
                        >
                            <div
                                class="w-10 h-10 bg-zinc-700 rounded-md overflow-hidden flex-shrink-0 border border-white/10"
                            >
                                <PlaylistCover
                                    playlistId={pl.id}
                                    name={pl.name}
                                    size={48}
                                />
                            </div>
                            <div class="min-w-0">
                                <div
                                    class="text-sm font-semibold text-white truncate"
                                >
                                    {pl.name}
                                </div>
                                <div class="text-xs text-zinc-500">
                                    {pl.track_count} tracks
                                </div>
                            </div>
                        </button>
                    {/each}
                </div>

                <div class="px-4 py-3 border-t border-white/10">
                    <button
                        on:click={() => (showCreationModal = true)}
                        class="w-full flex items-center gap-2 text-sm text-zinc-400 hover:text-white transition"
                    >
                        <IconPlus size={16} />
                        New Playlist
                    </button>
                </div>
            {/if}
        </div>
    </div>
{/if}
