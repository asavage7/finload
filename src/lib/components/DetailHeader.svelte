<script lang="ts">
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import ContextMenu from "$lib/components/ContextMenu.svelte";
  import CoverImage from "$lib/components/CoverImage.svelte";
  import { getImageUrl } from "$lib/utils/media";
  import {
    IconPlayerPlayFilled,
    IconArrowsShuffle,
    IconMenu2Filled,
  } from "@tabler/icons-svelte";

  export let typeLabel: string;
  export let title: string;
  export let id: string = "";
  export let onPlay: () => void;
  export let onShuffle: () => void;
  export let menuItems: any[] = [];
  export let primaryAction: "play" | "shuffle" = "play";

  let imageModalOpen = false;

  $: play = { onClick: onPlay, icon: IconPlayerPlayFilled, label: "Play" };
  $: shuffle = {
    onClick: onShuffle,
    icon: IconArrowsShuffle,
    label: "Shuffle",
  };
  $: actions = primaryAction === "shuffle" ? [shuffle, play] : [play, shuffle];
</script>

<header class="relative w-full flex items-end md:px-8 pt-8 pb-4 pt-18">
  {#if id}
    <div class="absolute inset-0 opacity-15 pointer-events-none">
      <CoverImage
        src={getImageUrl(id, 240, typeLabel)}
        alt=""
        showPlaceholder={false}
        class="w-full h-full blur-3xl scale-110"
      />
    </div>
  {/if}

  <BackButton class="absolute top-4 left-4 z-20" />

  <div
    class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-[var(--8xl)] mx-auto pb-8 md:pr-6 border-b border-white/10"
  >
    {#if $$slots.cover}
      <!-- Still needed by playlist page, hoping to remove in the future -->
      <div class="w-full px-8 md:w-auto md:p-0">
        <slot name="cover" />
      </div>
    {:else if id}
      <div class="w-32 md:w-48 lg:w-56">
        <CoverImage
          src={getImageUrl(id, 240, typeLabel)}
          alt="Image of {title}"
          showPlaceholder={false}
          class="w-full aspect-square cursor-pointer {typeLabel === 'ARTIST'
            ? 'rounded-full'
            : 'rounded-xl'}"
          on:click={() => (imageModalOpen = true)}
        />
      </div>
    {/if}

    <!-- pr reserve keeps title + meta clear of the pinned button cluster:
         a narrow column in the md–lg band, a wider row at lg+. -->
    <div
      class="flex-1 min-w-0 text-center md:text-left space-y-2 px-4 md:pl-0 md:pr-10 lg:pr-0"
    >
      <span
        class="text-xs uppercase font-black tracking-widest"
        style="color: var(--accent-light)">{typeLabel}</span
      >
      <h1
        class="text-2xl md:text-4xl lg:text-5xl font-black text-white line-clamp-2 mb-0 pb-1"
      >
        {title}
      </h1>
      <div class="space-y-2 lg:pr-48">
        <slot name="meta" />
      </div>
    </div>

    <div
      class="md:absolute right-0 flex flex-row md:flex-col lg:flex-row justify-center items-center gap-2"
    >
      {#each actions as action, i}
        {@const Icon = action.icon}
        <IconButton
          on:click={action.onClick}
          accent={i === 0}
          aria-label={action.label}
          class={i === 0 ? "lg:px-4 gap-3 font-semibold" : ""}
        >
          <Icon size={16} />
          {#if i === 0}
            <span class="hidden lg:inline text-sm font-semibold">
              {action.label}
            </span>
          {/if}
        </IconButton>
      {/each}
      <ContextMenu items={menuItems} let:toggle>
        <IconButton on:click={toggle} aria-label="More options">
          <IconMenu2Filled size={16} />
        </IconButton>
      </ContextMenu>
    </div>
  </div>
</header>
{#if imageModalOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
    on:click={() => (imageModalOpen = false)}
  >
    <div
      class="bg-zinc-900 border border-white/10 rounded-2xl shadow-2xl p-4 mx-4 flex flex-col"
      style="max-width: 95vw; max-height: calc(100vh - 2rem); overflow-y: auto"
      on:click|stopPropagation
    >
      <div
        class="mx-auto shrink-0"
        style="width: min(80vw, 70vh); aspect-ratio: 1 / 1"
      >
        <CoverImage
          src={getImageUrl(id, 2000, typeLabel)}
          alt="Image of {title}"
          showPlaceholder={false}
          class="w-full h-full object-contain rounded-lg"
        />
      </div>
      <button
        on:click={() => (imageModalOpen = false)}
        class="mt-5 self-end px-4 py-2 rounded-full text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/10 transition border border-white/10"
      >
        Close
      </button>
    </div>
  </div>
{/if}
