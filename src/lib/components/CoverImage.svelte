<script lang="ts">
  let className = '';
  export { className as class };
  export let src: string = '';
  export let alt: string = '';
  export let fallbackText: string = '';
  export let style: string = '';

  let failed = false;
  let loaded = false;
  let _prevSrc = '';
  // Reset when the image source changes (e.g. track or album changed).
  $: if (src !== _prevSrc) { _prevSrc = src; failed = false; loaded = false; }

  // Show a cached/already-complete image immediately instead of flashing the
  // fallback letter for a frame.
  function trackLoad(img: HTMLImageElement) {
    if (img.complete && img.naturalWidth > 0) loaded = true;
  }
</script>

<div
  class="relative overflow-hidden border border-white/5 bg-zinc-700 flex items-center justify-center {className}"
  style="container-type: size; {style}"
>
  {#if !loaded || failed || !src}
    <span
      class="font-semibold text-zinc-600 select-none pointer-events-none text-base"
      style="font-size: max(1rem, 15cqmin);"
    >
      {(fallbackText || alt || '?').charAt(0).toUpperCase()}
    </span>
  {/if}

  {#if src && !failed}
    <img
      {src}
      {alt}
      use:trackLoad
      on:load={() => (loaded = true)}
      on:error={() => (failed = true)}
      loading="lazy"
      decoding="async"
      class="absolute inset-0 w-full h-full object-cover transition-opacity duration-200 {loaded ? 'opacity-100' : 'opacity-0'}"
    />
  {/if}

  <slot />
</div>
