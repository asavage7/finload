<script lang="ts">
    import { slideX } from "$lib/utils/transitions";
    import { fade } from "svelte/transition";
    export let side: "left" | "right" = "right";
    export let widthPx: number;
    export let duration = 150;
    // When overlaying, the panel floats above the content with a dimmed backdrop
    // instead of reserving layout space.
    export let overlay = false;
    export let onClose: () => void = () => {};
</script>

{#if overlay}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="absolute inset-0 z-1050 bg-black/50"
        transition:fade={{ duration }}
        on:click={onClose}
    ></div>
{/if}

<div
    transition:slideX={{ side, duration }}
    style="width: {widthPx}px;"
    class="absolute top-0 h-full p-2 overflow-hidden transition-[width] duration-150 ease-out {overlay
        ? 'z-1100'
        : 'z-10'} {side === 'left' ? 'left-0' : 'right-0'}"
>
    <div
        class="flex flex-col h-full w-full rounded-xl border border-white/5 overflow-hidden {overlay
            ? 'bg-zinc-800 shadow-2xl shadow-black/50'
            : 'bg-white/5'}"
    >
        <slot />
    </div>
</div>
