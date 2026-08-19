<script lang="ts">
  export let destructive = false;
  export let white = false;
  export let active = false;
  export let accent = false;
  export let text = false;
  export let el: HTMLButtonElement | undefined = undefined;
  let cls = "";
  export { cls as class };

  $: hoverClass = destructive
    ? "hover:text-red-400 hover:bg-red-400/10"
    : accent
      ? "hover:brightness-110"
      : "hover:text-white hover:bg-white/5";

  $: baseClass = destructive
    ? active
      ? "text-red-400 bg-red-400/10 border-red-400/10"
      : "text-zinc-400 border-transparent"
    : accent
      ? "text-white bg-[var(--accent)] border-white/10"
      : active
        ? "text-white bg-white/10 border-white/10"
        : white
          ? "text-white border-transparent"
          : "text-zinc-400 border-transparent";
</script>

<button bind:this={el} on:click {...$$restProps} class="rounded-full transition border flex gap-2 items-center hover:cursor-pointer {text ? 'text-sm pr-3 py-1.5 pl-2' : 'p-2'} {baseClass} {hoverClass} {cls}">
  <slot />
</button>
