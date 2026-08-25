<script lang="ts">
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { apiUrl } from "$lib/backend";
    import { open as openFolderDialog } from "@tauri-apps/plugin-dialog";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import JellyfinLibraryModal from "$lib/components/modals/JellyfinLibraryModal.svelte";
    import {
        IconPlugConnected,
        IconArrowRight,
        IconCheck,
        IconX,
        IconLoader2,
        IconFolder,
    } from "@tabler/icons-svelte";

    let librarySource: "jellyfin" | "local" = "jellyfin";
    let jellyfinUrl = "";
    let jellyfinUsername = "";
    let jellyfinPassword = "";
    let localMusicPath = "";

    type TestState = { status: "idle" | "testing" | "ok" | "error"; message: string };
    let testState: TestState = { status: "idle", message: "" };
    let testedValues = { url: "", username: "", password: "" };

    // Reset a stale "connection ok" once any tested field is edited.
    $: if (
        testState.status === "ok" &&
        (jellyfinUrl !== testedValues.url ||
            jellyfinUsername !== testedValues.username ||
            jellyfinPassword !== testedValues.password)
    ) {
        testState = { status: "idle", message: "" };
    }

    $: canContinue =
        librarySource === "jellyfin"
            ? testState.status === "ok"
            : localMusicPath.trim().length > 0;

    let submitting = false;
    let libraryModalOpen = false;

    async function fetchSettings() {
        try {
            const res = await fetch(apiUrl("/api/settings"));
            if (res.ok) {
                const s = await res.json();
                librarySource = s.library_source === "local" ? "local" : "jellyfin";
                jellyfinUrl = s.jellyfin_url ?? "";
                jellyfinUsername = s.jellyfin_username ?? "";
                jellyfinPassword = s.jellyfin_password ?? "";
                localMusicPath = s.local_music_path ?? "";
            }
        } catch {
            // backend unavailable
        }
    }

    function patchSettings(body: Record<string, unknown>) {
        return fetch(apiUrl("/api/settings"), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    }

    async function testConnection() {
        testState = { status: "testing", message: "" };
        try {
            const res = await fetch(apiUrl("/api/jellyfin/test"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jellyfin_url: jellyfinUrl,
                    jellyfin_username: jellyfinUsername,
                    jellyfin_password: jellyfinPassword,
                }),
            });
            const data = await res.json();
            testState = { status: data.ok ? "ok" : "error", message: data.message ?? "" };
            if (data.ok) {
                testedValues = { url: jellyfinUrl, username: jellyfinUsername, password: jellyfinPassword };
            }
        } catch {
            testState = { status: "error", message: "Could not reach backend" };
        }
    }

    async function browseFolder() {
        try {
            const selected = await openFolderDialog({
                directory: true,
                multiple: false,
                title: "Choose your music folder",
            });
            if (typeof selected === "string") localMusicPath = selected;
        } catch {
            // Not running under the Tauri webview — manual text input still works.
        }
    }

    // Kept off until the privacy page confirms real choices
    const SYNC_PRIVACY_DEFAULTS = {
        enable_radio: false,
        enable_genre_enrichment: false,
        enable_online_metadata: false,
    };

    async function onNext() {
        if (!canContinue || submitting) return;
        submitting = true;
        if (librarySource === "jellyfin") {
            await patchSettings({
                library_source: "jellyfin",
                jellyfin_url: jellyfinUrl,
                jellyfin_username: jellyfinUsername,
                jellyfin_password: jellyfinPassword,
                ...SYNC_PRIVACY_DEFAULTS,
            });
            submitting = false;
            libraryModalOpen = true;
        } else {
            await patchSettings({
                library_source: "local",
                local_music_path: localMusicPath,
                ...SYNC_PRIVACY_DEFAULTS,
            });
            await fetch(apiUrl("/api/jobs/sync/start"), { method: "POST" });
            submitting = false;
            goto("/onboarding/privacy");
        }
    }

    onMount(fetchSettings);
</script>

<div
    class="w-full h-full bg-zinc-900 flex flex-col items-center justify-center p-4"
