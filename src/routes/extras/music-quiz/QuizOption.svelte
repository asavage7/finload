<script lang="ts">
    import MediaRow from "$lib/components/MediaRow.svelte";
    import { getImageUrl } from "$lib/utils/media";
    import { IconCheck, IconX } from "@tabler/icons-svelte";
    import type { QuizChoice } from "$lib/utils/quiz";

    export let choice: QuizChoice;
    export let onSelect: (choice: QuizChoice) => void = () => {};
    export let disabled: boolean = false;
    // Set once a round is revealed: "correct" marks the real answer, "wrong"
    // marks what the player picked when it was not the answer.
    export let result: "" | "correct" | "wrong" = "";

    $: subtitle = [choice.artist_name, choice.album_title]
        .filter(Boolean)
        .join(" ∙ ");

    $: frameClass =
        result === "correct"
            ? "border-green-500/40 bg-green-500/10"
            : result === "wrong"
              ? "border-red-500/40 bg-red-500/10"
              : "border-white/10 bg-white/5 hover:border-white/20";
</script>

<div class="rounded-xl border transition {frameClass}">
    <MediaRow
        selectable
        {disabled}
        onSelect={() => onSelect(choice)}
        id={choice.id}
        album_id={choice.album_id}
        title={choice.title}
        {subtitle}
        imageUrl={getImageUrl(choice.id, 240, "track")}
        type="track"
    >
        <svelte:fragment slot="trailing">
            {#if result === "correct"}
                <IconCheck size={18} class="text-green-400" />
            {:else if result === "wrong"}
                <IconX size={18} class="text-red-400" />
            {/if}
        </svelte:fragment>
    </MediaRow>
</div>
