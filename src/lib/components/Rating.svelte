<script lang="ts">
    import { IconStarFilled } from "@tabler/icons-svelte";
    import { fetchRating, updateRating } from "$lib/utils/rating";
    import { ratingOverrides } from "$lib/store";

    export let id: string = "";
    export let itemType: "album" | "track" | "" = "";
    // Pass rating to skip the API fetch and seed with a known value.
    export let rating: number | undefined = undefined;
    export let unrated_color: string = "rgba(255,255,255,0.5)";
    export let rated_color: string = "#facc15";
    export let size: number = 16;
    export let onrate: ((rating: number) => void) | undefined = undefined;
    let cls = "";
    export { cls as class };

    let hovered: number = -1;

    $: interactive = !!(id && itemType);

    // ratingOverrides is the single source of truth — syncs all Rating instances
    // that share the same id without any parent needing to know about the store.
    $: currentRating =
        id && $ratingOverrides[id] !== undefined
            ? $ratingOverrides[id]
            : (rating ?? 0);

    // Seed ratingOverrides from the rating prop when a value is provided
    $: if (id && rating !== undefined && $ratingOverrides[id] === undefined) {
        ratingOverrides.update((r) => ({ ...r, [id]: rating as number }));
    }

    // Fetch from API when no seed value is available
    $: if (
        id &&
        itemType &&
        rating === undefined &&
        $ratingOverrides[id] === undefined
    ) {
        loadRating(id, itemType);
    }

    async function loadRating(itemId: string, type: string) {
        const r = await fetchRating(type as "album" | "track", itemId);
        ratingOverrides.update((overrides) => ({ ...overrides, [itemId]: r }));
    }

    $: displayRating = hovered >= 0 ? hovered + 1 : Math.round(currentRating);

    async function handleRate(i: number) {
        if (!interactive) return;
        const newRating = i + 1 === Math.round(currentRating) ? 0 : i + 1;
        ratingOverrides.update((r) => ({ ...r, [id]: newRating }));
        await updateRating(itemType as "album" | "track", id, newRating);
        onrate?.(newRating);
    }
</script>

<div class="flex items-center {cls}">
    {#each Array(5) as _, i}
        <!-- svelte-ignore a11y_interactive_supports_focus -->
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <span
            role={interactive ? "button" : undefined}
            class="px-0.5 py-0.5 {interactive
                ? 'cursor-pointer'
                : 'cursor-default'}"
            on:mouseenter={() => {
                if (interactive) hovered = i;
            }}
            on:mouseleave={() => {
                if (interactive) hovered = -1;
            }}
            on:click|preventDefault|stopPropagation={() => handleRate(i)}
        >
            {#if i < displayRating}
                <IconStarFilled {size} style="color: {rated_color}" />
            {:else}
                <IconStarFilled
                    {size}
                    style="color: {unrated_color}; opacity: 0.75"
                />
            {/if}
        </span>
    {/each}
</div>
