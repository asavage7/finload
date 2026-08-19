<script lang="ts">
  import StepButton from "./StepButton.svelte";

  export let onNext: () => void;
</script>

<h1 class="text-3xl font-bold">Welcome to Finload</h1>
<p class="text-white/50">
  Let's get your music library set up. This will only take a minute.
</p>
<StepButton variant="primary" on:click={onNext}>Get Started</StepButton>
