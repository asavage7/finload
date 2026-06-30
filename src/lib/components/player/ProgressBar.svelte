<script lang="ts">
  export let value = 0;
  export let max = 100;
  export let onSeek: (newValue: number) => void = () => {};
  export let accentColor = '#ffffff';

  let isDragging = false;
  let localValue = value;

  // Keep local value in sync with Python updates unless the user is actively dragging
  $: if (!isDragging) {
    localValue = value;
  }

  function handleInput(e: Event) {
    isDragging = true;
    localValue = Number((e.target as HTMLInputElement).value);
  }

  function handleChange(e: Event) {
    isDragging = false;
    const finalValue = Number((e.target as HTMLInputElement).value);
    onSeek(finalValue);
  }
</script>

<div class="relative w-full flex items-center group py-2 select-none">
  <div class="absolute left-0 right-0 h-1 bg-white/10 rounded-full overflow-hidden pointer-events-none">
    <div 
      class="h-full rounded-full" 
      style="width: {(localValue / (max || 1)) * 100}%; background-color: {accentColor}; will-change: width;"
    ></div>
  </div>

  <input
    type="range"
    min="0"
    {max}
    value={localValue}
    on:input={handleInput}
    on:change={handleChange}
    class="w-full h-1 opacity-0 cursor-pointer absolute inset-0 z-10"
  />

  <div 
    class="absolute w-3 h-3 bg-white rounded-full shadow-md pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-20"
    style="left: calc({(localValue / (max || 1)) * 100}% - 6px); will-change: left;"
  ></div>
</div>