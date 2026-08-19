<script lang="ts">
    import { onDestroy, tick } from "svelte";
    import { fly } from "svelte/transition";
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import Loading from "$lib/components/Loading.svelte";
    import EmptyState from "$lib/components/EmptyState.svelte";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import BackButton from "$lib/components/ui/BackButton.svelte";
    import QuizOption from "./QuizOption.svelte";
    import QuizOptionGroup from "./QuizOptionGroup.svelte";
    import QuizPlaybackBar from "./QuizPlaybackBar.svelte";
    import { showConfirm } from "$lib/store";
    import {
        startQuiz,
        nextRound,
        submitAnswer,
        stopQuiz,
        fetchSuggestions,
        QuizSessionLost,
        QUIZ_TIME_LIMITS,
        QUIZ_DEFAULT_TIME_LIMIT,
        type QuizAnswerStyle,
        type QuizStartPoint,
        type QuizChoice,
        type QuizRound,
        type QuizResult,
    } from "$lib/utils/quiz";
    import {
        IconList,
        IconKeyboard,
        IconPlayerTrackPrevFilled,
        IconArrowsShuffle,
        IconClock,
        IconRefresh,
        IconPlayerPlayFilled,
        IconChevronRight,
        IconSearch,
        IconAlertTriangle,
        IconMusicQuestion,
    } from "@tabler/icons-svelte";

    // Contextual accent for the page, matching the Music Quiz card on /extras.
    const QUIZ_ACCENT = ["#6d3fb5", "#c4b5fd", "#1e1b2e"];

    const TICK_MS = 100;
    const SUGGEST_DEBOUNCE_MS = 150;
    // How long the answer stays up before the next clip starts. Long enough to
    // read what was playing, short enough that the game keeps moving.
    const REVEAL_SECONDS = 4;

    type Phase = "setup" | "loading" | "playing" | "revealed";

    let phase: Phase = "setup";
    let error = "";

    let answerStyle: QuizAnswerStyle = "multiple_choice";
    let startPoint: QuizStartPoint = "beginning";
    let timeLimit = QUIZ_DEFAULT_TIME_LIMIT;

    let round: QuizRound | null = null;
    let result: QuizResult | null = null;
    let score = 0;
    let correctCount = 0;

    let elapsed = 0;
    let submitting = false;
    let ticker: ReturnType<typeof setInterval> | null = null;

    let revealRemaining = 0;
    let revealTimer: ReturnType<typeof setInterval> | null = null;

    // Open-ended rounds only.
    let guessText = "";
    let guessInput: HTMLInputElement | null = null;
    let suggestions: QuizChoice[] = [];
    let suggestTimer: ReturnType<typeof setTimeout> | null = null;
    // What the player actually picked, so a wrong guess can be shown next to
    // the right answer on reveal.
    let lastGuess: QuizChoice | null = null;

    // The toolbar stays put across the gap between rounds, so it keys off
    // having a round at all rather than off the phase.
    $: inGame = round !== null && phase !== "setup";
    $: roundTimeLimit = round?.time_limit ?? timeLimit;

    const answerStyleOptions = [
        { value: "multiple_choice", label: "Multiple Choice", icon: IconList },
        { value: "open_ended", label: "Open Ended", icon: IconKeyboard },
    ];
    const startPointOptions = [
        {
            value: "beginning",
            label: "Beginning",
            icon: IconPlayerTrackPrevFilled,
        },
        { value: "random", label: "Random Part", icon: IconArrowsShuffle },
    ];
    $: timeLimitOptions = QUIZ_TIME_LIMITS.map((seconds) => ({
        value: seconds,
        label: `${seconds}s`,
        icon: IconClock,
    }));

    // -- timers --------------------------------------------------------------

    function stopTicker() {
        if (ticker) clearInterval(ticker);
        ticker = null;
    }

    function startTicker() {
        stopTicker();
        ticker = setInterval(() => {
            if (phase !== "playing") return;
            elapsed = Math.min(roundTimeLimit, elapsed + TICK_MS / 1000);
            // Running out of time is an answer in itself: the round reveals and
            // moves on rather than waiting for a guess that isn't coming.
            if (elapsed >= roundTimeLimit) submitGuess({});
        }, TICK_MS);
    }

    function stopRevealTimer() {
        if (revealTimer) clearInterval(revealTimer);
        revealTimer = null;
    }

    function startRevealTimer() {
        stopRevealTimer();
        revealRemaining = REVEAL_SECONDS;
        revealTimer = setInterval(() => {
            revealRemaining = Math.max(0, revealRemaining - TICK_MS / 1000);
            if (revealRemaining <= 0) {
                stopRevealTimer();
                goNextRound();
            }
        }, TICK_MS);
    }

    // -- session -------------------------------------------------------------

    function handleFailure(e: unknown) {
        stopTicker();
        stopRevealTimer();
        phase = "setup";
        round = null;
        result = null;
        error =
            e instanceof QuizSessionLost
                ? "The quiz session ended. Start a new game to carry on."
                : "Couldn't reach the backend. Check it's running and try again.";
    }

    function beginRound() {
        result = null;
        lastGuess = null;
        guessText = "";
        suggestions = [];
        elapsed = 0;
        phase = "playing";
        startTicker();
        if (round?.answer_style === "open_ended") {
            tick().then(() => guessInput?.focus());
        }
    }

    async function startGame() {
        stopTicker();
        stopRevealTimer();
        phase = "loading";
        error = "";
        try {
            round = await startQuiz({
                answer_style: answerStyle,
                start_point: startPoint,
                time_limit: timeLimit,
            });
            score = round.score;
            correctCount = round.correct_count;
            beginRound();
        } catch (e) {
            handleFailure(e);
        }
    }

    async function goNextRound() {
        stopTicker();
        stopRevealTimer();
        // Cleared before the request so the last round's reveal doesn't sit on
        // screen while the next clip is loading.
        result = null;
        elapsed = 0;
        phase = "loading";
        try {
            round = await nextRound();
            beginRound();
        } catch (e) {
            handleFailure(e);
        }
    }

    async function confirmNewGame() {
        stopRevealTimer();
        const confirmed = await showConfirm({
            title: "Start a new game?",
            message:
                "All progress in the current game, including your score, will be lost.",
            confirmLabel: "New Game",
            destructive: true,
        });
        if (!confirmed) {
            // Resuming a countdown that ran during the dialog would leave a
            // fraction of a second to read the answer, so it starts over.
            if (phase === "revealed") startRevealTimer();
            return;
        }
        stopTicker();
        await stopQuiz();
        round = null;
        result = null;
        lastGuess = null;
        score = 0;
        correctCount = 0;
        elapsed = 0;
        error = "";
        phase = "setup";
    }

    // -- guessing ------------------------------------------------------------

    // Called with no choice and no text when the clock runs out, which grades
    // as a wrong answer rather than a skipped round.
    async function submitGuess(guess: { choice?: QuizChoice; text?: string }) {
        if (phase !== "playing" || submitting) return;
        submitting = true;
        stopTicker();
        try {
            result = await submitAnswer({
                trackId: guess.choice?.id,
                text: guess.text,
                elapsed,
            });
            lastGuess = guess.choice ?? null;
            score = result.score;
            correctCount = result.correct_count;
            phase = "revealed";
            startRevealTimer();
        } catch (e) {
            handleFailure(e);
        } finally {
            submitting = false;
        }
    }

    function scheduleSuggestions(value: string) {
        if (suggestTimer) clearTimeout(suggestTimer);
        const query = value.trim();
        if (!query) {
            suggestions = [];
            return;
        }
        suggestTimer = setTimeout(async () => {
            const results = await fetchSuggestions(query);
            // A slower earlier request must not overwrite the results for what
            // is now in the box.
            if (guessText.trim() === query) suggestions = results;
        }, SUGGEST_DEBOUNCE_MS);
    }

    $: if (phase === "playing" && round?.answer_style === "open_ended")
        scheduleSuggestions(guessText);

    function submitTypedGuess() {
        const text = guessText.trim();
        if (!text) return;
        // The top suggestion is what the player is looking at, so Enter commits
        // it; with nothing suggested the typed title is graded on its own.
        submitGuess({ choice: suggestions[0], text });
    }

    // Which frame an option wears once the round is revealed.
    function optionResult(
        choice: QuizChoice,
        current: QuizResult | null,
    ): "" | "correct" | "wrong" {
        if (!current) return "";
        if (choice.id === current.answer.id) return "correct";
        if (choice.id === current.selected_id) return "wrong";
        return "";
    }

    onDestroy(() => {
        stopTicker();
        stopRevealTimer();
        if (suggestTimer) clearTimeout(suggestTimer);
        stopQuiz();
    });
