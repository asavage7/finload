<script lang="ts">
    import ProgressBar from "$lib/components/player/ProgressBar.svelte";
    import CoverImage from "$lib/components/CoverImage.svelte";
    import { IconMusicQuestion } from "@tabler/icons-svelte";
    import { formatTime } from "$lib/utils/formatTime";
    import { getImageUrl, fetchAccentColors } from "$lib/utils/media";
    import type { QuizChoice } from "$lib/utils/quiz";

    export let elapsed: number = 0;
    export let timeLimit: number = 10;
    // The round is over: the clip has stopped and the answer is on show.
    export let revealed: boolean = false;
    export let answer: QuizChoice | null = null;

    // Null until the answer's artwork colors are in, which is also what keeps
    // the accent layer hidden while a round is still being guessed.
    let accentColors: string[] | null = null;

    async function loadAccent(choice: QuizChoice) {
        const colors = await fetchAccentColors("track", choice.id);
        // A slow fetch must not paint the previous answer's colors over a
        // round that has already moved on.
        if (!revealed || answer?.id !== choice.id) return;
        accentColors = colors.length > 0 ? colors : null;
    }

    $: if (revealed && answer) loadAccent(answer);
    else accentColors = null;

    $: shown = Math.min(elapsed, timeLimit);
    $: remaining = Math.max(0, timeLimit - elapsed);
</script>

<!-- border-color transitions on its own, so unlike the gradient it just needs
     the accent applied over the default border class. -->
<div
    class="relative flex items-center gap-2 md:gap-6 p-2 pr-8 shadow-xl rounded-xl overflow-hidden transition-colors duration-500 bg-gradient-to-br from-blue-900 to-purple-800"
>
    <!-- background-image can't be transitioned, so the answer's colors ride in
         on their own layer and cross-fade against the base gradient. -->
    <div
        class="absolute inset-0 pointer-events-none transition-opacity duration-500 {accentColors
            ? 'opacity-100'
            : 'opacity-0'}"
        style={accentColors
            ? `background-image: linear-gradient(to bottom right, ${accentColors[0]}, ${accentColors[2]});`
            : ""}
    ></div>

    <!-- Kept above the accent layer, which is absolutely positioned. -->
    <div class="relative h-32 w-32">
        {#if revealed && answer}
            <CoverImage
                src={getImageUrl(answer.id, 240, "track")}
                alt={answer.title}
                fallbackText={answer.title}
                class="w-full h-full rounded-lg shadow-lg"
            />
        {:else}
            <div
                class="w-full h-full rounded-lg border border-white/10 bg-black/30 flex items-center justify-center"
            >
                <IconMusicQuestion size={40} class="text-white" />
            </div>
        {/if}
    </div>

    <div class="relative flex-1 min-w-0">
        <div class="flex flex-col gap-1 h-20 justify-end">
            <div class="text-sm text-white/70">
                {revealed ? "The answer was" : "Now playing"}
            </div>
            <h2 class="text-xl md:text-3xl font-black text-white truncate">
                {revealed && answer ? answer.title : "What's this song?"}
            </h2>
            <div class="text-sm text-white/70 truncate">
                {#if revealed && answer}
                    {[answer.artist_name, answer.album_title]
                        .filter(Boolean)
                        .join(" ∙ ")}
                {/if}
            </div>
        </div>

        <!-- The bar reports the round clock, which is not something the player
             is allowed to scrub, so pointer events are dropped on it. -->
        <div class="mt-2 pointer-events-none">
            <ProgressBar
                value={revealed ? timeLimit : shown}
                max={timeLimit}
                accentColor="#ffffff"
            />
        </div>

        <div class="flex w-full items-center justify-between gap-2">
            <span class="text-xs text-white/60 shrink-0">
                {formatTime(shown)} / {formatTime(timeLimit)}
            </span>
            {#if !revealed && remaining <= 3}
                <span class="text-xs text-amber-300 font-semibold shrink-0">
                    {Math.ceil(remaining)}s left
                </span>
            {/if}
        </div>
    </div>
</div>
