<script lang="ts">
    import { goto } from "$app/navigation";
    import { apiUrl } from "$lib/backend";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import { IconArrowRight, IconArrowBack } from "@tabler/icons-svelte";

    type Key =
        | "enable_genre_enrichment"
        | "enable_online_metadata"
        | "enable_lrclib_lyrics"
        | "enable_synced_lyrics"
        | "enable_radio";

    // Mirrors the settings-schema.json defaults (settings_manager.py)
    let values: Record<Key, boolean> = {
        enable_genre_enrichment: true,
        enable_online_metadata: true,
        enable_lrclib_lyrics: true,
        enable_synced_lyrics: true,
        enable_radio: true,
    };

    const TOGGLES: {
        key: Key;
        label: string;
        description: string;
        indent?: boolean;
        showIf?: Key;
    }[] = [
        {
            key: "enable_genre_enrichment",
            label: "Genre enrichment",
            description:
                "Look up additional genres from MusicBrainz and Last.fm",
        },
        {
            key: "enable_online_metadata",
            label: "Online metadata",
            description:
                "Fetch artist bios, images, and album descriptions from TheAudioDB",
        },
        {
            key: "enable_lrclib_lyrics",
            label: "Online Lyrics",
            description: "Fetch lyrics from lrclib.net when available",
        },
        {
            key: "enable_synced_lyrics",
            label: "Synced Lyrics",
            description: "Show synced lyrics when available",
            indent: true,
            showIf: "enable_lrclib_lyrics",
        },
        {
            key: "enable_radio",
            label: "Enable Radio",
            description:
                "Recommend tracks based on genre, audio analysis, and your listening habits",
        },
    ];

    function toggle(key: Key) {
        values = { ...values, [key]: !values[key] };
    }

    let submitting = false;

    async function finish() {
        if (submitting) return;
        submitting = true;
        await fetch(apiUrl("/api/settings"), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(values),
        });
        goto("/onboarding/sync");
    }
</script>

<div
    class="w-full h-full bg-zinc-900 flex flex-col items-center justify-center p-4"
>
    <div
        class="text-3xl font-bold text-white/80 flex items-center mb-8 flex gap-2"
    >
        <span>Privacy &amp; Data</span>
    </div>
    <div
        class="w-full max-w-2xl mx-auto bg-white/5 border border-white/10 rounded-xl overflow-hidden"
    >
        <div class="flex flex-col">
            {#each TOGGLES as t (t.key)}
                {#if !t.showIf || values[t.showIf]}
                    <div
                        class="flex items-center justify-between px-4 py-3 border-b border-white/10 last:border-b-0 {t.indent
                            ? 'pl-8'
                            : ''}"
                    >
                        <div class="pr-4">
                            <p class="text-sm font-medium text-white/90">
                                {t.label}
                            </p>
                            <p class="text-xs text-white/50">{t.description}</p>
                        </div>
                        <button
                            role="switch"
                            aria-checked={values[t.key]}
                            aria-label={t.label}
                            class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none {values[
                                t.key
                            ]
                                ? 'bg-blue-500'
                                : 'bg-zinc-700'}"
                            on:click={() => toggle(t.key)}
                        >
                            <span
                                class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 {values[
                                    t.key
                                ]
                                    ? 'translate-x-5'
                                    : 'translate-x-0'}"
                            ></span>
                        </button>
                    </div>
                {/if}
            {/each}
        </div>
        <div
            class="grid grid-cols-[auto_1fr_auto] gap-4 items-center p-3 border-t border-white/10"
        >
            <IconButton text white on:click={() => goto("/onboarding")}>
                <IconArrowBack size={16} />
                Back
            </IconButton>
            <span class="text-sm text-center truncate text-white/50"
                >You can change these anytime in Settings.</span
            >
            <IconButton text white on:click={finish} disabled={submitting}>
                <IconArrowRight size={16} />
                Next
            </IconButton>
        </div>
    </div>
</div>
