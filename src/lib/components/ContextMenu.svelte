<script lang="ts">
    import { onMount } from "svelte";
    import { IconCheck } from "@tabler/icons-svelte";

    let open = false;
    let triggerRect: DOMRect | null = null;
    let menuEl: HTMLDivElement | null = null;

    export let items: {
        label?: string;
        icon?: any;
        action?: () => void;
        enabled?: boolean;
        destructive?: boolean;
        divider?: boolean;
        active?: boolean;
    }[] = [];

    export function toggle(e: MouseEvent) {
        e.preventDefault();
        e.stopPropagation();
        if (open) {
            open = false;
            return;
        }
        // Tell any other open context menus to close first.
        // This component also listens for that event and will close, which
        // is fine; we then proceed to open.
        window.dispatchEvent(new CustomEvent("close-all-context-menus"));
        // Anchor the menu to the trigger's on-screen position so it can be
        // rendered in a portal, free of any clipping/hover parent.
        triggerRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        open = true;
    }

    function close() {
        open = false;
    }

    function handleOutsideClick(e: MouseEvent) {
        if (open && menuEl && !menuEl.contains(e.target as Node)) {
            open = false;
        }
    }

    function handleItem(item: (typeof items)[0], e: MouseEvent) {
        e.preventDefault();
        e.stopPropagation();
        if (item.enabled !== false) {
            item.action?.();
        }
        open = false;
    }

    function portal(node: HTMLElement) {
        document.body.appendChild(node);
        return {
            destroy() {
                node.remove();
            },
        };
    }

    // Listen for global close requests so only one menu can be open at a time.
    function onCloseAll() {
        open = false;
    }

    onMount(() => {
        window.addEventListener("close-all-context-menus", onCloseAll as EventListener);
        return () => {
            window.removeEventListener("close-all-context-menus", onCloseAll as EventListener);
        };
    });

    const MENU_WIDTH = 180;
    const ITEM_HEIGHT = 40;
    const MARGIN = 8;

    // Fixed-position the menu near the trigger, flipping at viewport edges.
    $: menuStyle = computeMenuStyle(triggerRect, items);

    function computeMenuStyle(
        rect: DOMRect | null,
        list: typeof items,
    ): string {
        if (!rect || typeof window === "undefined") return "";

        const estHeight = list.reduce((h, i) => h + (i.divider ? 9 : ITEM_HEIGHT), 8);

        // Right-align to the trigger, clamped to the viewport.
        let left = rect.right - MENU_WIDTH;
        left = Math.max(
            MARGIN,
            Math.min(left, window.innerWidth - MENU_WIDTH - MARGIN),
        );

        // Prefer below the trigger; flip above if it would overflow the bottom.
        let top = rect.bottom + 4;
        if (top + estHeight > window.innerHeight - MARGIN) {
            const above = rect.top - estHeight - 4;
            top =
                above >= MARGIN
                    ? above
                    : Math.max(MARGIN, window.innerHeight - estHeight - MARGIN);
        }

        return `position: fixed; left: ${left}px; top: ${top}px; min-width: ${MENU_WIDTH}px;`;
    }
</script>

<svelte:window
    on:click={handleOutsideClick}
    on:scroll|capture={close}
    on:resize={close}
/>

<div style="display: contents;">
    <slot {toggle} />
</div>

{#if open}
    <div
        use:portal
        bind:this={menuEl}
        style={menuStyle}
        class="z-[2000] bg-zinc-800/75 backdrop-blur-xl border border-white/10 rounded-xl shadow-2xl overflow-hidden"
    >
        {#each items as item}
            {#if item.divider}
                <div class="border-t border-white/10"></div>
            {:else}
                <button
                    class="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition text-left disabled:opacity-40 disabled:cursor-not-allowed {item.destructive ? 'text-red-400 hover:bg-red-500/15 hover:text-red-300' : 'text-zinc-300 hover:bg-white/10 hover:text-white'}"
                    disabled={item.enabled === false}
                    on:click={(e) => handleItem(item, e)}
                >
                    {#if item.icon}
                        <svelte:component this={item.icon} size={16} />
                    {/if}
                    <span class="flex-1">{item.label}</span>
                    {#if item.active}
                        <IconCheck size={14} class="shrink-0 text-white" />
                    {/if}
                </button>
            {/if}
        {/each}
    </div>
{/if}
