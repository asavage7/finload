<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { fly } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
  import { goto } from "$app/navigation";
  import { apiUrl, wsUrl } from "$lib/backend";
  import { onboardingComplete } from "$lib/store";
  import { open as openFolderDialog } from "@tauri-apps/plugin-dialog";
  import WelcomeStep from "./WelcomeStep.svelte";
  import SourceStep from "./SourceStep.svelte";
  import JellyfinConfigStep from "./JellyfinConfigStep.svelte";
  import LocalConfigStep from "./LocalConfigStep.svelte";
  import SyncingStep from "./SyncingStep.svelte";
  import DoneStep from "./DoneStep.svelte";

  // "jellyfin"/"local" stand in for the old single "configure" step, split so
  // each has its own flat branch below instead of a nested library-source if.
  type Step = "welcome" | "source" | "jellyfin" | "local" | "syncing" | "done";
  const STEP_ORDER: Step[] = ["welcome", "source", "jellyfin", "local", "syncing", "done"];

  // Dots collapse jellyfin/local into a single "configure" position so the
  // indicator still reads as 5 conceptual stages regardless of which source
  // the user picked.
  const DOT_INDEX: Record<Step, number> = {
    welcome: 0,
    source: 1,
    jellyfin: 2,
    local: 2,
    syncing: 3,
    done: 4,
  };
  const DOT_COUNT = 5;

  let step: Step = "welcome";
  let direction = 1;
  let loaded = false;
  // Drives the wrapper's animated height below; seeded with a reasonable
  // guess so the box isn't collapsed to 0 before the first real measurement.
  let contentHeight = 240;
  // The carousel's own width, so steps slide fully off/on rather than a
  // small fixed-pixel nudge.
  let boxWidth = 0;
  let stepEl: HTMLDivElement | undefined;

  // Measured with tick()+scrollHeight (synchronous) rather than
  // bind:clientHeight (ResizeObserver-based) so the height change starts in
  // the same frame as the slide, instead of visibly catching up a beat later.
  async function measureHeight() {
    await tick();
    if (stepEl) contentHeight = stepEl.scrollHeight;
  }

  function goToStep(next: Step) {
    direction = STEP_ORDER.indexOf(next) > STEP_ORDER.indexOf(step) ? 1 : -1;
    step = next;
    measureHeight();
  }

  let librarySource: "jellyfin" | "local" = "jellyfin";
  let jellyfinUrl = "";
  let jellyfinUsername = "";
  let jellyfinPassword = "";
  let localMusicPath = "";

  type TestState = { status: "idle" | "testing" | "ok" | "error"; message: string };
  let testState: TestState = { status: "idle", message: "" };
  let testedValues = { url: "", username: "", password: "" };

  // Reset a stale "connection ok" once any of the tested fields are edited.
  $: if (
    testState.status === "ok" &&
    (jellyfinUrl !== testedValues.url ||
      jellyfinUsername !== testedValues.username ||
      jellyfinPassword !== testedValues.password)
  ) {
    testState = { status: "idle", message: "" };
  }

  $: canContinueConfigure =
    librarySource === "jellyfin"
      ? testState.status === "ok"
      : localMusicPath.trim().length > 0;

  type SyncState = {
    status: "idle" | "running" | "complete" | "error";
    message: string;
    processed: number;
    total: number;
    added: number;
    removed: number;
  };
  const INITIAL_SYNC_STATE: SyncState = {
    status: "idle",
    message: "",
    processed: 0,
    total: 0,
    added: 0,
    removed: 0,
  };
  let syncState: SyncState = { ...INITIAL_SYNC_STATE };
  let syncWs: WebSocket | null = null;

  $: syncPercent =
    syncState.total > 0
      ? Math.min(100, Math.round((syncState.processed / syncState.total) * 100))
      : 0;

  function patchSettings(body: Record<string, unknown>) {
    return fetch(apiUrl("/api/settings"), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function fetchSettings() {
    try {
      const res = await fetch(apiUrl("/api/settings"));
      if (res.ok) {
        const s = await res.json();
        librarySource = s.library_source === "local" ? "local" : "jellyfin";
        jellyfinUrl = s.jellyfin_url ?? "";
        jellyfinUsername = s.jellyfin_username ?? "";
        jellyfinPassword = s.jellyfin_password ?? "";
        localMusicPath = s.local_music_path ?? "";
      }
    } catch {
      // backend unavailable — start from welcome with blank fields
    }
    loaded = true;
    measureHeight();
  }

  async function testConnection() {
    testState = { status: "testing", message: "" };
    try {
      const res = await fetch(apiUrl("/api/jellyfin/test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jellyfin_url: jellyfinUrl,
          jellyfin_username: jellyfinUsername,
          jellyfin_password: jellyfinPassword,
        }),
      });
      const data = await res.json();
      testState = { status: data.ok ? "ok" : "error", message: data.message ?? "" };
      if (data.ok) {
        testedValues = { url: jellyfinUrl, username: jellyfinUsername, password: jellyfinPassword };
      }
    } catch {
      testState = { status: "error", message: "Could not reach backend" };
    }
  }

  async function browseFolder() {
    try {
      const selected = await openFolderDialog({
        directory: true,
        multiple: false,
        title: "Choose your music folder",
      });
      if (typeof selected === "string") localMusicPath = selected;
    } catch {
      // Not running under the Tauri webview (plain `vite dev`) — the manual
      // text input stays available either way.
    }
  }

  function connectSyncSocket() {
    syncWs = new WebSocket(wsUrl("/ws/jobs/sync"));
    syncWs.onmessage = (event) => {
      syncState = JSON.parse(event.data);
      if (syncState.status === "complete") {
        setTimeout(() => goToStep("done"), 600);
      }
    };
  }

  async function startSync() {
    if (syncState.status === "running") return;
    syncState = { ...syncState, status: "running", message: "Starting…", processed: 0, total: 0 };
    try {
      await fetch(apiUrl("/api/jobs/sync/start"), { method: "POST" });
    } catch {
      syncState = { ...syncState, status: "error", message: "Could not reach backend" };
    }
  }

  async function continueFromConfigure() {
    const sourceFields =
      librarySource === "jellyfin"
        ? { jellyfin_url: jellyfinUrl, jellyfin_username: jellyfinUsername, jellyfin_password: jellyfinPassword }
        : { local_music_path: localMusicPath };
    await patchSettings({ library_source: librarySource, ...sourceFields });
    goToStep("syncing");
    connectSyncSocket();
    startSync();
  }

  function backToConfigure() {
    goToStep(librarySource);
    syncState = { ...INITIAL_SYNC_STATE };
    syncWs?.close();
    syncWs = null;
  }

  async function finish() {
    await patchSettings({ onboarding_complete: true });
    onboardingComplete.set(true);
    goto("/library");
  }

  onMount(fetchSettings);
  onDestroy(() => {
    syncWs?.close();
    syncWs = null;
  });

  type Orb = { top: number; left: number; size: number; duration: number; colorClass: string };
  const ORB_COLORS = ["bg-blue-600/40", "bg-purple-600/35", "bg-sky-500/25", "bg-indigo-500/30", "bg-pink-500/30"];

  function randomOrb(colorClass: string): Orb {
    return {
      top: Math.random() * 100,
      left: Math.random() * 100,
      size: 35 + Math.random() * 35,
      duration: 10 + Math.random() * 10,
      colorClass,
    };
  }

  let orbs: Orb[] = ORB_COLORS.map(randomOrb);
  let orbTimers: ReturnType<typeof setTimeout>[] = [];

  function retargetOrb(i: number) {
    orbs[i] = randomOrb(orbs[i].colorClass);
    orbs = orbs;
    orbTimers[i] = setTimeout(() => retargetOrb(i), orbs[i].duration * 1000);
  }

  onMount(() => {
    orbTimers = orbs.map((_, i) => setTimeout(() => retargetOrb(i), 300 + i * 400));
  });

  onDestroy(() => {
    orbTimers.forEach(clearTimeout);
  });
