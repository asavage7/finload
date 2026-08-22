<script lang="ts">
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import ContextMenu from "$lib/components/ContextMenu.svelte";
  import CoverImage from "$lib/components/CoverImage.svelte";
  import {
    IconPlayerPlayFilled,
    IconArrowsShuffle,
    IconMenu2Filled,
  } from "@tabler/icons-svelte";

  export let typeLabel: string;
  export let title: string;
  export let bgSrc: string = "";
  export let onPlay: () => void;
  export let onShuffle: () => void;
  export let menuItems: any[] = [];
  export let primaryAction: "play" | "shuffle" = "play";

  $: play = { onClick: onPlay, icon: IconPlayerPlayFilled, label: "Play" };
  $: shuffle = {
    onClick: onShuffle,
    icon: IconArrowsShuffle,
    label: "Shuffle",
  };
  $: actions = primaryAction === "shuffle" ? [shuffle, play] : [play, shuffle];
</script>

<header class="relative w-full flex items-end md:px-8 pt-8 pb-4 pt-18">
  {#if bgSrc}
    <div class="absolute inset-0 opacity-20 pointer-events-none">
      <CoverImage
        src={bgSrc}
        alt=""
        showPlaceholder={false}
        class="w-full h-full blur-3xl"
      />
    </div>
  {/if}

  <BackButton class="absolute top-4 left-4 z-20" />

  <div
    class="relative z-10 flex flex-col md:flex-row items-center md:items-end gap-6 w-full max-w-[var(--8xl)] mx-auto pb-8 md:pr-6 border-b border-white/10"
  >
    {#if $$slots.cover}
      <div class="w-full px-8 md:w-auto md:p-0">
        <slot name="cover" />
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
