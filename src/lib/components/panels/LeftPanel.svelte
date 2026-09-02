<script lang="ts">
    import { tick, onDestroy } from "svelte";
    import { page } from "$app/stores";
    import { goto } from "$app/navigation";
    import { openUrl } from "@tauri-apps/plugin-opener";
    import { leftPanelCondensed } from "$lib/store";
    import { getItemHref } from "$lib/utils/media";
    import { searchLibrary, type SearchResult } from "$lib/utils/search";
    import SearchResults from "$lib/components/panels/SearchResults.svelte";
    import {
        IconSearch,
        IconHome,
        IconHomeFilled,
        IconLibrary,
        IconLibraryFilled,
        IconLayoutSidebarLeftCollapse,
        IconLayoutSidebarLeftExpand,
        IconSettings,
        IconSettingsFilled,
        IconHeart,
        IconHeartFilled,
    } from "@tabler/icons-svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";

    let searchEl: HTMLInputElement | undefined;
    let searchAnchor: HTMLDivElement | undefined;
    let searchValue = "";

    // Search state
    let results: SearchResult[] = [];
    let loading = false;
    let popupOpen = false;
    let activeIndex = -1;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const SEARCH_DEBOUNCE_MS = 180;

    type NavItem = {
        label: string;
        href: string;
        icon: any;
        activeIcon: any;
        // A spacer before the item pushes it (and everything after) to the bottom.
        bottom?: boolean;
        external?: boolean; // Open via the system browser rather than navigating in-app.
    };

    const navItems: NavItem[] = [
        { label: "Home", href: "/", icon: IconHome, activeIcon: IconHomeFilled },
        { label: "Library", href: "/library", icon: IconLibrary, activeIcon: IconLibraryFilled },
        { label: "Support Finload", href: "https://github.com/asavage7/finload", icon: IconHeart, activeIcon: IconHeartFilled, bottom: true, external: true },
        { label: "Settings", href: "/settings", icon: IconSettings, activeIcon: IconSettingsFilled },
    ];

    function onNavClick(item: NavItem, event: MouseEvent) {
        if (!item.external) return;
        event.preventDefault();
        void openUrl(item.href);
    }

    $: condensed = $leftPanelCondensed;
    // Home only matches "/" exactly; everything else matches by prefix.
    $: isActive = (href: string) =>
        href === "/"
            ? $page.url.pathname === "/"
            : $page.url.pathname.startsWith(href);

    // Condensing hides the input, so any open results popup must go with it.
    $: if (condensed) popupOpen = false;

    // Debounce a fetch on every query change; empty query tears everything down.
    $: scheduleSearch(searchValue);
    function scheduleSearch(value: string) {
        if (debounceTimer) clearTimeout(debounceTimer);
        const q = value.trim();
        if (!q) {
            controller?.abort();
            results = [];
            loading = false;
            activeIndex = -1;
            popupOpen = false;
            return;
        }
        popupOpen = true;
        loading = true;
        debounceTimer = setTimeout(() => runSearch(q), SEARCH_DEBOUNCE_MS);
    }

    async function runSearch(q: string) {
        controller?.abort();
        controller = new AbortController();
        try {
            results = await searchLibrary(q, controller.signal);
            activeIndex = results.length ? 0 : -1;
            loading = false;
        } catch (e) {
            if ((e as Error).name === "AbortError") return; // superseded
            console.error("Search failed:", e);
            results = [];
            loading = false;
        }
    }

    function selectResult(result: SearchResult) {
        popupOpen = false;
        searchValue = "";
        results = [];
        searchEl?.blur();
        goto(getItemHref(result.type, result.id, result.album_id ?? undefined));
    }

    function onKeydown(e: KeyboardEvent) {
        const n = results.length;
        if (e.key === "Escape") {
            popupOpen = false;
            searchEl?.blur();
        } else if (e.key === "Enter") {
            const r = results[activeIndex] ?? results[0];
            if (r) {
                e.preventDefault();
                selectResult(r);
            }
        } else if (n && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            activeIndex =
                (activeIndex + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
        }
    }

    async function expandAndFocusSearch() {
        if (condensed) {
            leftPanelCondensed.set(false);
            // The input only exists in the expanded layout, so wait for it to
            // mount before moving focus to it.
            await tick();
            searchEl?.focus();
        }
    }

    onDestroy(() => {
        if (debounceTimer) clearTimeout(debounceTimer);
        controller?.abort();
    });
</script>

<div class="flex flex-col h-full w-full p-2 gap-2">
    <!-- Header: condense toggle -->
    <div
        class="flex items-center justify-center px-1 h-9 shrink-0"
    >
        {#if !condensed}
        <img src="favicon.png" alt="Finload" class="h-6 w-auto" />
            <span class="font-bold text-md text-white/90 pl-3 w-full">Finload</span>
        {/if}
        <IconButton
            white
            on:click={() => leftPanelCondensed.update((c) => !c)}
            aria-label={condensed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!condensed}
            class="bg-white/5"
        >
            {#if condensed}
                <IconLayoutSidebarLeftExpand size={16} />
            {:else}
                <IconLayoutSidebarLeftCollapse size={16} />
            {/if}
        </IconButton>
    </div>

    <!-- Search -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        bind:this={searchAnchor}
        on:click={expandAndFocusSearch}
        class="flex items-center gap-2 px-2 {condensed
            ? 'py-2 hover:bg-white/5 cursor-pointer border-transparent'
            : 'py-1.5 bg-white/5 border-white/10'} rounded-full border focus-within:border-white/10 transition"
    >
        <IconSearch size={16} class="text-zinc-400 shrink-0" />
        {#if !condensed}
            <input
                bind:this={searchEl}
                bind:value={searchValue}
                on:keydown={onKeydown}
                on:focus={() => {
                    if (searchValue.trim()) popupOpen = true;
                }}
                type="text"
                placeholder="Search"
                autocomplete="off"
                spellcheck="false"
                class="bg-transparent outline-none text-sm text-white placeholder:text-zinc-500 w-full min-w-0"
            />
        {/if}
    </div>

    <SearchResults
        open={popupOpen && !condensed}
        anchor={searchAnchor}
        {results}
        {loading}
        query={searchValue}
        {activeIndex}
        on:select={(e) => selectResult(e.detail)}
        on:hover={(e) => (activeIndex = e.detail)}
        on:close={() => (popupOpen = false)}
    />

    <!-- Navigation -->
    <nav class="flex flex-col gap-1 mt-1 flex-1">
        {#each navItems as item}
            {@const active = isActive(item.href)}
            {#if item.bottom}
                <div class="flex-1"></div>
            {/if}
            <a
                href={item.href}
                title={item.label}
                on:click={(e) => onNavClick(item, e)}
                class="flex items-center {condensed
                    ? 'py-2'
                    : 'gap-3 py-1.5'} px-2 rounded-lg border text-sm transition {active
                    ? 'bg-white/10 text-white border-white/10 font-semibold'
                    : 'text-zinc-400 hover:text-white hover:bg-white/5 border-transparent'}"
            >
                <svelte:component
                    this={active ? item.activeIcon : item.icon}
                    size={16}
                    class="shrink-0"
                />
                {#if !condensed}
                    <span class="truncate">{item.label}</span>
                {/if}
            </a>
        {/each}
    </nav>
</div>
