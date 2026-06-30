<script lang="ts">
    import { apiUrl } from "$lib/backend";
    import { playlistCoverTimestamps } from "$lib/store";
    import { IconCamera } from "@tabler/icons-svelte";
    import PlaylistCover from "$lib/components/PlaylistCover.svelte";

    function focusOnMount(node: HTMLElement) {
        node.focus();
    }

    export let open = false;
    export let edit = false;
    export let playlist: any = null;
    export let onCreate: (playlist: any) => void = () => {};
    export let onCancel: () => void = () => {
        open = false;
    };

    let name = "";
    let imageFile: File | null = null;
    let imagePreviewUrl = "";

    $: if (open && edit && playlist) {
        name = playlist.name ?? "";
    }

    $: if (!open) {
        name = "";
        imageFile = null;
        imagePreviewUrl = "";
    }

    function handleImageClick() {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = "image/*";
        input.onchange = (e) => {
            const file = (e.target as HTMLInputElement).files?.[0];
            if (file) {
                if (imageFile) URL.revokeObjectURL(imagePreviewUrl);
                imageFile = file;
                imagePreviewUrl = URL.createObjectURL(file);
            }
        };
        input.click();
    }

    async function handleSubmit() {
        if (!name.trim()) return;
        let result: any;
        if (edit && playlist) {
            const res = await fetch(apiUrl(`/api/playlist/${playlist.id}`), {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name.trim() }),
            });
            if (!res.ok) return;
            result = await res.json();
        } else {
            const res = await fetch(apiUrl("/api/playlists"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name.trim() }),
            });
            if (!res.ok) return;
            result = await res.json();
        }
        if (imageFile) {
            const form = new FormData();
            form.append("file", imageFile);
            await fetch(apiUrl(`/api/playlist/${result.id}/image`), {
                method: "POST",
                body: form,
            });
            playlistCoverTimestamps.update(m => ({ ...m, [result.id]: Date.now() }));
        }
        open = false;
        if (imageFile && imagePreviewUrl) { URL.revokeObjectURL(imagePreviewUrl); imagePreviewUrl = ""; }
        imageFile = null;
        onCreate(result);
    }

    function handleCancel() {
        if (imageFile && imagePreviewUrl) { URL.revokeObjectURL(imagePreviewUrl); imagePreviewUrl = ""; }
        imageFile = null;
        onCancel();
    }

    function handleBackdrop(e: MouseEvent) {
        if (e.target === e.currentTarget) handleCancel();
    }
</script>

{#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        on:click={handleBackdrop}
    >
        <div
            class="bg-zinc-900 border border-white/10 rounded-2xl shadow-2xl p-4 w-full max-w-md mx-8 flex flex-col md:flex-row items-center gap-6"
        >
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <div
                class="relative w-36 h-36 rounded-lg shadow-2xl border border-white/10 overflow-hidden bg-zinc-800 flex items-center justify-center group/cover cursor-pointer shrink-0"
                on:click={handleImageClick}
            >
                {#if imagePreviewUrl}
                    <img src={imagePreviewUrl} alt="" class="w-full h-full object-cover" />
                {:else}
                    <PlaylistCover playlistId={edit ? playlist?.id ?? "" : ""} name={name || ""} size={142} />
                {/if}
                <div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover/cover:opacity-100 transition-opacity duration-200 bg-black/50">
                    <IconCamera size={28} class="text-white" />
                </div>
            </div>

            <div class="flex flex-col gap-4 w-full flex-1 justify-center md:justify-start">
                <div class="w-full">
                    <h2 class="text-lg font-bold text-white mb-3 text-center md:text-left">
                        {edit ? "Edit Playlist" : "New Playlist"}
                    </h2>
                    <input
                        class="w-full bg-zinc-800 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-zinc-500 outline-none focus:border-white/30"
                        placeholder="Playlist name…"
                        bind:value={name}
                        on:keydown={(e) => e.key === "Enter" && handleSubmit()}
                        use:focusOnMount
                    />
                </div>

                <div class="flex gap-3 justify-end w-full">
                    <button
                        on:click={handleCancel}
                        class="px-4 py-2 rounded-full text-sm font-semibold text-white hover:bg-white/5 transition border border-white/10"
                    >
                        Cancel
                    </button>
                    <button
                        on:click={handleSubmit}
                        class="px-4 py-1.5 rounded-full text-sm font-semibold text-white bg-blue-500 hover:bg-blue-400 border border-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition shrink-0"
                        disabled={!name.trim()}
                    >
                        {edit ? "Save" : "Create"}
                    </button>
                </div>
            </div>
        </div>
    </div>
{/if}
