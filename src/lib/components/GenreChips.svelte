<script lang="ts">
  import { onMount, tick } from "svelte";

  type Genre = { id: string; name: string };

  // Shared by the album and artist detail page headers. Caps visible chips
  // and tucks the rest behind a "+N" chip that opens a floating popover
  // styled like ContextMenu (same portal/positioning/outside-click
  // conventions) but showing more chips, not a menu-item list. Each chip
  // links to /genre/{id}.
  export let genres: Genre[] = [];
  export let max: number = 3;

  $: visible = genres.slice(0, max);
  $: overflow = genres.slice(max);
  // Chunked into fixed-size rows so the popover wraps predictably at 3-per-row
  // regardless of how long individual genre names are (a shared CSS grid
  // column width made every chip in a column stretch to the widest one).
  $: overflowRows = chunk(overflow, 3);

  function chunk<T>(items: T[], size: number): T[][] {
    const rows: T[][] = [];
    for (let i = 0; i < items.length; i += size) {
      rows.push(items.slice(i, i + size));
    }
    return rows;
  }

  const chipClass =
    "px-2.5 py-0.5 rounded-full text-xs font-medium text-zinc-300 bg-white/5 border border-white/10";

  const MARGIN = 8;

  let open = false;
  let positioned = false;
  let left = 0;
  let top = 0;
  let triggerEl: HTMLButtonElement;
  let popoverEl: HTMLDivElement | null = null;

  async function toggle() {
    if (open) {
      open = false;
      return;
    }
    window.dispatchEvent(new CustomEvent("close-all-context-menus"));
    positioned = false;
    open = true;
    // Popover mounts invisible (fixed + max-width already applied via static
    // classes) so this measures its real, constrained size before we clamp
    // and reveal it — measuring before those constraints exist is what
    // caused it to size itself full-width and land in the wrong spot.
    await tick();
    position();
  }

  function position() {
    if (!triggerEl || !popoverEl) return;
    const rect = triggerEl.getBoundingClientRect();
    const popRect = popoverEl.getBoundingClientRect();

    left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - popRect.width - MARGIN));

    let t = rect.bottom + 4;
    if (t + popRect.height > window.innerHeight - MARGIN) {
      t = Math.max(MARGIN, rect.top - popRect.height - 4);
    }
    top = t;
    positioned = true;
  }

  function close() {
    open = false;
  }

  function handleOutsideClick(e: MouseEvent) {
    if (open && popoverEl && !popoverEl.contains(e.target as Node) && e.target !== triggerEl) {
      open = false;
    }
  }

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return {
      destroy() {
        node.remove();
      },
    };
  }

  onMount(() => {
    window.addEventListener("close-all-context-menus", close);
    return () => window.removeEventListener("close-all-context-menus", close);
  });
</script>

<svelte:window on:click={handleOutsideClick} on:scroll|capture={close} on:resize={close} />

{#if genres.length > 0}
  <div class="flex flex-wrap items-center justify-center md:justify-start gap-1.5">
    {#each visible as genre (genre.id)}
      <a href={`/genre/${genre.id}`} class="{chipClass} hover:text-white hover:bg-white/10 transition">{genre.name}</a>
    {/each}
    {#if overflow.length > 0}
      <button
        bind:this={triggerEl}
        on:click|stopPropagation={toggle}
        class="{chipClass} text-zinc-400 hover:text-white hover:bg-white/10 transition"
      >
        +{overflow.length}
      </button>
    {/if}
  </div>
{/if}

{#if open}
  <div
    use:portal
    bind:this={popoverEl}
    class="fixed z-[2000] flex flex-col gap-1.5 p-1 bg-black/10 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl"
    class:invisible={!positioned}
    style="left: {left}px; top: {top}px; width: max-content;"
  >
    {#each overflowRows as row, i (i)}
      <div class="flex gap-1.5">
        {#each row as genre (genre.id)}
          <a href={`/genre/${genre.id}`} class="{chipClass} hover:text-white hover:bg-white/10 transition">{genre.name}</a>
        {/each}
      </div>
    {/each}
  </div>
{/if}
