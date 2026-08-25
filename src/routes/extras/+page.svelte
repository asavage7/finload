<script lang="ts">
    import ViewLayout from "$lib/components/ViewLayout.svelte";
    import { IconMusicQuestion } from "@tabler/icons-svelte";
</script>

<ViewLayout>
    <div slot="content" class="w-full h-full overflow-y-auto pb-28">
        <div class=" px-4 md:px-8 pt-10 max-w-[var(--10xl)] mx-auto">
            <h1 class="text-3xl font-bold">Extras</h1>
            <p>Fun games or experiments with features of Finload.</p>
            <div
                class="grid grid-cols-[repeat(auto-fill,minmax(400px,1fr))] gap-4 mt-6"
            >
                <a
                    href="/extras/music-quiz"
                    class="bg-gradient-to-br from-blue-900 to-purple-800 hover:from-blue-800 hover:to-purple-800 border border-white/10 transition-colors rounded-lg p-4 flex flex-col gap-1 items-center justify-center text-center"
                >
                    <IconMusicQuestion size=32 class="text-white"/>
                    <h2 class="text-xl font-semibold">Music Quiz</h2>
                    <p class="text-sm text-white/70">
                        Test your music knowledge.
                    </p>
                </a>
            </div>
        </div>
    </div></ViewLayout
>
