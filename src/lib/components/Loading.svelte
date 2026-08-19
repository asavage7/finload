<script lang="ts">
    import { IconLoader2 } from "@tabler/icons-svelte";
    import { onMount } from "svelte";
    let visible = false;
    onMount(() => {
        const t = setTimeout(() => { visible = true; }, 150);
        return () => clearTimeout(t);
    });
</script>

{#if visible}
<div class="w-full h-full flex items-center justify-center gap-4 text-zinc-500">
    <IconLoader2 size={32} class="animate-spin" />
    <span class="text-lg font-medium">Loading...</span>
</div>
{/if}