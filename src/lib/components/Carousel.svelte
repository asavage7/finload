<script lang="ts">
  import { tick } from "svelte";
  import {
    IconArrowsShuffle,
    IconChevronLeft,
    IconChevronRight,
  } from "@tabler/icons-svelte";

  export let title: string;
  export let items: any[] = [];
  export let layout: "row" | "columns" = "row";
  // When set, shows a shuffle button to the left of the scroll arrows —
  // the caller owns what "shuffle all" means for this row's item type.
  export let onShuffle: (() => void) | null = null;

  let scrollEl: HTMLDivElement;
  let canScrollLeft = false;
  let canScrollRight = false;

  function updateScrollState() {
    if (!scrollEl) return;
    canScrollLeft = scrollEl.scrollLeft > 4;
    canScrollRight = scrollEl.scrollLeft < scrollEl.scrollWidth - scrollEl.clientWidth - 4;
  }

  $: if (items) {
    tick().then(updateScrollState);
  }

  function contentLeft(el: HTMLElement): number {
    return el.getBoundingClientRect().left - scrollEl.getBoundingClientRect().left + scrollEl.scrollLeft;
  }

  function scrollByPage(direction: 1 | -1) {
    if (!scrollEl) return;
    const children = Array.from(scrollEl.children) as HTMLElement[];
    if (children.length === 0) return;

    if (direction === 1) {
      const viewEnd = scrollEl.scrollLeft + scrollEl.clientWidth;
      const target = children.find((el) => contentLeft(el) + el.offsetWidth > viewEnd + 1);
      scrollEl.scrollTo({ left: target ? contentLeft(target) : scrollEl.scrollWidth, behavior: "smooth" });
    } else {
      const targetPos = Math.max(0, scrollEl.scrollLeft - scrollEl.clientWidth);
      const candidates = children.filter((el) => contentLeft(el) <= targetPos + 1);
      const target = candidates[candidates.length - 1] ?? children[0];
      scrollEl.scrollTo({ left: contentLeft(target), behavior: "smooth" });
    }
  }

</script>

<svelte:window on:resize={updateScrollState} />

{#if items.length > 0}
  <section class="w-full min-w-0">
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-lg md:text-xl font-bold text-white">{title}</h3>
      <div class="flex items-center gap-2 shrink-0">
        {#if onShuffle}
          <button
            on:click={onShuffle}
            aria-label="Shuffle all"
            class="p-2 rounded-full border border-transparent text-zinc-400 transition hover:text-white hover:bg-white/5"
          >
            <IconArrowsShuffle size={18} />
          </button>
        {/if}
        <button
          on:click={() => scrollByPage(-1)}
          disabled={!canScrollLeft}
          aria-label="Scroll back"
          class="p-2 rounded-full border border-transparent text-zinc-400 transition hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none"
        >
          <IconChevronLeft size={18} />
        </button>
        <button
          on:click={() => scrollByPage(1)}
          disabled={!canScrollRight}
          aria-label="Scroll forward"
          class="p-2 rounded-full border border-transparent text-zinc-400 transition hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none"
        >
          <IconChevronRight size={18} />
        </button>
      </div>
    </div>

    <div
      bind:this={scrollEl}
      on:scroll={updateScrollState}
      class="overflow-x-auto scroll-smooth pb-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden {layout ===
      'row'
        ? 'flex'
        : 'grid grid-rows-3 grid-flow-col justify-start gap-x-3 gap-y-1'}"
    >
      {#each items as item (item.id)}
        <div class={layout === "row" ? "w-48 shrink-0" : "w-72"}>
          <slot {item} />
        </div>
      {/each}
    </div>
  </section>
{/if}
