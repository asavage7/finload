<div class="w-full h-full flex flex-col overflow-hidden bg-zinc-950">
  
  <div class="flex-grow overflow-y-auto overflow-x-hidden">
    
    {#if $$slots.header}
      <slot name="header" />
    {/if}

    <div>
      {#if $$slots.toolbar}
        <div class="flex items-center justify-between w-full pb-4 sticky top-0 z-7000">
          <slot name="toolbar" />
        </div>
      {/if}

        <div class="w-full mb-28">
            <slot name="content" />
        </div>
    </div>

  </div>
</div>