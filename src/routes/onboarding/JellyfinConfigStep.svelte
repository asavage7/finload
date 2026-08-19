<script lang="ts">
  import { IconCheck, IconX, IconLoader2 } from "@tabler/icons-svelte";
  import StepButton from "./StepButton.svelte";

  type TestState = { status: "idle" | "testing" | "ok" | "error"; message: string };

  export let url: string;
  export let username: string;
  export let password: string;
  export let testState: TestState;
  export let canContinue: boolean;
  export let onTest: () => void;
  export let onBack: () => void;
  export let onNext: () => void;
</script>

<h1 class="text-2xl font-bold">Connect to Jellyfin</h1>
<p class="text-zinc-400 -mt-2">Enter your server details below.</p>

<div class="flex flex-col gap-3 w-full text-left">
  <input
    class="w-full bg-black/25 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-zinc-500 outline-none focus:border-white/30"
    placeholder="http://192.168.1.100:8096"
    bind:value={url}
  />
  <input
    class="w-full bg-black/25 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-zinc-500 outline-none focus:border-white/30"
    placeholder="Username"
    bind:value={username}
  />
  <input
    type="password"
    class="w-full bg-black/25 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-zinc-500 outline-none focus:border-white/30"
    placeholder="Password"
    bind:value={password}
  />
</div>

<div class="flex items-center gap-3">
  <button
    on:click={onTest}
    disabled={testState.status === "testing" || !url || !username || !password}
    class="px-4 py-1.5 rounded-full text-sm font-medium text-white hover:bg-white/5 border border-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-2"
  >
    {#if testState.status === "testing"}
      <IconLoader2 size={16} class="animate-spin" />
    {/if}
    Test connection
  </button>

  {#if testState.status === "ok"}
    <span class="text-sm text-emerald-400 flex items-center gap-1">
      <IconCheck size={16} />
      {testState.message}
    </span>
  {:else if testState.status === "error"}
    <span class="text-sm text-red-400 flex items-center gap-1">
      <IconX size={16} />
      {testState.message}
    </span>
  {/if}
</div>

<div class="flex gap-3 mt-4">
  <StepButton on:click={onBack}>Back</StepButton>
  <StepButton variant="primary" disabled={!canContinue} on:click={onNext}>Continue</StepButton>
</div>
