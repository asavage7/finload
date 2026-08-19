<script lang="ts">
  import StepButton from "./StepButton.svelte";

  export let path: string;
  export let canContinue: boolean;
  export let onBrowse: () => void;
  export let onBack: () => void;
  export let onNext: () => void;
</script>

<h1 class="text-2xl font-bold">Choose your music folder</h1>
<p class="text-zinc-400 -mt-2">Point Finload at the folder containing your music.</p>

<div class="flex gap-2 w-full">
  <input
    class="flex-1 bg-zinc-800 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-zinc-500 outline-none focus:border-white/30"
    placeholder="/home/user/Music"
    bind:value={path}
  />
  <button
    on:click={onBrowse}
    class="px-4 py-2 rounded-full text-sm font-medium text-white hover:bg-white/5 border border-white/10 transition shrink-0"
  >
    Browse…
  </button>
</div>

<div class="flex gap-3 mt-4">
  <StepButton on:click={onBack}>Back</StepButton>
  <StepButton variant="primary" disabled={!canContinue} on:click={onNext}>Continue</StepButton>
</div>
