<script lang="ts">
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { apiUrl } from "$lib/backend";
    import { onboardingComplete } from "$lib/store";
    import IconButton from "$lib/components/ui/IconButton.svelte";
    import { IconArrowRight, IconArrowBack } from "@tabler/icons-svelte";
    import { subscribeJobStatus, startJob, type JobState } from "$lib/utils/backgroundJobs";

    let jobState: JobState = {
        status: "running",
        message: "",
        processed: 0,
        total: 0,
    };
    $: syncing = jobState.status !== "complete";
    $: progress =
        jobState.total > 0
            ? Math.min(100, (jobState.processed / jobState.total) * 100)
            : 0;

    onMount(() => {
        const unsubscribe = subscribeJobStatus("sync", (state) => {
            jobState = state;
        });

        void startJob("sync", true);
        return unsubscribe;
    });

    async function finish() {
        await fetch(apiUrl("/api/settings"), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ onboarding_complete: true }),
        });
        onboardingComplete.set(true);
        goto("/library?refresh");
    }
</script>

<div
    class="w-full h-full bg-zinc-900 flex flex-col items-center justify-center p-4"
>
    <div
        class="text-3xl font-bold text-white/80 flex items-center mb-8 flex gap-2"
    >
        {#if syncing}
            <span>Syncing your library...</span>
        {:else}
            <span>Sync complete!</span>
        {/if}
    </div>
    <div
        class="w-full max-w-2xl mx-auto bg-white/5 border border-white/10 rounded-xl overflow-hidden"
    >
        <div class="flex flex-col h-16">
            <div class="flex-1 flex items-center justify-center text-white/50">
                <div class="w-full max-w-md px-6">
                    {#if jobState.message}
                        <div class="mb-2 text-sm text-white/50 text-center truncate">
                            {jobState.message}
                        </div>
                    {/if}
                    <div
                        class="h-2 w-full overflow-hidden rounded-full bg-white/10"
                    >
                        <div
                            class="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden"
                        >
                            <div
                                class="h-full bg-blue-500 transition-all duration-300"
                                style="width: {jobState.total > 0
                                    ? progress
                                    : 100}%"
                            ></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <div
            class="grid grid-cols-[auto_1fr_auto] gap-4 items-center p-3 border-t border-white/10"
        >
            <IconButton text white on:click={() => goto("/onboarding/privacy")}>
                <IconArrowBack size={16} />
                Back
            </IconButton>
            <div class="flex-1 text-center text-sm text-white/50">
                {#if syncing}
                    This may take a while.
                {:else}
                    Press Finish to continue to your library.
                {/if}
            </div>
            <IconButton text white on:click={finish} disabled={syncing}>
                <IconArrowRight size={16} />
                Finish
            </IconButton>
        </div>
    </div>
</div>
