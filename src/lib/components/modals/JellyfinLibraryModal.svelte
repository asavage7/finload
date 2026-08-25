<script lang="ts">
    import Modal from "./Modal.svelte";
    import { apiUrl } from "$lib/backend";

    export let open = false;
    export let onClose: () => void = () => {
        open = false;
    };
    // Fired only after a successful save, distinct from onClose (which also
    // fires on Cancel) — lets callers advance a flow only when the user
    // actually confirmed a selection, not on every close.
    export let onSave: () => void = () => {};

    type Library = { id: string; name: string };

    let libraries: Library[] = [];
    let selected = new Set<string>();
    let loading = false;
    let saving = false;
    let error = "";
    let saveNote = "";
    // True when a previously-saved selection is still being backfilled by a
    // resync (jellyfin_library_ids_pending is set) -- browsing keeps showing
    // the old selection until that finishes, so surface that it's in progress.
    let applying = false;

    async function loadLibraries() {
        loading = true;
        error = "";
        try {
            const [librariesRes, settingsRes] = await Promise.all([
                fetch(apiUrl("/api/jellyfin/libraries")),
                fetch(apiUrl("/api/settings")),
            ]);
            if (!librariesRes.ok) throw new Error("Could not reach Jellyfin");
            libraries = await librariesRes.json();
            const settings = settingsRes.ok ? await settingsRes.json() : {};
            // A pending selection (still being backfilled by a resync) is
            // the more current one to show -- otherwise reopening the modal
            // mid-resync would look like the last save didn't take.
            const pending: string[] | null = settings.jellyfin_library_ids_pending ?? null;
            applying = pending !== null;
            const current: string[] = pending ?? settings.jellyfin_library_ids ?? [];
            selected = current.length
                ? new Set(current)
                : new Set(libraries.map((l) => l.id));
        } catch (e) {
            error = "Couldn't load libraries from Jellyfin.";
        } finally {
            loading = false;
        }
    }

    $: if (open) {
        saveNote = "";
        loadLibraries();
    }

    function toggle(id: string) {
        const next = new Set(selected);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        selected = next;
    }

    async function handleSave() {
        saving = true;
        error = "";
        try {
            // Selecting every library on the server is the same as leaving
            // the selection unset -- send [] so sync stays unscoped instead
            // of paying the per-library fan-out cost for no filtering benefit.
            const allSelected = libraries.every((l) => selected.has(l.id));
            const library_ids = allSelected ? [] : Array.from(selected);
            const res = await fetch(apiUrl("/api/jellyfin/libraries/select"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ library_ids }),
            });
            if (!res.ok) throw new Error("Save failed");
            const result = await res.json();
            saveNote = result.resync_started
                ? "Selection saved — applying now, see Library Sync above."
                : "Selection saved — a sync is already running, so it'll fully apply once that finishes.";
            open = false;
            onClose();
            onSave();
        } catch (e) {
            error = "Couldn't save your selection.";
        } finally {
            saving = false;
        }
    }

</script>

<Modal
    {open}
    {onClose}
    panelClass="bg-zinc-900 border border-white/10 rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4 flex flex-col gap-4"
>
    <div>
        <h2 class="text-lg font-bold text-white">Jellyfin Libraries</h2>
        <p class="text-xs text-zinc-500 mt-1">
            Choose which libraries to show.
        </p>
        {#if applying}
            <p class="text-xs text-blue-400 mt-1">
                Changes are still applying. Check library sync for progress.
            </p>
        {/if}
    </div>

    {#if loading}
        <div class="text-sm text-zinc-500 py-4 text-center">Loading libraries…</div>
    {:else if error}
        <div class="text-sm text-red-400 py-4 text-center">{error}</div>
    {:else if libraries.length === 0}
        <div class="text-sm text-zinc-500 py-4 text-center">No music libraries found.</div>
    {:else}
        <div class="flex flex-col gap-1 max-h-64 overflow-y-auto">
            {#each libraries as lib (lib.id)}
                <label
                    class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-white/5 cursor-pointer transition"
                >
                    <input
                        type="checkbox"
                        checked={selected.has(lib.id)}
                        on:change={() => toggle(lib.id)}
                        class="w-4 h-4 rounded border-white/20 bg-zinc-800 accent-blue-500"
                    />
                    <span class="text-sm text-white">{lib.name}</span>
                </label>
            {/each}
        </div>
    {/if}

    <div class="flex gap-3 justify-end pt-2">
        <button
            on:click={onClose}
            class="px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
        >
            Cancel
        </button>
        <button
            on:click={handleSave}
            disabled={loading || saving || libraries.length === 0}
            class="px-4 py-2 rounded-full text-sm font-semibold text-white bg-blue-500 hover:bg-blue-400 border border-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
            {saving ? "Saving…" : "Save"}
        </button>
    </div>
</Modal>
