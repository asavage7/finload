<script lang="ts">
    import { IconStarFilled } from '@tabler/icons-svelte';
    export let rating: number = 0; // Expected to be between 0 and 5
    export let unrated_color: string = "#ffffff";
    export let rated_color: string = "#facc15";
    export let size: number = 16;
</script>

<div class="flex items-center gap-1">
    {#each Array(5) as _, i}
        {#if i < Math.round(rating)}
            <IconStarFilled size={size} style="color: {rated_color}" />
        {:else}
            <IconStarFilled size={size} style="color: {unrated_color}; opacity: 0.5" />
        {/if}
    {/each}
</div>