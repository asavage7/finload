<script lang="ts">
    import Modal from "./Modal.svelte";
    import { confirmStore, resolveConfirm } from '$lib/store';
</script>

<Modal
    open={$confirmStore.open}
    onClose={() => resolveConfirm(false)}
    panelClass="bg-zinc-800 border border-white/10 rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4"
>
    <h2 class="text-lg font-bold text-white mb-2">{$confirmStore.title}</h2>
    {#if $confirmStore.message}
        <p class="text-sm text-zinc-400 mb-6">{$confirmStore.message}</p>
    {/if}
    <div class="flex gap-3 justify-end">
        <button
            on:click={() => resolveConfirm(false)}
            class="px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
        >
            {$confirmStore.cancelLabel}
        </button>
        <button
            on:click={() => resolveConfirm(true)}
            class="px-4 py-2 rounded-full text-sm font-semibold border transition {$confirmStore.destructive ? 'text-red-500 bg-red-500/10 hover:bg-red-500/20 border-red-500/10' : 'bg-blue-500 hover:bg-blue-400 text-white border-white/10'}"
        >
            {$confirmStore.confirmLabel}
        </button>
    </div>
</Modal>
