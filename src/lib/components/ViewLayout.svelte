<div class="w-full h-screen flex flex-col overflow-y-auto overflow-x-hidden bg-zinc-950">
  
  {#if $$slots.header}
    <div class="shrink-0">
      <slot name="header" />
    </div>
  {/if}

  {#if $$slots.toolbar}
    <div class="shrink-0 flex items-center justify-between w-full z-70">
      <slot name="toolbar" />
    </div>
  {/if}

  <div class="w-full flex-1 min-h-0">
    <slot name="content" />
  </div>

</div>