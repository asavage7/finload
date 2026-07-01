<script lang="ts">
    import { createEventDispatcher } from "svelte";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import { getImageUrl } from "$lib/utils/media";
    import type { SearchResult } from "$lib/utils/search";

    export let open = false;
    export let anchor: HTMLElement | undefined = undefined;
    export let results: SearchResult[] = [];
    export let loading = false;
    export let query = "";
    export let activeIndex = -1;

    const dispatch = createEventDispatcher<{
        select: SearchResult;
        hover: number;
        close: void;
    }>();

    let popupEl: HTMLDivElement | null = null;

    const MARGIN = 8;
    // The search field is narrow; let the results box grow past it for legibility.
    const MIN_WIDTH = 380;

    // Anchor the box just below the search field, at least as wide as it and
    // clamped to the viewport. It closes on scroll/resize, so the anchor is
    // always stationary when this reads its rect.
    $: style = open && anchor ? positionUnder(anchor) : "";

    function positionUnder(el: HTMLElement): string {
        const r = el.getBoundingClientRect();
        const width = Math.min(Math.max(r.width, MIN_WIDTH), innerWidth - 2 * MARGIN);
        const left = Math.max(MARGIN, Math.min(r.left, innerWidth - width - MARGIN));
        const top = r.bottom + MARGIN;
        return `position:fixed; left:${left}px; top:${top}px; width:${width}px; max-height:${innerHeight - top - MARGIN}px;`;
    }

    function onWindowClick(e: MouseEvent) {
        const t = e.target as Node;
        if (open && !popupEl?.contains(t) && !anchor?.contains(t)) dispatch("close");
    }

    function portal(node: HTMLElement) {
        document.body.appendChild(node);
        return { destroy: () => node.remove() };
    }

    $: emptyMessage = loading
        ? "Searching…"
        : query.trim()
          ? `No results for "${query.trim()}"`
          : "";
</script>

<svelte:window
    on:click={onWindowClick}
    on:scroll|capture={() => open && dispatch("close")}
    on:resize={() => open && dispatch("close")}
/>

{#if open}
    <div
        use:portal
        bind:this={popupEl}
        {style}
        class="z-[2000] flex flex-col overflow-y-auto bg-zinc-800/75 backdrop-blur-xl border border-white/10 rounded-xl shadow-2xl p-1.5"
    >
        {#each results as result, i}
            <button
                type="button"
                on:click={() => dispatch("select", result)}
                on:mousemove={() => dispatch("hover", i)}
                class="flex gap-4 items-center p-1.5 pr-3 rounded-lg text-left transition {i ===
                activeIndex
                    ? 'bg-white/10'
                    : 'hover:bg-white/5'}"
            >
                <CoverImage
                    src={getImageUrl(result.image_id, 220, result.type === "artist" ? "artist" : undefined)}
                    alt={result.title}
                    fallbackText={result.title}
                    class="w-12 h-12 shrink-0 {result.type === 'artist' ? 'rounded-full' : 'rounded-md'}"
                />
                <div class="flex flex-col min-w-0 flex-1">
                    <span class="font-bold text-sm text-white truncate">{result.title}</span>
                    <span class="text-xs text-zinc-400 truncate">{result.subtitle}</span>
                </div>
            </button>
        {:else}
            {#if emptyMessage}
                <div class="px-3 py-4 text-sm text-zinc-400 text-center">{emptyMessage}</div>
            {/if}
        {/each}
    </div>
{/if}
