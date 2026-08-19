<script lang="ts">
  import StepButton from "./StepButton.svelte";

  export let onFinish: () => void;
</script>

<h1 class="text-2xl font-bold">You're all set</h1>
<p class="text-zinc-400 -mt-2">Your library is ready to go.</p>
<StepButton variant="primary" on:click={onFinish}>Start listening</StepButton>
