<script lang="ts">
  import { IconServer, IconFolder } from "@tabler/icons-svelte";
  import StepButton from "./StepButton.svelte";

  export let selected: "jellyfin" | "local";
  export let onBack: () => void;
  export let onNext: () => void;
</script>

<h1 class="text-2xl font-bold">Where's your music?</h1>
<p class="text-white/50 -mt-2">Choose where Finload should load your library from.</p>

<div class="grid grid-cols-2 gap-4 w-full">
  <button
    on:click={() => (selected = "jellyfin")}
    class="flex flex-col items-center gap-3 p-6 rounded-xl border transition bg-black/25 {selected === 'jellyfin' ? 'border-blue-500' : 'border-white/10'}"
  >
    <IconServer size={28} />
    <span class="text-sm font-semibold">Jellyfin</span>
  </button>
  <button
    on:click={() => (selected = "local")}
    class="flex flex-col items-center gap-3 p-6 rounded-xl border transition bg-black/25 {selected === 'local' ? 'border-blue-500' : 'border-white/10'}"
  >
    <IconFolder size={28} />
    <span class="text-sm font-semibold">Local files</span>
  </button>
</div>

<p class="text-white/50 text-sm">
  We recommend tagging your library with MusicBrainz Picard or a similar tool for best results.
</p>

<div class="flex gap-3 mt-4">
  <StepButton on:click={onBack}>Back</StepButton>
  <StepButton variant="primary" on:click={onNext}>Continue</StepButton>
</div>
