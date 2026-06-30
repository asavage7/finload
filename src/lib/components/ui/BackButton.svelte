<script lang="ts">
  import { IconArrowBack } from "@tabler/icons-svelte";
  let cls = "";
  export { cls as class };
</script>

<button
  on:click={() => history.back()}
  class="p-2 rounded-full bg-transparent text-white/75 hover:text-white hover:bg-white/10 cursor-pointer transition {cls}"
>
  <div class="flex items-center gap-2 px-2">
    <IconArrowBack size={16} />
    Back
  </div>
</button>
