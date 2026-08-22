<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import ViewLayout from "$lib/components/ViewLayout.svelte";
  import BackButton from "$lib/components/ui/BackButton.svelte";
  import IconButton from "$lib/components/ui/IconButton.svelte";
  import {
    IconInfoCircle,
    IconAdjustments,
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
    // For control: "task" only — the state.jobs registry name this entry
    // drives, and (mirroring showIf) the settings key/value that must match
    // for the task to be runnable, with the reason to show when it doesn't.
    job?: string;
    // For control: "action" only — looked up in settingActions below to run
    // the button's actual behavior, and the label shown on the button
    // itself (setting.label is the row's title, same as every other control).
    action?: string;
    buttonLabel?: string;
    enabledIf?: { key: string; value: string | boolean };
    disabledReason?: string;
    // Starts a new box within the section, splitting it off from whatever
    // came before. Omitted (the default) keeps a setting in the same box
    // as the previous one, so sections that never set this render as one
    // box, same as before this existed.
    break?: boolean;
  };

  type SectionDef = {
    id: string;
    label: string;
    settings: SettingDef[];
  };

  const schema = schemaData as { sections: SectionDef[] };

  // Every setting across every section, keyed by its own key — lets showIf
  // chain through a referenced setting's own showIf (see isVisible) instead
  // of each dependent setting having to repeat its ancestors' conditions.
  const settingsByKey = new Map<string, SettingDef>(
    schema.sections.flatMap((s) => s.settings).map((s) => [s.key, s]),
  );

  // Keyed by section id (settings-schema.json); falls back to no icon for
  // any section id added to the schema without a corresponding entry here.
  const sectionIcons: Record<string, typeof IconLibrary> = {
    setup: IconAdjustments,
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

  // What JobCard actually renders: schema-provided label/description/gating
  // merged with the backend's live state for that job.
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
    // Optimistic flip so the button reacts instantly; the socket corrects it.
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
      // backend unavailable; leave the list empty
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

  // A setting is visible only if its own showIf matches AND — when showIf
  // points at another setting in the schema — that setting is itself
  // visible, walking the chain transitively. So a setting gated on
  // "enable_transcoding", which is itself gated on "library_source", doesn't
  // need to repeat the library_source condition to inherit it.
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

  // Recomputed whenever settings values or job-load state change, since
  // showIf/task visibility can change which sections actually render.
  // `values` must appear directly in this statement (not just inside
  // isVisible) or Svelte's dependency analysis won't pick it up and this
  // will never re-run when a showIf-gating setting changes.
  $: renderedSections = schema.sections
    .map((section) => {
      const visibleSettings = section.settings.filter((s) => isVisible(s, values));
      const hasTasks = visibleSettings.some((s) => s.control === "task");
      return { ...section, visibleSettings, hasTasks };
    })
    // A section with tasks stays hidden until jobs have loaded, so it
    // doesn't flash empty (its boxes would have no rows to show yet).
    .filter((s) => s.visibleSettings.length > 0 && (!s.hasTasks || jobsLoaded));

  $: if (loaded && renderedSections) tick().then(updateActiveSection);

  function scrollToSection(id: string) {
    sectionRefs[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Highlights the last section whose top has scrolled past a fixed offset
  // from the content pane's top, mirroring how a reading position "sticks".
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

  async function rerunSetup() {
    const confirmed = await showConfirm({
      title: "Re-run setup?",
      message:
        "This may interrupt playback and switch your library source. Your existing libraries won't be deleted.",
      confirmLabel: "Continue",
    });
    if (!confirmed) return;

    await fetch(apiUrl("/api/settings"), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ onboarding_complete: false }),
    });
    onboardingComplete.set(false);
    goto("/onboarding");
  }

  // What a control: "action" button actually does when clicked, keyed by
  // its schema `action` id — mirrors how control: "task" looks a job up by
  // name in state.jobs, just on the frontend instead of the backend.
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