>
    <div class="text-3xl font-bold text-white/80 flex items-center mb-8 flex gap-2">
        <img src="/favicon.png" alt="Finload Logo" class="w-12 h-12" /><span
            >Welcome to Finload</span
        >
    </div>
    <div
        class="w-full max-w-2xl mx-auto bg-white/5 border border-white/10 rounded-xl overflow-hidden"
    >
        <div class="flex border-b border-white/10 font-semibold">
            <button
                class="px-4 py-2 flex-1 transition {librarySource === 'jellyfin' ? 'text-white bg-white/5' : 'text-white/50'}"
                on:click={() => (librarySource = "jellyfin")}
                >Jellyfin</button
            >
            <button
                class="px-4 py-2 flex-1 transition {librarySource === 'local' ? 'text-white bg-white/5' : 'text-white/50'}"
                on:click={() => (librarySource = "local")}
                >Local folder</button
            >
        </div>
        {#if librarySource === "jellyfin"}
            <div class="p-4 my-2 flex flex-col gap-4 text-sm">
                <div class="flex-1 flex gap-4 justify-between items-center">
                    <span class="w-36 text-right">Jellyfin Server URL</span>
                    <input
                        type="text"
                        placeholder="https://jellyfin.example.com"
                        bind:value={jellyfinUrl}
                        class="flex-1 px-4 py-2 rounded-full bg-white/5 text-white/80"
                    />
                </div>
                <div class="flex-1 flex gap-4 justify-between items-center">
                    <span class="w-36 text-right">Username</span>
                    <input
                        type="text"
                        bind:value={jellyfinUsername}
                        class="flex-1 px-4 py-2 rounded-full bg-white/5 text-white/80"
                    />
                </div>
                <div class="flex-1 flex gap-4 justify-between items-center">
                    <span class="w-36 text-right">Password</span>
                    <input
                        type="password"
                        bind:value={jellyfinPassword}
                        class="flex-1 px-4 py-2 rounded-full bg-white/5 text-white/80"
                    />
                </div>
            </div>
            <div class="flex justify-end p-3 gap-4 align-center items-center border-t border-white/10">
                <span
                    class="flex-1 pl-2 text-sm truncate {testState.status === 'error' ? 'text-red-400' : testState.status === 'ok' ? 'text-emerald-400' : 'text-white/50'}"
                    >{testState.message}</span
                >
                <IconButton
                    text
                    white
                    on:click={testConnection}
                    disabled={testState.status === "testing" || !jellyfinUrl || !jellyfinUsername || !jellyfinPassword}
                >
                    {#if testState.status === "testing"}
                        <IconLoader2 size={16} class="animate-spin" />
                    {:else if testState.status === "ok"}
                        <IconCheck size={16} />
                    {:else if testState.status === "error"}
                        <IconX size={16} />
                    {:else}
                        <IconPlugConnected size={16} />
                    {/if}
                    Test Connection
                </IconButton>
                <IconButton
                    text
                    white
                    on:click={onNext}
                    disabled={!canContinue || submitting}
                >
                    <IconArrowRight size={16} />
                    Next
                </IconButton>
            </div>
        {:else}
            <div class="p-4 my-2 flex flex-col gap-4 text-sm">
                <div class="flex-1 flex gap-4 justify-between items-center">
                    <span class="w-36 text-right">Music folder</span>
                    <input
                        type="text"
                        placeholder="/home/user/Music"
                        bind:value={localMusicPath}
                        class="flex-1 px-4 py-2 rounded-full bg-white/5 text-white/80"
                    />
                    <IconButton text white on:click={browseFolder}>
                        <IconFolder size={16} />
                        Browse
                    </IconButton>
                </div>
            </div>
            <div class="flex justify-end p-3 gap-4 align-center items-center border-t border-white/10">
                <span class="flex-1 pl-2 text-sm truncate text-white/50"
                    >Finload will watch this folder for your music.</span
                >
                <IconButton
                    text
                    white
                    on:click={onNext}
                    disabled={!canContinue || submitting}
                >
                    <IconArrowRight size={16} />
                    Next
                </IconButton>
            </div>
        {/if}
    </div>
</div>

<JellyfinLibraryModal
    bind:open={libraryModalOpen}
    onClose={() => (libraryModalOpen = false)}
    onSave={() => goto("/onboarding/privacy")}
/>
