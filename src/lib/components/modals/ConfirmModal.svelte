<script lang="ts">
    import { confirmStore, resolveConfirm } from '$lib/store';

    function handleBackdrop(e: MouseEvent) {
        if (e.target === e.currentTarget) resolveConfirm(false);
    }
</script>

{#if $confirmStore.open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        on:click={handleBackdrop}
    >
        <div class="bg-zinc-800 border border-white/10 rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4">
            <h2 class="text-lg font-bold text-white mb-2">{$confirmStore.title}</h2>
            {#if $confirmStore.message}
                <p class="text-sm text-zinc-400 mb-6">{$confirmStore.message}</p>
            {/if}
            <div class="flex gap-3 justify-end">
                <button
                    on:click={() => resolveConfirm(false)}
                    class="px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
                >
                    Cancel
                </button>
                <button
                    on:click={() => resolveConfirm(true)}
                    class="px-4 py-2 rounded-full text-sm font-semibold border transition {$confirmStore.destructive ? 'text-red-500 bg-red-500/10 hover:bg-red-500/20 border-red-500/10' : 'bg-blue-500 hover:bg-blue-400 text-white border-white/10'}"
                >
                    {$confirmStore.confirmLabel}
                </button>
            </div>
        </div>
    </div>
{/if}
