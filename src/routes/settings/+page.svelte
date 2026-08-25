<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import {
    IconInfoCircle,
    IconListCheck,
    IconLibrary,
    IconPlayerPlay,
    IconMicrophone2,
    IconRadio,
  } from "@tabler/icons-svelte";
  import JobCard from "$lib/components/JobCard.svelte";
  import JellyfinLibraryModal from "$lib/components/modals/JellyfinLibraryModal.svelte";
  import { apiUrl } from "$lib/backend";
  import schemaData from "$lib/settings-schema.json";
  import { goto } from "$app/navigation";
  import { onboardingComplete, showConfirm } from "$lib/store";
  import {
    subscribeJobStatus,
    startJob,
    type JobInfo,
    type JobState,
  } from "$lib/utils/backgroundJobs";

  type SelectOption = { value: string; label: string };

  type SettingDef = {
    key: string;
    label?: string;
    description?: string;
    control: "toggle" | "select" | "text" | "number" | "task" | "action" | "info";
    options?: SelectOption[];
    placeholder?: string;
    hidden?: boolean;
    min?: number;
    max?: number;
    step?: number;
    showIf?: { key: string; value: string | boolean };
    job?: string;
    action?: string;
    buttonLabel?: string;
    enabledIf?: { key: string; value: string | boolean };
    disabledReason?: string;
    break?: boolean;
  };

  type SectionDef = {
    id: string;
    label: string;
    settings: SettingDef[];
  };

  const schema = schemaData as { sections: SectionDef[] };

  const settingsByKey = new Map<string, SettingDef>(
    schema.sections.flatMap((s) => s.settings).map((s) => [s.key, s]),
  );

  // Keyed by section id (settings-schema.json); falls back to no icon for
  // any section id added to the schema without a corresponding entry here.
  const sectionIcons: Record<string, typeof IconLibrary> = {
    tasks: IconListCheck,
    library: IconLibrary,
    playback: IconPlayerPlay,
    lyrics: IconMicrophone2,
    radio: IconRadio,
  };

  let values: Record<string, unknown> = {};
  let loaded = false;
  let libraryModalOpen = false;

  let jobs: JobInfo[] = [];
  let jobsLoaded = false;
  let unsubscribeJobs: Array<() => void> = [];

  let contentEl: HTMLDivElement | undefined;
  let sectionRefs: Record<string, HTMLElement> = {};
  let activeSectionId = "";

  function setJobState(name: string, state: JobState) {
    jobs = jobs.map((job) => (job.name === name ? { ...job, state } : job));
  }

  type TaskDisplay = {
    label: string;
    description: string;
    enabled: boolean;
    disabled_reason: string | null;
    supports_force: boolean;
    state: JobState;
  };

  function buildTask(
    setting: SettingDef,
    backendJob: JobInfo,
    currentValues: Record<string, unknown>,
  ): TaskDisplay {
    const enabled =
      !setting.enabledIf || currentValues[setting.enabledIf.key] === setting.enabledIf.value;
    return {
      label: setting.label ?? backendJob.name,
      description: setting.description ?? "",
      enabled,
      disabled_reason: enabled ? null : (setting.disabledReason ?? null),
      supports_force: backendJob.supports_force,
      state: backendJob.state,
    };
  }

  async function runJob(name: string, task: TaskDisplay, force: boolean) {
    if (!task.enabled || task.state.status === "running") return;
    if (force) {
      const ok = await showConfirm({
        title: `Re-run ${task.label}?`,
        message: "This reprocesses your whole library and can take a while.",
        confirmLabel: "Re-run",
      });
      if (!ok) return;
    }
    // Optimistic UI update
    setJobState(name, {
      ...task.state,
      status: "running",
      message: "Starting…",
      processed: 0,
      total: 0,
    });
    try {
      await startJob(name, force);
    } catch {
      setJobState(name, {
        ...task.state,
        status: "error",
        message: "Could not reach backend",
      });
    }
  }

  async function loadJobs() {
    try {
      const res = await fetch(apiUrl("/api/jobs"));
      if (res.ok) {
        const data = await res.json();
        jobs = data.jobs as JobInfo[];
        unsubscribeJobs = jobs.map((job) =>
          subscribeJobStatus(job.name, (state) => setJobState(job.name, state)),
        );
      }
    } catch {
      // backend unavailable
    }
    jobsLoaded = true;
  }

  onMount(async () => {
    loadJobs();
    try {
      const res = await fetch(apiUrl("/api/settings"));
      if (res.ok) values = await res.json();
    } catch {
      // backend unavailable
    }
    loaded = true;
  });

  onDestroy(() => {
    unsubscribeJobs.forEach((unsubscribe) => unsubscribe());
  });

  // A setting is visible only if the chain of showIf conditions are satisfied.
  function isVisible(
    setting: SettingDef,
    currentValues: Record<string, unknown>,
    seen: Set<string> = new Set(),
  ): boolean {
    if (!setting.showIf) return true;
    if (currentValues[setting.showIf.key] !== setting.showIf.value) return false;
    if (seen.has(setting.key)) return true; // cycle guard; shouldn't happen in practice
    const parent = settingsByKey.get(setting.showIf.key);
    return !parent || isVisible(parent, currentValues, new Set(seen).add(setting.key));
  }

  function splitIntoBoxes(settings: SettingDef[]): SettingDef[][] {
    const boxes: SettingDef[][] = [];
    for (const setting of settings) {
      if (setting.break || boxes.length === 0) boxes.push([]);
      boxes[boxes.length - 1].push(setting);
    }
    return boxes;
  }

  // Recomputed whenever settings values or job-load state change
  $: renderedSections = schema.sections
    .map((section) => {
      const visibleSettings = section.settings.filter((s) => isVisible(s, values));
      const hasTasks = visibleSettings.some((s) => s.control === "task");
      return { ...section, visibleSettings, hasTasks };
    })
    // A section with tasks stays hidden until jobs have loaded
    .filter((s) => s.visibleSettings.length > 0 && (!s.hasTasks || jobsLoaded));

  $: if (loaded && renderedSections) tick().then(updateActiveSection);

  function scrollToSection(id: string) {
    sectionRefs[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Highlights the last section whose top has scrolled past a fixed offset
  function updateActiveSection() {
    if (!contentEl) return;
    const threshold = contentEl.getBoundingClientRect().top + 140;
    let current = renderedSections[0]?.id ?? "";
    for (const section of renderedSections) {
      const el = sectionRefs[section.id];
      if (el && el.getBoundingClientRect().top <= threshold) current = section.id;
    }
    activeSectionId = current;
  }

  async function saveSetting(key: string, value: unknown) {
    values = { ...values, [key]: value };
    let errorDetail = "";
    try {
      const res = await fetch(apiUrl("/api/settings"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
      if (!res.ok) errorDetail = `HTTP ${res.status}: ${await res.text()}`;
    } catch (e) {
      errorDetail = e instanceof Error ? e.message : String(e);
    }
    if (!errorDetail) return;

    const details = `Failed to save "${key}": ${errorDetail}`;
    const copy = await showConfirm({
      title: "Couldn't save setting",
      message: details,
      confirmLabel: "Copy to Clipboard",
      cancelLabel: "Close",
    });
    if (copy) navigator.clipboard.writeText(details);
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

  function handleNumberBlur(setting: SettingDef, e: Event) {
    const raw = Number((e.currentTarget as HTMLInputElement).value);
    if (Number.isNaN(raw)) return;
    let value = raw;
    if (setting.min != null) value = Math.max(setting.min, value);
    if (setting.max != null) value = Math.min(setting.max, value);
    saveSetting(setting.key, value);
  }

  // Setup is where the library source is chosen, and changing it swaps both the
  // provider and the database the app is running on. Restarting into setup means
  // that happens on a freshly started app with nothing playing or cached, rather
  // than being hot-swapped underneath a live player.
  async function rerunSetup() {
    const confirmed = await showConfirm({
      title: "Re-run setup?",
      message:
        "Finload will restart into the setup wizard, so playback stops. Each source keeps its own library, so nothing is deleted.",
      confirmLabel: "Restart and Set Up",
    });
    if (!confirmed) return;

    await fetch(apiUrl("/api/settings"), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ onboarding_complete: false }),
    });
    try {
      await invoke("restart_app");
    } catch {
      // Not running under the Tauri webview (browser dev run): fall back to
      // navigating there in place.
      onboardingComplete.set(false);
      goto("/onboarding");
    }
  }

  const settingActions: Record<string, () => void> = {
    rerun_setup: rerunSetup,
    manage_jellyfin_libraries: () => (libraryModalOpen = true),
  };
</script>

<ViewLayout>
  <div
    slot="toolbar"
    class="flex items-center justify-between w-full bg-zinc-900 border-b border-white/10 p-2"
  >
    <BackButton />
    <span class="text-sm font-semibold text-white">Settings</span>
    <IconButton white aria-label="About Finload">
      <IconInfoCircle size={16} />
    </IconButton>
  </div>

  <div
    slot="content"
    bind:this={contentEl}
    on:scroll={updateActiveSection}
    class="w-full h-full overflow-y-auto max-w-4xl mx-auto pb-28"
  >
    {#if !loaded}
      <div class="p-8 text-zinc-500 text-sm">Connecting to backend…</div>
    {:else}
    <div class="flex items-start gap-6 p-4">
      <div class="hidden md:flex md:flex-col w-48 sticky top-6 gap-1 p-2 border border-white/5 rounded-xl bg-white/5">
        {#each renderedSections as section (section.id)}
          <a
            href="#{section.id}"
            on:click|preventDefault={() => scrollToSection(section.id)}
            class="flex items-center gap-3 py-1.5 px-2 rounded-lg border text-sm transition {activeSectionId ===
            section.id
              ? 'bg-white/10 text-white border-white/10 font-semibold'
              : 'text-zinc-400 hover:text-white hover:bg-white/5 border-transparent'}"
          >
            <svelte:component this={sectionIcons[section.id]} size={16} class="shrink-0" />
            <span class="truncate">{section.label}</span>
          </a>
        {/each}
        <div class="shrink-0"></div>
      </div>
      <div class="flex-1 min-w-0 mb-[110%]">
        <div class="max-w-2xl mx-auto flex flex-col">

        {#each renderedSections as section (section.id)}
          {@const boxes = splitIntoBoxes(section.visibleSettings)}
            <section id={section.id} bind:this={sectionRefs[section.id]}>
              <h2
                class="text-xs font-bold uppercase tracking-widest text-zinc-500 mb-3 mt-6"
              >
                {section.label}
              </h2>

              <div class="flex flex-col gap-3">
                {#each boxes as box (box[0].key)}
                  <div
                    class="bg-zinc-800 rounded-xl border border-white/5 divide-y divide-white/5"
                  >
                    {#each box as setting (setting.key)}
                      {#if setting.control === "task"}
                        {@const backendJob = jobs.find((j) => j.name === setting.job)}
                        {#if backendJob}
                          {@const task = buildTask(setting, backendJob, values)}
                          <div class="px-4 py-3">
                            <JobCard
                              job={task}
                              onRun={(force) => runJob(setting.job ?? "", task, force)}
                            />
                          </div>
                        {/if}
                      {:else}
                        <div class="flex items-center justify-between px-4 py-3">
                          <div class="mr-6 min-w-0 flex-1">
                            {#if setting.label}
                              <div class="text-sm font-medium text-white">
                                {setting.label}
                              </div>
                            {/if}
                            {#if setting.description}
                              <div
                                class="whitespace-pre-line {setting.control === 'info' ? 'text-white text-sm leading-relaxed' : 'text-zinc-500 text-xs'}"
                                class:mt-0.5={setting.label}
                              >
                                {setting.description}
                              </div>
                            {/if}
                          </div>

                          {#if setting.control === "toggle"}
                            <button
                              role="switch"
                              aria-checked={!!values[setting.key]}
                              aria-label={setting.label}
                              class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none"
                              class:bg-blue-500={!!values[setting.key]}
                              class:bg-zinc-700={!values[setting.key]}
                              on:click={() => handleToggle(setting)}
                            >
                              <span
                                class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200"
                                class:translate-x-5={!!values[setting.key]}
                                class:translate-x-0={!values[setting.key]}
                              ></span>
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
                          {:else if setting.control === "number"}
                            <input
                              type="number"
                              class="bg-zinc-700 border border-white/10 text-sm text-white rounded-lg px-3 py-1.5 outline-none focus:ring-1 focus:ring-white/20 w-24 shrink-0"
                              min={setting.min}
                              max={setting.max}
                              step={setting.step}
                              value={Number(values[setting.key] ?? 0)}
                              on:blur={(e) => handleNumberBlur(setting, e)}
                            />
                          {:else if setting.control === "action"}
                            <button
                              on:click={() => settingActions[setting.action ?? ""]?.()}
                              class="px-4 py-1.5 rounded-lg text-sm font-medium text-white bg-blue-500 hover:bg-blue-400 transition shrink-0"
                            >
                              {setting.buttonLabel ?? "Run"}
                            </button>
                          {/if}
                        </div>
                      {/if}
                    {/each}
                  </div>
                {/each}
              </div>
            </section>
        {/each}
        </div>
      </div>
    </div>
    {/if}
  </div>
</ViewLayout>

<JellyfinLibraryModal bind:open={libraryModalOpen} onClose={() => (libraryModalOpen = false)} />
