<script lang="ts">
    import type { ComponentType } from "svelte";

    export let label: string;
    export let description: string = "";
    export let options: {
        value: any;
        label: string;
        icon?: ComponentType;
    }[] = [];
    export let value: any;
    export let onChange: (value: any) => void;
</script>

<div class="flex flex-col gap-2">
    <div>
        <div class="text-sm font-semibold text-white">{label}</div>
        {#if description}
            <div class="text-xs text-zinc-500">{description}</div>
        {/if}
    </div>
    <div class="flex items-center flex-wrap bg-white/5 rounded-full w-fit">
        {#each options as option (option.value)}
            <button
                on:click={() => onChange(option.value)}
                class="px-3.5 py-1.5 rounded-full text-sm font-semibold transition border {value ===
                option.value
                    ? 'bg-zinc-700 text-white shadow-lg border-white/10'
                    : 'text-zinc-400 hover:text-white hover:bg-white/5 border-transparent'}"
            >
                <div class="flex items-center gap-2">
                    {#if option.icon}
                        <svelte:component this={option.icon} size={16} />
                    {/if}
                    {option.label}
                </div>
            </button>
        {/each}
    </div>
</div>