</script>

<ViewLayout accent={QUIZ_ACCENT}>
    <!-- The toolbar slot must be a direct child of ViewLayout, so the setup
         screen hides the bar rather than omitting it. -->
    <div
        slot="toolbar"
        class={inGame
            ? "flex items-center justify-between w-full bg-zinc-900 border-b border-white/10 p-2 z-7000"
            : "hidden"}
    >
        {#if inGame}
            <div class="flex items-baseline gap-2 pl-2">
                <span class="text-xs uppercase tracking-wide text-zinc-500"
                    >Score</span
                >
                <span class="text-lg font-bold text-white">{score}</span>
            </div>

            <div
                class="absolute left-1/2 -translate-x-1/2 text-sm font-semibold text-zinc-300"
            >
                Round {round?.round_number ?? 1}
                <span class="text-zinc-500">
                    ∙ Level {(round?.difficulty_level ?? 0) + 1}</span
                >
            </div>

            <IconButton text on:click={confirmNewGame}>
                <IconRefresh size={16} />
                <span>New Game</span>
            </IconButton>
        {/if}
    </div>

    <!-- The scroll area and the result banner are siblings rather than the
         banner sitting in the flow, so revealing a round lays it over the
         bottom of the page instead of pushing the answer options down it. -->
    <div slot="content" class="relative w-full h-full">
        <div class="w-full h-full overflow-y-auto pb-28">
            <div class="px-4 md:px-8 pt-10 max-w-[var(--8xl)] mx-auto">
                {#if phase === "setup"}
                    <BackButton class="-ml-3 mb-4" />
                    <h1 class="text-3xl font-bold">Music Quiz</h1>
                    <p class="text-zinc-400">
                        Name the song from a short clip. It gets harder the longer
                        you last.
                    </p>

                    {#if error}
                        <EmptyState
                            variant="error"
                            icon={IconAlertTriangle}
                            title="Couldn't start the quiz."
                            message={error}
                        />
                    {/if}

                    <div
                        class="mt-8 flex flex-col gap-6 p-6 rounded-xl border border-white/10 bg-white/5"
                    >
                        <QuizOptionGroup
                            label="Answer Style"
                            description="Pick from four options, or type the title and choose from suggestions."
                            options={answerStyleOptions}
                            value={answerStyle}
                            onChange={(v) => (answerStyle = v)}
                        />
                        <QuizOptionGroup
                            label="Starting Point"
                            description="Where in the track each clip starts playing."
                            options={startPointOptions}
                            value={startPoint}
                            onChange={(v) => (startPoint = v)}
                        />
                        <QuizOptionGroup
                            label="Time Limit"
                            description="How long you get to answer. One shot per round, so answer fast for more points."
                            options={timeLimitOptions}
                            value={timeLimit}
                            onChange={(v) => (timeLimit = v)}
                        />

                        <div class="flex items-center gap-3 pt-2">
                            <IconButton accent text on:click={startGame}>
                                <IconPlayerPlayFilled size={16} />
                                <span>Start Quiz</span>
                            </IconButton>
                            <span class="text-xs text-zinc-500">
                                Difficulty steps up every 10 rounds.
                            </span>
                        </div>
                    </div>
                {:else if phase === "loading"}
                    <Loading />
                {:else if round}
                    <QuizPlaybackBar
                        {elapsed}
                        timeLimit={roundTimeLimit}
                        revealed={phase === "revealed"}
                        answer={result?.answer ?? null}
                    />

                    {#if round.answer_style === "multiple_choice"}
                        <div class="mt-6 grid grid-cols-1 lg:grid-cols-2 gap-3">
                            {#each round.choices as choice (choice.id)}
                                <QuizOption
                                    {choice}
                                    disabled={phase !== "playing" || submitting}
                                    result={optionResult(choice, result)}
                                    onSelect={(picked) =>
                                        submitGuess({ choice: picked })}
                                />
                            {/each}
                        </div>
                    {:else}
                        <div class="mt-6 flex flex-col gap-3">
                            <div
                                class="flex items-center gap-2 px-3 py-2 bg-white/5 rounded-full border border-white/10 focus-within:border-white/20 transition"
                            >
                                <IconSearch
                                    size={16}
                                    class="text-zinc-400 shrink-0"
                                />
                                <input
                                    bind:this={guessInput}
                                    bind:value={guessText}
                                    on:keydown={(e) =>
                                        e.key === "Enter" && submitTypedGuess()}
                                    disabled={phase !== "playing"}
                                    type="text"
                                    placeholder="Type the song title..."
                                    autocomplete="off"
                                    spellcheck="false"
                                    class="bg-transparent outline-none text-sm text-white placeholder:text-zinc-500 w-full min-w-0 disabled:text-zinc-500"
                                />
                            </div>

                            {#if phase === "playing"}
                                {#each suggestions as choice (choice.id)}
                                    <QuizOption
                                        {choice}
                                        disabled={submitting}
                                        onSelect={(picked) =>
                                            submitGuess({ choice: picked })}
                                    />
                                {/each}
                                {#if guessText.trim() && suggestions.length === 0}
                                    <EmptyState
                                        icon={IconMusicQuestion}
                                        title="No matching tracks."
                                        message="Keep typing, or press Enter to lock in what you have."
                                    />
                                {/if}
                            {:else if result}
                                <QuizOption
                                    choice={result.answer}
                                    disabled
                                    result="correct"
                                />
                                {#if !result.correct && lastGuess && lastGuess.id !== result.answer.id}
                                    <QuizOption
                                        choice={lastGuess}
                                        disabled
                                        result="wrong"
                                    />
                                {/if}
                            {/if}
                        </div>
                    {/if}
                {/if}
            </div>
        </div>

        {#if phase === "revealed" && result}
            <!-- Lines up with the scroll area's own container so the banner
                 sits directly under the answer options, not off to one side. -->
            <div
                transition:fly={{ y: 16, duration: 150 }}
                class="absolute bottom-4 left-0 right-0 z-50 px-4 md:px-8 max-w-[var(--8xl)] mx-auto"
            >
                <div
                    class="flex items-center justify-between gap-4 flex-wrap p-4 rounded-2xl border shadow-lg {result.correct
                        ? 'border-green-500/40 bg-green-500/20'
                        : 'border-red-500/40 bg-red-500/20'}"
                >
                    <div>
                        <div
                            class="font-bold {result.correct
                                ? 'text-green-400'
                                : 'text-red-400'}"
                        >
                            {result.correct ? "Correct!" : "Not this time."}
                        </div>
                        <div class="text-sm text-zinc-400">
                            {result.correct
                                ? `+${result.points} points`
                                : "No points for this round."}
                            <span class="text-zinc-500">
                                ∙ {correctCount} right so far</span
                            >
                        </div>
                    </div>
                    <IconButton text on:click={goNextRound} class="text-white {result.correct
                        ? 'border-green-500/40 bg-green-700'
                        : 'border-red-500/40 bg-red-700'}">
                        <span class="text-white">Next Round in {Math.ceil(revealRemaining)}s</span>
                        <IconChevronRight class="text-white" size={16} />
                    </IconButton>
                </div>
            </div>
        {/if}
    </div>
</ViewLayout>
