<script lang="ts">
    export let open = false;
    export let onClose: () => void = () => {
        open = false;
    };
    export let closeOnBackdrop = true;
    export let closeOnEscape = true;
    export let panelClass =
        "bg-zinc-900 border border-white/10 rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4";

    function handleBackdrop(e: MouseEvent) {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
    }

    function handleKeydown(e: KeyboardEvent) {
        if (open && closeOnEscape && e.key === "Escape") onClose();
    }
</script>

<svelte:window on:keydown={handleKeydown} />

{#if open}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        on:click={handleBackdrop}
    >
        <div class={panelClass}>
            <slot />
        </div>
    </div>
{/if}
