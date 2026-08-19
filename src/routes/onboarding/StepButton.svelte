<script lang="ts">
  export let variant: "primary" | "secondary" = "secondary";
  export let disabled = false;

  $: sizeClass = variant === "primary" ? "px-6 py-2 bg-blue-500 hover:bg-blue-400" : "px-4 py-2 hover:bg-white/5";
</script>

<button
  on:click
  {disabled}
  class="rounded-full text-sm font-semibold text-white border border-white/10 transition disabled:opacity-50 disabled:cursor-not-allowed {sizeClass}"
>
  <slot />
</button>