</script>

<div
  class="relative flex h-full w-full flex-col items-center justify-center overflow-hidden text-white px-8 pt-8"
>
  <div class="pointer-events-none absolute inset-0 z-0">
    {#each orbs as orb}
      <div
        class="onboarding-orb blur-3xl {orb.colorClass}"
        style="top: {orb.top}%; left: {orb.left}%; width: {orb.size}vmin; height: {orb.size}vmin; transition-duration: {orb.duration}s;"
      ></div>
    {/each}
  </div>

  {#if loaded}
    <div class="relative z-10 w-full max-w-lg m-8 flex flex-col items-center gap-6 text-center bg-white/5 p-8 rounded-xl border border-white/10">
      <div
        class="relative w-full overflow-hidden"
        style="height: {contentHeight}px; transition: height 200ms ease-in-out;"
        bind:clientWidth={boxWidth}
      >
        {#key step}
          <div
            bind:this={stepEl}
            class="absolute inset-x-0 top-1/2 -translate-y-1/2 flex flex-col items-center gap-6 text-center w-full"
            in:fly={{ x: direction * boxWidth, duration: 200, easing: cubicOut, opacity: 1 }}
            out:fly={{ x: -direction * boxWidth, duration: 200, easing: cubicOut, opacity: 1 }}
          >
            {#if step === "welcome"}
              <WelcomeStep onNext={() => goToStep("source")} />
            {:else if step === "source"}
              <SourceStep
                bind:selected={librarySource}
                onBack={() => goToStep("welcome")}
                onNext={() => goToStep(librarySource)}
              />
            {:else if step === "jellyfin"}
              <JellyfinConfigStep
                bind:url={jellyfinUrl}
                bind:username={jellyfinUsername}
                bind:password={jellyfinPassword}
                {testState}
                canContinue={canContinueConfigure}
                onTest={testConnection}
                onBack={() => goToStep("source")}
                onNext={continueFromConfigure}
              />
            {:else if step === "local"}
              <LocalConfigStep
                bind:path={localMusicPath}
                canContinue={canContinueConfigure}
                onBrowse={browseFolder}
                onBack={() => goToStep("source")}
                onNext={continueFromConfigure}
              />
            {:else if step === "syncing"}
              <SyncingStep {syncState} {syncPercent} onRetry={startSync} onBack={backToConfigure} />
            {:else if step === "done"}
              <DoneStep onFinish={finish} />
            {/if}
          </div>
        {/key}
      </div>

      <div class="flex items-center gap-2 m-2">
        {#each Array(DOT_COUNT) as _, i}
          <div
            class="h-1.5 w-1.5 rounded-full transition-colors duration-300 {DOT_INDEX[step] >= i ? 'bg-blue-500' : 'bg-white/10'}"
          ></div>
        {/each}
      </div>
    </div>
  {/if}
</div>
