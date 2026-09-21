<script lang="ts">
    import Modal from "./Modal.svelte";
    import { marked } from "marked";
    import DOMPurify from "dompurify";
    import { openUrl } from "@tauri-apps/plugin-opener";
    import { apiUrl } from "$lib/backend";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import { IconX } from "@tabler/icons-svelte";
    import { showConfirm } from "$lib/store";

    let {
        open = $bindable(false),
        newVersion = "",
        releaseNotes = "",
        onClose,
    }: {
        open?: boolean;
        newVersion?: string;
        releaseNotes?: string;
        onClose?: () => void;
    } = $props();

    let html = $derived(
        releaseNotes
            ? DOMPurify.sanitize(
                  marked.parse(releaseNotes, { async: false }) as string,
              )
            : "",
    );

    function close() {
        sessionStorage.setItem("updateDismissed", "true");
        open = false;
        onClose?.();
    }

    async function updateSetting(
        setting: Record<string, boolean | string>,
        errorMessage: string,
    ) {
        try {
            const res = await fetch(apiUrl("/api/settings"), {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(setting),
            });
            if (!res.ok) throw new Error("Failed to update setting.");
            if (window.location.pathname.endsWith("/settings"))
                window.location.reload();
            close();
        } catch (e) {
            close();
            await showConfirm({
                title: "Error",
                message: errorMessage,
                allowCancel: false,
                confirmLabel: "OK",
            });
        }
    }
</script>

<Modal
    {open}
    onClose={close}
    panelClass="bg-zinc-800 border border-white/10 rounded-2xl shadow-2xl p-4 w-full max-w-xl mx-4"
>
    <div class="flex gap-4 justify-between mb-4 items-center">
        <h2 class="text-lg font-bold text-white ml-1">
            Update Available: {newVersion}
        </h2>
        <IconButton
            white
            onclick={close}
            aria-label="Close queue panel"
            class="bg-white/5 hover:bg-white/10"
        >
            <IconX size={16} />
        </IconButton>
    </div>
    {#if releaseNotes}
        <div
            class="markdown-content text-zinc-400 mb-6 px-4 py-2 max-h-96 overflow-y-auto border border-white/10 rounded-xl bg-zinc-900"
        >
            {@html html}
        </div>
    {/if}
    <div class="flex gap-3">
        <button
            onclick={() =>
                updateSetting(
                    { enable_update_check: false },
                    "Failed to disable auto-update.",
                )}
            class="px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
        >
            Don't Show Again
        </button>
        <button
            onclick={() =>
                updateSetting(
                    { ignored_update_version: newVersion },
                    "Failed to skip this version.",
                )}
            class="px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
        >
            Skip this Version
        </button>
        <div class="flex-1"></div>
        <button
            onclick={() =>
                openUrl("https://github.com/asavage7/finload/releases")}
            class="px-4 py-2 rounded-full text-sm font-semibold border transition bg-blue-500 hover:bg-blue-400 text-white border-white/10"
        >
            Go to Release Page
        </button>
    </div>
</Modal>

<style>
    :global(.markdown-content) {
        font-size: 0.875rem;
        line-height: 1.5;
    }

    :global(.markdown-content p) {
        margin: 0.5rem 0;
    }

    :global(.markdown-content ul),
    :global(.markdown-content ol) {
        margin: 0.5rem 0;
        padding-left: 1.5rem;
    }

    :global(.markdown-content ul) {
        list-style-type: disc;
    }

    :global(.markdown-content ol) {
        list-style-type: decimal;
    }

    :global(.markdown-content li) {
        margin: 0.25rem 0;
    }

    :global(.markdown-content h1),
    :global(.markdown-content h2),
    :global(.markdown-content h3),
    :global(.markdown-content h4) {
        margin: 1rem 0 0.5rem;
        color: white;
        font-weight: 700;
    }

    :global(.markdown-content h1) {
        font-size: 1.5rem;
    }
    :global(.markdown-content h2) {
        font-size: 1.25rem;
    }
    :global(.markdown-content h3) {
        font-size: 1.125rem;
    }

    :global(.markdown-content a) {
        color: rgb(96 165 250);
        text-decoration: underline;
    }

    :global(.markdown-content code) {
        border-radius: 0.25rem;
        background: rgba(255, 255, 255, 0.05);
        padding: 0.125rem 0.25rem;
    }

    :global(.markdown-content pre) {
        overflow-x: auto;
        margin: 0.75rem 0;
        border-radius: 0.5rem;
        background: rgba(255, 255, 255, 0.05);
        padding: 0.75rem;
    }

    :global(.markdown-content pre code) {
        background: transparent;
        padding: 0;
    }
</style>
