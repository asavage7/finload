<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import { apiUrl, wsUrl } from "$lib/backend";
  import schemaData from "$lib/settings-schema.json";

  type SelectOption = { value: string; label: string };

  type SettingDef = {
    key: string;
    label: string;
    description?: string;
    control: "toggle" | "select" | "text";
    options?: SelectOption[];
    placeholder?: string;
    hidden?: boolean;
    showIf?: { key: string; value: string | boolean };
  };

  type SectionDef = {
    id: string;
    label: string;
    settings: SettingDef[];
  };

  const schema = schemaData as { sections: SectionDef[] };

  let values: Record<string, unknown> = {};
  let loaded = false;
  let saveStatus = "";
  let saveTimer: ReturnType<typeof setTimeout>;

  type SyncState = {
    status: "idle" | "running" | "complete" | "error";
    message: string;
    processed: number;
    total: number;
    added: number;
    removed: number;
  };

  let syncState: SyncState = {
    status: "idle",
    message: "",
    processed: 0,
    total: 0,
    added: 0,
    removed: 0,
  };
  let syncWs: WebSocket | null = null;

  $: syncPercent =
    syncState.total > 0
      ? Math.min(100, Math.round((syncState.processed / syncState.total) * 100))
      : 0;

  type EnrichState = {
    status: "idle" | "running" | "complete" | "error";
    message: string;
    processed: number;
    total: number;
  };

  let enrichState: EnrichState = { status: "idle", message: "", processed: 0, total: 0 };
  let enrichPollTimer: ReturnType<typeof setInterval> | null = null;

  async function fetchEnrichStatus() {
    try {
      const res = await fetch(apiUrl("/api/metadata/status"));
      if (res.ok) enrichState = await res.json();
    } catch { /* backend unavailable */ }
    // Poll while running; stop once idle/complete/error.
    if (enrichState.status === "running" && !enrichPollTimer) {
      enrichPollTimer = setInterval(fetchEnrichStatus, 3000);
    } else if (enrichState.status !== "running" && enrichPollTimer) {
      clearInterval(enrichPollTimer);
      enrichPollTimer = null;
    }
  }

  function connectSyncSocket() {
    syncWs = new WebSocket(wsUrl("/ws/sync"));
    syncWs.onmessage = (event) => {
      syncState = JSON.parse(event.data);
    };
  }

  async function startSync() {
    if (syncState.status === "running") return;
    // Optimistic update so the button reacts instantly; the socket corrects it.
    syncState = { ...syncState, status: "running", message: "Starting…", processed: 0, total: 0 };
    try {
      await fetch(apiUrl("/api/sync"), { method: "POST" });
    } catch {
      syncState = { ...syncState, status: "error", message: "Could not reach backend" };
    }
  }

  onMount(async () => {
    connectSyncSocket();
    try {
      const res = await fetch(apiUrl("/api/settings"));
      if (res.ok) values = await res.json();
    } catch {
      // backend unavailable
    }
    loaded = true;
    fetchEnrichStatus();
  });

  onDestroy(() => {
    syncWs?.close();
    syncWs = null;
    if (enrichPollTimer) clearInterval(enrichPollTimer);
  });

  function isVisible(setting: SettingDef): boolean {
    if (!setting.showIf) return true;
    return values[setting.showIf.key] === setting.showIf.value;
  }

  async function saveSetting(key: string, value: unknown) {
    values = { ...values, [key]: value };
    clearTimeout(saveTimer);
    try {
      const res = await fetch(apiUrl("/api/settings"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
      saveStatus = res.ok ? "Saved" : "Error saving";
    } catch {
      saveStatus = "Error saving";
    }
    saveTimer = setTimeout(() => (saveStatus = ""), 2000);
  }

  function handleToggle(setting: SettingDef) {
    saveSetting(setting.key, !values[setting.key]);
  }

  function handleSelect(setting: SettingDef, e: Event) {
    saveSetting(setting.key, (e.currentTarget as HTMLSelectElement).value);
  }

  function handleTextBlur(setting: SettingDef, e: Event) {
    saveSetting(setting.key, (e.currentTarget as HTMLInputElement).value);
  }
</script>

<ViewLayout>
  <div
    slot="toolbar"
    class="flex items-center justify-between w-full bg-zinc-900 border-b border-white/10 p-4"
  >
    <BackButton />
    <span class="text-sm font-semibold text-white">Settings</span>
    <span
      class="text-xs w-16 text-right transition-opacity"
      class:text-emerald-400={saveStatus === "Saved"}
      class:text-red-400={saveStatus !== "Saved" && saveStatus !== ""}
      class:opacity-0={!saveStatus}
    >
      {saveStatus}
    </span>
  </div>

  <div slot="content" class="w-full h-full overflow-y-auto pb-28">
    {#if !loaded}
      <div class="p-8 text-zinc-500 text-sm">Connecting to backend…</div>
    {:else}
      <div class="max-w-2xl mx-auto px-6 py-6 flex flex-col gap-8">

        <section>
          <h2 class="text-xs font-bold uppercase tracking-widest text-zinc-500 mb-3">
            Library Sync
          </h2>
          <div class="bg-zinc-800 rounded-xl border border-white/5 p-4 flex flex-col gap-3">
            <div class="flex items-center justify-between gap-4">
              <div class="min-w-0">
                <div class="text-sm font-medium text-white">Sync library</div>
                <div class="text-xs text-zinc-500 mt-0.5">
                  {#if syncState.status === "running"}
                    {syncState.message}{syncState.total > 0
                      ? ` — ${syncState.processed}/${syncState.total}`
                      : ""}
                  {:else if syncState.status === "complete"}
                    {syncState.message}
                  {:else if syncState.status === "error"}
                    <span class="text-red-400">{syncState.message}</span>
                  {:else}
                    Import new tracks and remove deleted ones from your server.
                  {/if}
                </div>
              </div>
              <button
                on:click={startSync}
                disabled={syncState.status === "running"}
                class="px-4 py-1.5 rounded-lg text-sm font-medium text-white bg-blue-500 hover:bg-blue-400 disabled:opacity-50 disabled:cursor-not-allowed transition shrink-0"
              >
                {syncState.status === "running" ? "Syncing…" : "Sync Now"}
              </button>
            </div>

            {#if syncState.status === "running"}
              <div class="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden">
                <div
                  class="h-full bg-blue-500 transition-all duration-300"
                  class:animate-pulse={syncState.total === 0}
                  style="width: {syncState.total > 0 ? syncPercent : 100}%"
                ></div>
              </div>
            {/if}
          </div>

          {#if enrichState.status === "running"}
            <div class="text-xs text-zinc-500 flex items-center gap-2 mt-1 px-1">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse shrink-0"></span>
              Adding metadata to your library, some images may be missing until this is complete
              {#if enrichState.total > 0}
                <span class="text-zinc-600">({enrichState.processed}/{enrichState.total})</span>
              {/if}
            </div>
          {/if}
        </section>

        {#each schema.sections as section (section.id)}
          {@const visibleSettings = section.settings.filter(isVisible)}
          {#if visibleSettings.length > 0}
            <section>
              <h2 class="text-xs font-bold uppercase tracking-widest text-zinc-500 mb-3">
                {section.label}
              </h2>
              <div class="bg-zinc-800 rounded-xl border border-white/5 divide-y divide-white/5">

                {#each visibleSettings as setting (setting.key)}
                  <div class="flex items-center justify-between px-4 py-3">
                    <div class="mr-6 min-w-0">
                      <div class="text-sm font-medium text-white">{setting.label}</div>
                      {#if setting.description}
                        <div class="text-xs text-zinc-500 mt-0.5">{setting.description}</div>
                      {/if}
                    </div>

                    {#if setting.control === "toggle"}
                      <button
                        role="switch"
                        aria-checked={!!values[setting.key]}
                        class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none"
                        class:bg-blue-500={!!values[setting.key]}
                        class:bg-zinc-700={!values[setting.key]}
                        on:click={() => handleToggle(setting)}
                      >
                        <span
                          class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200"
                          class:translate-x-5={!!values[setting.key]}
                          class:translate-x-0={!values[setting.key]}
                        />
                      </button>

                    {:else if setting.control === "select" && setting.options}
                      <select
                        class="bg-zinc-700 border border-white/10 text-sm text-white rounded-lg px-3 py-1.5 outline-none focus:ring-1 focus:ring-white/20 shrink-0"
                        value={String(values[setting.key] ?? "")}
                        on:change={(e) => handleSelect(setting, e)}
                      >
                        {#each setting.options as opt (opt.value)}
                          <option value={opt.value}>{opt.label}</option>
                        {/each}
                      </select>

                    {:else if setting.control === "text"}
                      <input
                        type={setting.hidden ? "password" : "text"}
                        class="bg-zinc-700 border border-white/10 text-sm text-white rounded-lg px-3 py-1.5 outline-none focus:ring-1 focus:ring-white/20 w-56 shrink-0"
                        placeholder={setting.placeholder ?? ""}
                        value={String(values[setting.key] ?? "")}
                        on:blur={(e) => handleTextBlur(setting, e)}
                      />
                    {/if}
                  </div>
                {/each}

              </div>
            </section>
          {/if}
        {/each}

      </div>
    {/if}
  </div>
</ViewLayout>
