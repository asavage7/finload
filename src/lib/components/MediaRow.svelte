<script lang="ts">
    import {
        IconPlayerPlayFilled,
        IconMenu2Filled,
    } from "@tabler/icons-svelte";
    import { fade } from "svelte/transition";
    import Rating from "./Rating.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import CoverImage from "./CoverImage.svelte";
    import ContextMenu from "./ContextMenu.svelte";
    import { onMount } from "svelte";
    import { fetchAccentColors, getItemHref } from "$lib/utils/media";
    import {
        playAlbum,
        playTrackById,
        playTracksFrom,
        buildItemMenuItems,
    } from "$lib/utils/playback";

    export let id: string;
    export let album_id: string = "";
    export let title: string;
    export let subtitle: string = "";
    export let imageUrl: string = "";
    export let duration: string = "";
    export let type: "artist" | "album" | "playlist" | "track" = "track";
    export let rating: number = 0;
    // Hides the rating and duration (e.g. narrow carousel columns where
    // there isn't room for them).
    export let compact: boolean = false;
    // The ordered track ids this row belongs to (e.g. a whole "Because
    // you've been into X" carousel) — when set, playing this track queues
    // the rest of the list after it, same as clicking partway into an
    // album. Falls back to playing just this track when omitted.
    export let queueContext: string[] = [];
    // Turns the row into a selection target instead of a link: clicking
    // anywhere on it calls onSelect rather than navigating, and the play
    // overlay, rating, duration and context menu give way to the "trailing"
    // slot. For rows that stand for a choice rather than a place to go.
    export let selectable: boolean = false;
    export let onSelect: (id: string) => void = () => {};
    // Selection mode only: keeps the row rendered but no longer clickable.
    export let disabled: boolean = false;

    let accentColors: string[] = [
        "rgba(255,255,255,0.1)",
        "#rgba(255,255,255,0.5)",
        "#000000",
    ];

    onMount(async () => {
        await getAccentColor();
    });

    async function getAccentColor() {
        const colors = await fetchAccentColors(type, id);
        if (colors.length > 0) {
            accentColors = colors;
        }
    }

    function playItem() {
        if (type === "album") playAlbum(id);
        else if (type === "track") {
            if (queueContext.length > 0) playTracksFrom(queueContext, id);
            else playTrackById(id);
        }
    }

    function handleRowClick() {
        if (selectable && !disabled) onSelect(id);
    }

    function handleCoverClick(e: MouseEvent) {
        // In selection mode the whole row is a single target, so let the click
        // through to it instead of playing the track the cover depicts.
        if (selectable) return;
        e.preventDefault();
        e.stopPropagation();
        playItem();
    }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<svelte:element
    this={selectable ? "button" : "a"}
    href={selectable ? undefined : getItemHref(type, id, album_id)}
    type={selectable ? "button" : undefined}
    disabled={selectable && disabled ? true : undefined}
    on:click={handleRowClick}
    class="group flex gap-4 p-1.5 pr-3 rounded-xl hover:bg-white/5 transition duration-300 items-center min-w-0 w-full text-left {selectable &&
    disabled
        ? 'cursor-default'
        : 'cursor-pointer'}"
>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
        class="relative shrink-0 {selectable ? '' : 'cursor-pointer'}"
        on:click={handleCoverClick}
    >
        <CoverImage
            src={imageUrl}
            alt={title}
            fallbackText={title}
            class="w-12 h-12 shadow-md {type === 'artist'
                ? 'rounded-full'
                : 'rounded-md'}"
        >
            {#if type !== "artist" && !selectable}
                <div
                    transition:fade={{ duration: 100 }}
                    class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition flex items-center justify-center"
                >
                    <button
                        aria-label="Play {title}"
                        class="p-2 rounded-full flex items-center justify-center shadow-md border cursor-pointer text-white"
                        style="background-color: {accentColors[0]}; border-color: {accentColors[1]}33;"
                    >
                        <IconPlayerPlayFilled size={16} />
                    </button>
                </div>
            {/if}
        </CoverImage>
    </div>

    <div class="text-left my-1 flex-grow min-w-0">
        <div class="font-bold truncate w-full text-sm text-white">
            {title || "Unknown"}
        </div>
        {#if subtitle}
            <div class="text-xs text-zinc-400 truncate w-full">{subtitle}</div>
        {/if}
    </div>
    <div class="flex items-center gap-2 shrink-0">
        {#if selectable}
            <slot name="trailing" />
        {:else}
            {#if type !== "artist" && !compact}
                <Rating
                    class="hidden md:flex"
                    {id}
                    itemType={type === "track" || type === "album" ? type : ""}
                    {rating}
                    size={14}
                    rated_color={accentColors[1]}
                />

                <span class="w-14 pr-2 text-right text-xs text-zinc-500"
                    >{duration}</span
                >
            {/if}
            <ContextMenu
                items={buildItemMenuItems(
                    id,
                    type,
                    type === "track" ? album_id : "",
                )}
                let:toggle
            >
                <IconButton on:click={(e) => toggle(e)} aria-label="More options">
                    <IconMenu2Filled size={16} />
                </IconButton>
            </ContextMenu>
        {/if}
    </div>
</svelte:element>
