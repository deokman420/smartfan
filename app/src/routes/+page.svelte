<script>
  import {
    telemetry,
    link,
    linkError,
    deviceOnline,
    stale,
    settings,
    configured,
    saveSettings,
    authFailed,
    connect,
    command,
    setParam,
    mode,
    DEFAULTS
  } from '$lib/link.js';
  import { theme, setTheme } from '$lib/theme.js';
  import { units, setUnits, tempUnit, abs as toTemp, delta as toDelta } from '$lib/units.js';
  import {
    MODES,
    STATE_TONE,
    STATE_BLURB,
    excessTone,
    dur,
    durMin,
    num,
    int,
    rssiWord
  } from '$lib/format.js';
  import { ICONS } from '$lib/icons.js';
  import Nav from '$lib/Nav.svelte';
  import Chart from '$lib/Chart.svelte';
  import Activity from '$lib/Activity.svelte';
  import { history, since, stats, F } from '$lib/history.js';
  import { learnTau, timeToOff, runContext } from '$lib/dryrate.js';
  import WifiPanel from '$lib/WifiPanel.svelte';
  import { onMount } from 'svelte';

  let showSettings = $state(false);
  let draft = $state({ ...DEFAULTS });
  let view = $state('status');
  let detail = $state(null);
  let range = $state(6);

  const VIEWS = [
    { id: 'status', label: 'Status', icon: 'gauge' },
    { id: 'readings', label: 'Readings', icon: 'drop' },
    { id: 'tuning', label: 'Thresholds', icon: 'sliders' },
    { id: 'charts', label: 'Charts', icon: 'chart' },
    { id: 'health', label: 'Health', icon: 'pulse' }
  ];

  // Chart definitions, shared by the grid and the detail view so the small
  // card and the expanded one can never drift into showing different
  // series for the same title.
  //
  // Derived rather than const: the climate chart's unit and conversion follow
  // the temperature setting, and a plain const is evaluated once and would
  // keep drawing Celsius on a Fahrenheit page until a reload.
  let CHARTS = $derived([
    {
      id: 'humidity',
      title: 'Humidity against its baseline',
      blurb: 'The fan runs on the gap between the two, not on humidity itself.',
      unit: '%',
      bands: F.fan,
      bandOn: 'Fan on',
      bandOff: 'Fan off',
      bandStory: 'The fan ran',
      bandIdle: 'The fan stayed off',
      series: [
        { field: F.rh, label: 'Humidity', color: 'var(--blue)', fill: true },
        { field: F.base, label: 'Baseline', color: 'var(--purple)', dash: true }
      ]
    },
    {
      // Excess sits at 0-30 while humidity sits at 62-92. Sharing one axis
      // stretched the scale from -12 to 104 and flattened both lines into
      // nothing. Its own chart, with the thresholds drawn on, answers the
      // question the reader actually has: was the fan about to run.
      id: 'excess',
      title: 'Excess above baseline',
      blurb: 'The number the control loop acts on, against its thresholds.',
      unit: '%',
      bands: F.fan,
      bandOn: 'Fan on',
      bandOff: 'Fan off',
      bandStory: 'The fan ran',
      bandIdle: 'The fan stayed off',
      refs: () => [
        { value: Number(cfg.RH_RISE_ON), label: 'starts' },
        { value: Number(cfg.RH_FALL_OFF), label: 'stops' }
      ],
      series: [{ field: F.exc, label: 'Excess', color: 'var(--accent)', fill: true }]
    },
    {
      id: 'climate',
      title: 'Temperature and dew point',
      blurb: 'Condensation forms when a surface reaches the dew point.',
      unit: $tempUnit,
      // Stored in Celsius, like everything on the wire and on the fan's
      // flash. The conversion happens here, at the edge, so a week of
      // history never has to know this setting exists.
      convert: (c) => toTemp(c, $units),
      // Explicitly "these two series are worth comparing", rather than the
      // chart sniffing for unit === '°C' as it used to. That test was a type
      // tag wearing a unit's clothes, and it stopped being true the moment
      // the unit could also be °F.
      compare: true,
      series: [
        { field: F.temp, label: 'Temperature', color: 'var(--orange)' },
        { field: F.dp, label: 'Dew point', color: 'var(--blue)' }
      ]
    },
    {
      id: 'light',
      title: 'Light level',
      blurb: 'Occupancy is inferred from the room light.',
      unit: '%',
      bands: F.lit,
      bandOn: 'Lights on',
      bandOff: 'Dark',
      bandStory: 'The room was lit',
      bandIdle: 'The room stayed dark',
      bandColor: 'var(--purple)',
      series: [{ field: F.light, label: 'Light', color: 'var(--purple)', fill: true }]
    },
    {
      id: 'fan',
      title: 'Fan speed',
      blurb: 'Measured by the tachometer, not by what the fan was told.',
      unit: 'rpm',
      bands: F.fan,
      bandOn: 'Fan on',
      bandOff: 'Fan off',
      bandStory: 'The fan ran',
      bandIdle: 'The fan stayed off',
      series: [{ field: F.rpm, label: 'Fan speed', color: 'var(--accent)', fill: true }]
    }
  ]);

  const RANGES = [
    { hours: 1, label: '1 h' },
    { hours: 6, label: '6 h' },
    { hours: 24, label: '24 h' },
    // A week is only worth offering because the fan keeps its own record on
    // flash and backfills it. Before that, this range would have been an
    // empty chart with a confident label.
    { hours: 168, label: '7 d' }
  ];

  onMount(() => {
    // First run on this browser: ask where the fan is rather than dialling
    // out with nothing and reporting the refusal as if it were a fault.
    // Served by the fan itself there is nothing to ask, `configured` is
    // already true, and this never fires.
    if (!$configured) openSettings();
  });

  // Nothing here is optimistic. Every value shown is the last one the
  // device actually reported, so a command that silently failed looks like
  // a command that silently failed rather than like a success.
  let t = $derived($telemetry);
  let cfg = $derived(t?.cfg ?? {});
  let state = $derived(t?.state ?? null);
  let tone = $derived(state ? (STATE_TONE[state] ?? 'dim') : 'dim');
  let excTone = $derived(excessTone(t?.exc, cfg.RH_RISE_ON, cfg.RH_FALL_OFF));

  // Reachable means every hop is up. In broker mode that is two -- our
  // socket to the broker, and the device's own retained availability -- and
  // in device mode it is one. Either way, anything down makes what is on
  // screen history rather than status.
  let live = $derived($link === 'online' && $deviceOnline && !$stale);

  /* When the fan will stop, learned from this room's own past runs.
     Measured at 1.2 ms over the full 6000-row history cap, so it simply
     runs on every sample rather than being throttled behind a staleness
     check that would cost more to reason about than it saves.

     Display only, and deliberately downstream of everything: nothing in
     the control path imports it, and the fan stops on a measured reading
     crossing a threshold exactly as it did before. */
  let learned = $derived(learnTau($history, { minPeak: Number(cfg.RH_RISE_ON) || undefined }));
  let runCtx = $derived(runContext($history));
  let offIn = $derived(
    state === 'RUN' && t && learned
      ? timeToOff({
          exc: t.exc,
          ran: runCtx.ran,
          lightOn: !!t.light_on,
          darkFor: runCtx.darkFor,
          learned,
          fallOff: Number(cfg.RH_FALL_OFF),
          minRunS: Number(cfg.MIN_RUN_S),
          maxRunS: Number(cfg.MAX_RUN_S),
          postLightS: Number(cfg.POST_LIGHT_S)
        })
      : null
  );

  let cardRows = $derived(since($history, 6));
  let detailRows = $derived(since($history, range));
  const ACTIVITY = {
    id: 'activity',
    title: 'Fan, light and state',
    blurb: 'One clock for the room light, the fan, and what the controller was doing.'
  };

  let detailChart = $derived(
    detail === 'activity' ? ACTIVITY : detail ? CHARTS.find((c) => c.id === detail) : null
  );

  /* Appearance. Not part of `draft`: a theme that waits for Save and
     reconnect, and that a Cancel silently reverts, is a worse control than
     no control. It applies on the press and persists on the press, which is
     also the only way to see what you are choosing. */
  /* Units, beside the theme and for the same reason: a viewer preference,
     not a device setting. The fan measures, stores and decides in Celsius
     whatever this says, so two phones can disagree and both be right. */
  const UNITS = [
    { id: 'C', label: '°C' },
    { id: 'F', label: '°F' }
  ];

  const THEMES = [
    { id: 'system', label: 'System' },
    { id: 'light', label: 'Light' },
    { id: 'dark', label: 'Dark' }
  ];

  function openSettings() {
    draft = { ...$settings };
    showSettings = true;
  }

  const CONNECT_MODES = [
    {
      id: 'device',
      label: 'The fan itself',
      blurb: 'Nothing else has to be running. No broker, no Home Assistant.'
    },
    {
      id: 'broker',
      label: 'An MQTT broker',
      blurb: 'Adds Home Assistant to the picture. Needs the broker to be up.'
    }
  ];

  function applySettings() {
    saveSettings({
      ...draft,
      port: Number(draft.port) || DEFAULTS.port,
      httpPort: Number(draft.httpPort) || 80
    });
    showSettings = false;
    connect();
  }

  function writeParam(key, value) {
    const v = Number(value);
    if (!Number.isFinite(v)) return;
    setParam(key, v);
  }

  const TUNABLES = [
    { key: 'RH_RISE_ON', label: 'Start at', unit: '%', step: 0.5, hint: 'above baseline' },
    { key: 'RH_FALL_OFF', label: 'Stop below', unit: '%', step: 0.5, hint: 'above baseline' },
    { key: 'LIGHT_ON', label: 'Light on', unit: '', step: 500, hint: 'raw reading' },
    { key: 'LIGHT_OFF', label: 'Light off', unit: '', step: 500, hint: 'raw reading' },
    { key: 'MIN_RUN_S', label: 'Minimum run', unit: 's', step: 30, hint: 'once started' },
    { key: 'MAX_RUN_S', label: 'Maximum run', unit: 's', step: 60, hint: 'then latches off' },
    { key: 'POST_LIGHT_S', label: 'After lights out', unit: 's', step: 30, hint: 'keep running' },
    { key: 'BOOST_S', label: 'Boost length', unit: 's', step: 60, hint: 'manual boost' }
  ];

  const TUNE_GROUPS = [
    {
      title: 'Humidity',
      blurb: 'The fan starts at the higher line and keeps running until the room falls through the lower one.',
      keys: ['RH_RISE_ON', 'RH_FALL_OFF']
    },
    {
      title: 'Light',
      blurb: 'Occupancy is inferred from the room light, between these two raw readings.',
      keys: ['LIGHT_ON', 'LIGHT_OFF']
    },
    {
      title: 'Timing',
      blurb: 'How long a run may last, and how long a manual boost holds.',
      keys: ['MIN_RUN_S', 'MAX_RUN_S', 'POST_LIGHT_S', 'BOOST_S']
    }
  ];

  function tunable(key) {
    return TUNABLES.find((p) => p.key === key);
  }

  // Gauge geometry. -5..20 %RH above baseline, matching the Home Assistant
  // gauge so the two never disagree about where the fan starts.
  const G_MIN = -5;
  const G_MAX = 20;
  const GX = 120;
  const GY = 118;
  const R = 92;

  function angle(v) {
    const clamped = Math.max(G_MIN, Math.min(G_MAX, Number(v) || 0));
    return -180 + ((clamped - G_MIN) / (G_MAX - G_MIN)) * 180;
  }

  function pt(deg, radius = R) {
    const rad = (deg * Math.PI) / 180;
    return [GX + radius * Math.cos(rad), GY + radius * Math.sin(rad)];
  }

  function arc(from, to) {
    const [x1, y1] = pt(from);
    const [x2, y2] = pt(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
  }

  function meterPct(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(100, ((n - G_MIN) / (G_MAX - G_MIN)) * 100));
  }

  function fmtChart(v, unit) {
    if (!Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    if (unit === 'rpm') return Math.round(n).toLocaleString();
    return n.toFixed(1);
  }

  function signalBars(dbm) {
    const n = Number(dbm);
    if (!Number.isFinite(n)) return 0;
    if (n >= -55) return 4;
    if (n >= -67) return 3;
    if (n >= -75) return 2;
    return 1;
  }

  /* A margin, not a temperature. toDelta, never toTemp: the 32 degree
     offset belongs to the scale's zero point and not to the distance
     between two readings, and applying it here would report a 4 degree
     margin as 39 -- a different claim about the room, not a rounding
     error. */
  /* stats() reads the stored rows, which are Celsius whatever the setting
     says. The chart converts its own copy, so these numbers -- printed as
     the card's headline and as the detail view's min/avg/max -- have to make
     the same trip or the headline reads "23.6 °F" beside an axis that reads
     75. Every field here is an absolute temperature, so the absolute
     conversion is the right one; a range would not be. */
  function chartStats(rows, field, chart) {
    const st = stats(rows, field);
    if (!st || !chart?.convert) return st;
    const f = chart.convert;
    return { ...st, min: f(st.min), max: f(st.max), mean: f(st.mean), last: f(st.last) };
  }

  let dewGap = $derived.by(() => {
    const gap = Number(t?.temp) - Number(t?.dp);
    return Number.isFinite(gap) ? `${toDelta(gap, $units).toFixed(1)}${$tempUnit}` : '—';
  });

  let lightPct = $derived(Math.max(0, Math.min(100, Number(t?.light_pc) || 0)));

  let boostPct = $derived.by(() => {
    const total = Number(cfg.BOOST_S);
    const left = Number(t?.boost_left) * 60;
    if (!(total > 0) || !Number.isFinite(left)) return 0;
    return Math.max(0, Math.min(100, (1 - left / total) * 100));
  });
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key !== 'Escape') return;
    if (detail) detail = null;
    else if (showSettings) showSettings = false;
  }}
/>

<svelte:head>
  <title>Smartfan</title>
</svelte:head>

<div class="app wrap">
  {#snippet modeControl(labelId)}
    <div class="mode">
      <span class="k" id={labelId}>Mode</span>
      <div class="segment" role="radiogroup" aria-labelledby={labelId}>
        {#each MODES as m (m)}
          <button
            type="button"
            role="radio"
            aria-checked={(cfg.MODE ?? 'Auto') === m}
            class:on={(cfg.MODE ?? 'Auto') === m}
            disabled={!live}
            onclick={() => setParam('MODE', m)}
          >{m}</button>
        {/each}
      </div>
      <p class="sub">
        Force on and Force off override the humidity logic until set back to
        Auto.
      </p>
    </div>
  {/snippet}

  <header>
    <div class="brand">
      <span class="mark" class:spin={!!t?.fan_on} aria-hidden="true">
        <svg viewBox="0 0 16 16">{@html ICONS.fan}</svg>
      </span>
      <div>
        <p class="eyebrow">{$settings.room}</p>
        <h1>Smartfan</h1>
      </div>
    </div>
    <div class="head-right">
      {#if $link === 'online' && $deviceOnline && !$stale}
        <span class="pill tone-ok"><i class="dot"></i>Live</span>
      {:else if $link === 'online' && $stale}
        <span class="pill tone-warn"><i class="dot"></i>No update</span>
      {:else if $link === 'online' && !$deviceOnline}
        <span class="pill tone-bad"><i class="dot"></i>Fan offline</span>
      {:else if $link === 'error'}
        <span class="pill tone-bad"><i class="dot"></i>Error</span>
      {:else}
        <span class="pill tone-dim"><i class="dot"></i>Connecting</span>
      {/if}
      <button class="icon" type="button" onclick={openSettings} aria-label="Settings">
        <svg viewBox="0 0 16 16" aria-hidden="true">{@html ICONS.sliders}</svg>
        <span>Settings</span>
      </button>
    </div>
  </header>

  {#if t}
    <div class="nav-slot">
      <Nav
        items={VIEWS}
        current={view}
        onSelect={(id) => (view = id)}
        alert={t.fault ? 'status' : null}
      />
    </div>
  {/if}

  <div class="main-col">
  {#if $linkError && $authFailed}
    <div class="banner bad">
      <p>{$linkError}</p>
      <p class="banner-sub">
        The Home Assistant Mosquitto add-on does not allow anonymous
        connections, so a username and password are required.
      </p>
      <button onclick={openSettings}>Open settings</button>
    </div>
  {:else if $linkError}
    <p class="banner bad">{$linkError}</p>
  {:else if $link === 'online' && !$deviceOnline}
    <p class="banner bad">
      {#if $mode === 'broker'}
        The broker is reachable but the fan is not reporting. It keeps running
        on its own logic when the network is down — this is a reporting
        problem, not a fan problem.
      {:else}
        The fan stopped answering. It keeps running on its own logic either
        way — this is a reporting problem, not a fan problem.
      {/if}
    </p>
  {:else if $stale}
    <p class="banner warn">
      Nothing heard for over 90 seconds. The fan
      {$mode === 'broker' ? 'publishes every 30' : 'is asked every 2'}.
    </p>
  {/if}

  {#if !t && !$configured}
    <div class="card empty">
      <p class="lead">Point the app at your fan to get started.</p>
      <p class="sub">
        Everything stays on your network. The app talks straight to the fan —
        or to your MQTT broker, if you have one. No account, no cloud, and no
        server in between either way.
      </p>
      <button class="primary setup" onclick={openSettings}>Set up connection</button>
    </div>
  {:else if !t}
    <div class="card empty">
      <p class="lead">Waiting for the first reading…</p>
      <p class="sub">
        {#if $mode === 'broker'}
          The fan reports every 30 seconds. If nothing arrives, check the
          broker settings.
        {:else}
          The fan is asked every 2 seconds. If nothing arrives, check the
          address in Settings.
        {/if}
      </p>
    </div>
  {:else}
      <main class="stage">
        {#if view === 'status'}
          <div class="status-layout">
            <section class="hero card" class:is-boost={state === 'BOOST'} class:faulty={t.fault} class:dimmed={!live}>
              <div class="gauge">
                <svg viewBox="0 0 240 150" role="img" aria-label="Humidity above baseline">
                  <path d={arc(-180, 0)} class="track" />
                  <path d={arc(angle(G_MIN), angle(cfg.RH_FALL_OFF ?? 3))} class="band calm" />
                  <path
                    d={arc(angle(cfg.RH_FALL_OFF ?? 3), angle(cfg.RH_RISE_ON ?? 8))}
                    class="band warn"
                  />
                  <path d={arc(angle(cfg.RH_RISE_ON ?? 8), angle(G_MAX))} class="band hot" />
                  {#each [cfg.RH_FALL_OFF ?? 3, cfg.RH_RISE_ON ?? 8] as tick, i (i)}
                    <line
                      x1={pt(angle(tick), R - 2)[0]}
                      y1={pt(angle(tick), R - 2)[1]}
                      x2={pt(angle(tick), R + 11)[0]}
                      y2={pt(angle(tick), R + 11)[1]}
                      class="tick"
                    />
                  {/each}
                  <g
                    class="needle"
                    style="transform: rotate({angle(t.exc) + 180}deg); transform-origin: {GX}px {GY}px"
                  >
                    <line x1={GX - (R - 26)} y1={GY} x2={GX - 8} y2={GY} />
                    <circle cx={GX} cy={GY} r="6" class="hub" />
                    <circle cx={GX} cy={GY} r="2.4" class="hub-core" />
                  </g>
                </svg>
                <div class="reading tone-{excTone}">{num(t.exc, 1, '%')}</div>
                <div class="reading-sub">above baseline</div>
              </div>

              <div class="hero-state">
                <p class="eyebrow">Controller</p>
                <p class="state tone-{tone}">{state}</p>
                <p class="blurb">{STATE_BLURB[state] ?? ''}</p>
                {#if state === 'BOOST' && t.boost_left > 0}
                  <p class="blurb tone-active">{dur(t.boost_left * 60)} remaining</p>
                  <div class="boost-bar" aria-hidden="true"><i style="width: {boostPct}%"></i></div>
                {/if}
                {#if offIn}
                  {#if offIn.neverDries}
                    <p class="blurb tone-warn">
                      Settles above the stop point — this run ends on the
                      {durMin(cfg.MAX_RUN_S / 60)} cap, in {dur(offIn.capIn)}
                    </p>
                  {:else if offIn.capped}
                    <p class="blurb tone-warn">
                      {dur(offIn.capIn)} to the {durMin(cfg.MAX_RUN_S / 60)} cap, and
                      drying needs about {dur(offIn.needs)}
                    </p>
                  {:else if offIn.lightOn}
                    <p class="blurb tone-active">
                      About {dur(offIn.secs)} once the light goes off
                    </p>
                  {:else}
                    <p class="blurb tone-active">About {dur(offIn.secs)} left</p>
                  {/if}
                  <p class="learned">
                    {#if offIn.waiting && !offIn.capped}
                      waiting on the light, not the humidity
                    {:else if offIn.hi - offIn.lo >= 60}
                      {dur(offIn.lo)}–{dur(offIn.hi)} across {offIn.runs}
                      past shower{offIn.runs === 1 ? '' : 's'}
                    {:else}
                      <!-- Runs this consistent give quartiles a minute apart,
                           and "24 min-24 min" reads as a broken widget rather
                           than as a confident one.

                           "Showers", not "runs", and the word is doing real
                           work: only runs that peaked past RH_RISE_ON are
                           learned from, so this count is events where the
                           humidity actually pumped -- not every time the fan
                           happened to spin. -->
                      learned from {offIn.runs} past shower{offIn.runs === 1 ? '' : 's'}
                    {/if}
                  </p>
                {/if}
              </div>

              <div class="facts">
                <div class="fact">
                  <span class="k">Fan</span>
                  <span class="fact-v {t.fan_on ? 'tone-active' : ''}">
                    {t.fan_on ? `${int(t.rpm)} rpm` : 'Off'}
                  </span>
                </div>
                <div class="fact">
                  <span class="k">Light</span>
                  <span class="fact-v {t.light_on ? 'tone-warn' : ''}">
                    {t.light_on ? 'On' : 'Off'}
                  </span>
                </div>
                <div class="fact" class:alert={t.fault}>
                  <span class="k">Fault</span>
                  {#if t.fault}
                    <span class="fact-v tone-bad">{t.reason || 'Needs attention'}</span>
                    <button
                      type="button"
                      class="clear"
                      onclick={() => command('clear')}
                      disabled={!live}
                    >
                      Clear fault
                    </button>
                  {:else}
                    <span class="fact-v">None</span>
                  {/if}
                </div>
              </div>
            </section>

            <section class="dock card">
              <h2>Control</h2>
              <div class="controls">
                <button class="primary boost" type="button" onclick={() => command('boost')} disabled={!live}>
                  <span class="boost-ico" class:spin={state === 'BOOST'} aria-hidden="true">
                    <svg viewBox="0 0 16 16">{@html ICONS.fan}</svg>
                  </span>
                  <span class="boost-copy">
                    <span class="boost-title">Boost</span>
                    <span class="boost-sub">{dur(cfg.BOOST_S)}</span>
                  </span>
                </button>
                <button type="button" onclick={() => command('cancel')} disabled={!live || state !== 'BOOST'}>
                  Cancel boost
                </button>
                <div class="quiet-row">
                  <button type="button" class="quiet" onclick={() => command('relearn')} disabled={!live}>
                    Relearn baseline
                  </button>
                  <button
                    type="button"
                    class="quiet"
                    class:danger={t.fault}
                    onclick={() => command('clear')}
                    disabled={!live || !t.fault}
                  >
                    Clear fault
                  </button>
                </div>
              </div>
              {@render modeControl('mode-label')}
            </section>

            <section class="glance">
              <div class="glance-item">
                <span class="k">Humidity</span>
                <span class="v">{num(t.rh, 1, '%')}</span>
              </div>
              <div class="glance-item">
                <span class="k">Temperature</span>
                <span class="v">{num(toTemp(t.temp, $units), 1, $tempUnit)}</span>
              </div>
              <div class="glance-item">
                <span class="k">Light level</span>
                <span class="v">{num(t.light_pc, 1, '%')}</span>
              </div>
              <div class="glance-item">
                <span class="k">Run today</span>
                <span class="v">{durMin(t.run_min)}</span>
              </div>
            </section>

            <section class="card activity-card">
              <div class="chart-head">
                <div class="chart-title">
                  <h2>Fan, light and state</h2>
                  <p class="sub">The same hours, on one clock.</p>
                </div>
                <button
                  type="button"
                  class="icon"
                  onclick={() => (detail = 'activity')}
                  aria-label="Fan, light and state — open detail"
                >
                  Detail
                </button>
              </div>
              <Activity rows={cardRows} compact />
            </section>
          </div>
        {/if}

        {#if view === 'readings'}
          <div class="readings">
            <section class="panel card wide">
              <h2>Humidity</h2>
              <div class="read-main">
                <div class="read-hero">
                  <span class="k">Humidity</span>
                  <span class="v display">{num(t.rh, 1, '%')}</span>
                </div>
                <div class="read-side">
                  <div>
                    <span class="k">Baseline</span>
                    <span class="v">{num(t.base, 1, '%')}</span>
                    <span class="sub">what this room sits at</span>
                  </div>
                  <div>
                    <span class="k">Excess</span>
                    <span class="v tone-{excTone}">{num(t.exc, 1, '%')}</span>
                    <span class="sub">fan starts at {num(cfg.RH_RISE_ON, 1, '%')}</span>
                  </div>
                </div>
              </div>
              <div class="meter" aria-hidden="true">
                <div class="meter-track">
                  <i class="z calm" style="width: {meterPct(cfg.RH_FALL_OFF ?? 3)}%"></i>
                  <i
                    class="z warn"
                    style="width: {Math.max(0, meterPct(cfg.RH_RISE_ON ?? 8) - meterPct(cfg.RH_FALL_OFF ?? 3))}%"
                  ></i>
                  <i class="z hot"></i>
                </div>
                <i class="pip" style="left: {meterPct(t.exc)}%"></i>
                <div class="meter-labels">
                  <span>calm</span>
                  <span>stops {num(cfg.RH_FALL_OFF, 1, '%')}</span>
                  <span>starts {num(cfg.RH_RISE_ON, 1, '%')}</span>
                  <span>running</span>
                </div>
              </div>
              <div class="read-row">
                <div>
                  <span class="k">Temperature</span>
                  <span class="v">{num(toTemp(t.temp, $units), 1, $tempUnit)}</span>
                </div>
                <div>
                  <span class="k">Dew point</span>
                  <span class="v">{num(toTemp(t.dp, $units), 1, $tempUnit)}</span>
                </div>
                <div>
                  <span class="k">Above dew point</span>
                  <span class="v">{dewGap}</span>
                  <span class="sub">margin before condensation</span>
                </div>
              </div>
            </section>

            <section class="panel card">
              <h2>Light</h2>
              <div class="read-row">
                <div>
                  <span class="k">Light level</span>
                  <span class="v display">{num(t.light_pc, 1, '%')}</span>
                </div>
                <div>
                  <span class="k">Reads as</span>
                  <span class="v">{t.light_on ? 'On' : 'Off'}</span>
                  <span class="sub">{t.light_on ? 'room is lit' : 'room is dark'}</span>
                </div>
                <div>
                  <span class="k">Raw reading</span>
                  <span class="v mono-v">{int(t.light)}</span>
                  <span class="sub">on above {int(cfg.LIGHT_ON)}</span>
                </div>
              </div>
              <div class="level" aria-hidden="true"><i style="width: {lightPct}%"></i></div>
            </section>

            <section class="panel card">
              <h2>Fan</h2>
              <div class="read-row">
                <div>
                  <span class="k">Fan speed</span>
                  <span class="v display">{int(t.rpm)}</span>
                  <span class="sub">rpm</span>
                </div>
                <div>
                  <span class="k">Running</span>
                  <span class="v">{t.fan_on ? 'Yes' : 'No'}</span>
                </div>
                <div>
                  <span class="k">Run today</span>
                  <span class="v">{durMin(t.run_min)}</span>
                </div>
                <div>
                  <span class="k">Boost remaining</span>
                  <span class="v">{t.boost_left > 0 ? dur(t.boost_left * 60) : '—'}</span>
                  <span class="sub">{state === 'BOOST' ? 'boosting now' : 'not boosting'}</span>
                </div>
              </div>
            </section>
          </div>
        {/if}

        {#if view === 'tuning'}
          <div class="lede">
            <h2>Thresholds</h2>
            <p class="hint">
              The device validates every value against its own ranges and rejects
              anything outside them. A change only counts once it comes back in
              the next reading.
            </p>
          </div>
          <div class="tune-groups">
            {#each TUNE_GROUPS as group (group.title)}
              <section class="card tune-group" class:wide={group.keys.length > 2}>
                <h3>{group.title}</h3>
                <p class="sub">{group.blurb}</p>
                {#if group.title === 'Humidity'}
                  <div class="meter slim" aria-hidden="true">
                    <div class="meter-track">
                      <i class="z calm" style="width: {meterPct(cfg.RH_FALL_OFF ?? 3)}%"></i>
                      <i
                        class="z warn"
                        style="width: {Math.max(0, meterPct(cfg.RH_RISE_ON ?? 8) - meterPct(cfg.RH_FALL_OFF ?? 3))}%"
                      ></i>
                      <i class="z hot"></i>
                    </div>
                  </div>
                {/if}
                <div class="tune-grid">
                  {#each group.keys as key (key)}
                    {@const p = tunable(key)}
                    <div class="tune">
                      <label class="k" for={p.key}>{p.label}</label>
                      <div class="stepper">
                        <button
                          type="button"
                          aria-label="Decrease {p.label}"
                          disabled={!live}
                          onclick={() => writeParam(p.key, Number(cfg[p.key]) - p.step)}
                        >−</button>
                        <input
                          id={p.key}
                          type="number"
                          step={p.step}
                          disabled={!live}
                          value={cfg[p.key] ?? ''}
                          onchange={(e) => writeParam(p.key, e.currentTarget.value)}
                        />
                        <button
                          type="button"
                          aria-label="Increase {p.label}"
                          disabled={!live}
                          onclick={() => writeParam(p.key, Number(cfg[p.key]) + p.step)}
                        >+</button>
                      </div>
                      <span class="sub">{p.unit ? `${p.unit} · ` : ''}{p.hint}</span>
                    </div>
                  {/each}
                </div>
              </section>
            {/each}
          </div>
          <section class="card tune-mode">
            {@render modeControl('mode-t-label')}
          </section>
        {/if}

        {#if view === 'charts'}
          <h2>Charts</h2>
          <p class="hint">
            {#if $mode === 'device'}
              A week, kept by the fan itself and filled in when this app
              connects — so a phone opening it for the first time still sees
              what happened.
            {:else}
              Collected while this app is open, and kept in this browser. A
              break in the line is a period nobody recorded.
            {/if}
            Tap a chart, then drag across it to read any moment.
          </p>
          <div class="chart-grid">
            <div
              class="chart-card card wide"
              role="button"
              tabindex="0"
              aria-label="Fan, light and state — open detail"
              onclick={() => (detail = 'activity')}
              onkeydown={(e) =>
                (e.key === 'Enter' || e.key === ' ') &&
                (e.preventDefault(), (detail = 'activity'))}
            >
              <div class="chart-head">
                <div class="chart-title">
                  <h3>Fan, light and state</h3>
                </div>
                <span class="expand" aria-hidden="true">
                  <svg viewBox="0 0 16 16">{@html ICONS.expand}</svg>
                </span>
              </div>
              <Activity rows={cardRows} compact />
            </div>
            {#each CHARTS as c (c.id)}
              {@const st = chartStats(cardRows, c.series[0].field, c)}
              <div
                class="chart-card card"
                role="button"
                tabindex="0"
                aria-label="{c.title} — open detail"
                onclick={() => (detail = c.id)}
                onkeydown={(e) =>
                  (e.key === 'Enter' || e.key === ' ') &&
                  (e.preventDefault(), (detail = c.id))}
              >
                <div class="chart-head">
                  <div class="chart-title">
                    <h3>{c.title}</h3>
                    {#if st}
                      <p class="chart-now">
                        {fmtChart(st.last, c.unit)}<span>{c.unit === 'rpm' ? 'rpm' : c.unit}</span>
                      </p>
                    {/if}
                  </div>
                  <span class="expand" aria-hidden="true">
                    <svg viewBox="0 0 16 16">{@html ICONS.expand}</svg>
                  </span>
                </div>
                <Chart
                  rows={cardRows}
                  series={c.series}
                  bands={c.bands}
                  bandOn={c.bandOn ?? ''}
                  bandOff={c.bandOff ?? ''}
                  bandStory={c.bandStory ?? ''}
                  bandIdle={c.bandIdle ?? ''}
                  bandColor={c.bandColor ?? 'var(--accent)'}
                  refs={c.refs ? c.refs() : []}
                  unit={c.unit}
                  convert={c.convert}
                  compare={c.compare ?? false}
                  height={150}
                  compact
                />
              </div>
            {/each}
          </div>
        {/if}

        {#if view === 'health'}
          <div class="health">
            <section class="panel card">
              <h2>Link</h2>
              <div class="link-hero">
                <div>
                  <span class="k">Wi-Fi</span>
                  <span class="v display">{rssiWord(t.rssi)}</span>
                  <span class="sub">{int(t.rssi)} dBm</span>
                </div>
                <div class="bars" aria-hidden="true">
                  {#each [1, 2, 3, 4] as n (n)}
                    <i class:lit={signalBars(t.rssi) >= n}></i>
                  {/each}
                </div>
              </div>
              <div class="read-row">
                <div>
                  <span class="k">Reconnects</span>
                  <span class="v">{int(t.reconn)}</span>
                </div>
                <div>
                  <span class="k">Last outage</span>
                  <span class="v">{dur(t.outage_s)}</span>
                </div>
              </div>
            </section>

            <section class="panel card" class:faulty={t.fault}>
              <h2>Device</h2>
              <div class="link-hero">
                <div>
                  <span class="k">Uptime</span>
                  <span class="v display">{durMin(t.up_min)}</span>
                </div>
              </div>
              <div class="read-row">
                <div>
                  <span class="k">Last restart</span>
                  <span class="v small-v">{t.boot ?? '—'}</span>
                </div>
                <div>
                  <span class="k">Build</span>
                  <span class="v mono-v">{t.fw ?? '—'}</span>
                </div>
                <div>
                  <span class="k">Fault</span>
                  {#if t.fault}
                    <span class="v small-v tone-bad">{t.reason || 'Yes'}</span>
                  {:else}
                    <span class="v">None</span>
                  {/if}
                </div>
              </div>
            </section>
          </div>
        {/if}
      </main>
  {/if}
  </div>

  {#if detailChart}
    <!-- The backdrop is decoration, not a control. Giving it a role and a
         label put a second "Close chart" in the accessibility tree next to
         the real button, so a screen reader announced two identical
         controls. Keyboard users close with Escape or the button. -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="scrim" aria-hidden="true" onclick={() => (detail = null)}></div>
    <div class="sheet wide card" role="dialog" aria-label={detailChart.title}>
      <div class="detail-head">
        <div>
          <h3>{detailChart.title}</h3>
          <p class="sub">{detailChart.blurb}</p>
        </div>
        <button class="icon" onclick={() => (detail = null)} aria-label="Close chart">
          Close
        </button>
      </div>

      <div class="ranges" role="group" aria-label="Time range">
        {#each RANGES as r (r.hours)}
          <button class:on={range === r.hours} onclick={() => (range = r.hours)}>
            {r.label}
          </button>
        {/each}
      </div>

      {#if detailChart.id === 'activity'}
        <Activity rows={detailRows} />
      {:else}
      <Chart
        rows={detailRows}
        series={detailChart.series}
        bands={detailChart.bands}
        bandOn={detailChart.bandOn ?? ''}
        bandOff={detailChart.bandOff ?? ''}
        bandStory={detailChart.bandStory ?? ''}
        bandIdle={detailChart.bandIdle ?? ''}
        bandColor={detailChart.bandColor ?? 'var(--accent)'}
        refs={detailChart.refs ? detailChart.refs() : []}
        unit={detailChart.unit}
        convert={detailChart.convert}
        compare={detailChart.compare ?? false}
        height={250}
      />

      <div class="stats">
        {#each detailChart.series as sdef (sdef.field)}
          {@const st = chartStats(detailRows, sdef.field, detailChart)}
          <div class="statrow">
            <span class="name"><i style="background:{sdef.color}"></i>{sdef.label}</span>
            {#if st}
              <span class="nums">
                <b>{fmtChart(st.last, detailChart.unit)}</b>
                <span>min {fmtChart(st.min, detailChart.unit)}</span>
                <span>avg {fmtChart(st.mean, detailChart.unit)}</span>
                <span>max {fmtChart(st.max, detailChart.unit)}</span>
              </span>
            {:else}
              <span class="nums"><span>no data in this range</span></span>
            {/if}
          </div>
        {/each}
        <p class="sub samples">
          {detailRows.length} sample{detailRows.length === 1 ? '' : 's'} in the
          last {range} h
        </p>
      </div>
      {/if}
    </div>
  {/if}

  {#if showSettings}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="scrim" aria-hidden="true" onclick={() => (showSettings = false)}></div>
    <div class="sheet card" role="dialog" aria-label="Settings">
      <div class="grabber" aria-hidden="true"></div>
      <h3>Connection</h3>
      <p class="sub">
        Nothing leaves the network either way. The difference is what has to be
        running.
      </p>

      <div class="modes" role="radiogroup" aria-label="How to reach the fan">
        {#each CONNECT_MODES as m (m.id)}
          <button
            class="mode"
            class:active={draft.mode === m.id}
            role="radio"
            aria-checked={draft.mode === m.id}
            onclick={() => (draft.mode = m.id)}
          >
            <span class="mode-name">{m.label}</span>
            <span class="mode-blurb">{m.blurb}</span>
          </button>
        {/each}
      </div>

      {#if draft.mode === 'broker'}
        <label for="host">Broker address</label>
        <input id="host" bind:value={draft.host} autocapitalize="off" autocorrect="off" />

        <label for="port">WebSocket port</label>
        <input id="port" type="number" bind:value={draft.port} />
        <p class="sub">
          1884 on the Home Assistant Mosquitto add-on. Port 1883 is plain TCP
          and a browser cannot open it.
        </p>

        <label for="room">Room</label>
        <input id="room" bind:value={draft.room} autocapitalize="off" autocorrect="off" />

        <label for="user">Username</label>
        <input id="user" bind:value={draft.username} autocapitalize="off" autocorrect="off" />

        <label for="pass">Password</label>
        <input id="pass" type="password" bind:value={draft.password} />
        <p class="sub">
          Stored in this browser only. Anyone who can open this page and its dev
          tools can read it — give this account the narrowest access that works.
        </p>
      {:else}
        <label for="host">Fan address</label>
        <input id="host" bind:value={draft.host} autocapitalize="off" autocorrect="off" />

        <label for="httpPort">Port</label>
        <input id="httpPort" type="number" bind:value={draft.httpPort} />
        <p class="sub">
          The fan answers on port 80 at <code>fan.local</code>, and this page is
          served from the same place. There is no account and no password: the
          protection is that the fan is only reachable from your network.
        </p>
      {/if}

      <WifiPanel open={showSettings} holdS={Number(cfg.HOLD_PRESS_S) || 8} />

      <h3 class="section">Appearance</h3>
      <p class="sub">
        System follows the phone's own light or dark setting, and keeps following
        it. Applies straight away and stays in this browser: the fan is not told
        and does not care, so another phone can be on a different one.
      </p>

      <p class="setting-label">Temperature</p>
      <div class="modes two" role="radiogroup" aria-label="Temperature units">
        {#each UNITS as u (u.id)}
          <button
            class="mode"
            class:active={$units === u.id}
            role="radio"
            aria-checked={$units === u.id}
            onclick={() => setUnits(u.id)}
          >
            <span class="mode-name">{u.label}</span>
          </button>
        {/each}
      </div>

      <p class="setting-label">Colour scheme</p>
      <div class="modes three" role="radiogroup" aria-label="Colour scheme">
        {#each THEMES as opt (opt.id)}
          <button
            class="mode"
            class:active={$theme === opt.id}
            role="radio"
            aria-checked={$theme === opt.id}
            onclick={() => setTheme(opt.id)}
          >
            <span class="mode-name">{opt.label}</span>
          </button>
        {/each}
      </div>

      <div class="sheet-actions">
        <button onclick={() => (showSettings = false)}>Cancel</button>
        <button class="primary" onclick={applySettings}>Save and reconnect</button>
      </div>
    </div>
  {/if}
</div>

<style>
  /* The shell is the app. .wrap's old column is neutralised here so the
     rail can own the left edge and the stage can use the rest. */
  .app.wrap {
    max-width: none;
    margin: 0;
    padding: 0;
    min-height: 100dvh;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 16px 18px 6px;
    max-width: 760px;
    margin: 0 auto;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .mark {
    width: 42px;
    height: 42px;
    border-radius: 13px;
    background: var(--accent-dim);
    color: var(--accent);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    border: 1px solid var(--accent-soft);
  }
  .mark svg,
  .boost-ico svg,
  button.icon svg {
    fill: none;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .mark svg {
    width: 22px;
    height: 22px;
    stroke-width: 1.6;
  }
  .mark.spin svg,
  .boost-ico.spin svg {
    animation: spin 1.35s linear infinite;
    transform-origin: center;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  h1 {
    font-size: 1.35rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.03em;
    line-height: 1.1;
  }

  .head-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
  }

  button.icon {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-dim);
    border-radius: 999px;
    padding: 7px 12px 7px 10px;
    font-size: 0.82rem;
    transition: background 0.2s, border-color 0.2s, color 0.2s;
  }
  button.icon svg {
    width: 14px;
    height: 14px;
    stroke-width: 1.7;
  }
  button.icon:hover {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
    color: var(--text);
  }

  .main-col {
    max-width: 760px;
    margin: 0 auto;
    padding: 8px 18px calc(104px + env(safe-area-inset-bottom));
  }

  .nav-slot {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 8;
    background: color-mix(in srgb, var(--bg-card) 92%, transparent);
    backdrop-filter: blur(16px);
    border-top: 1px solid var(--border);
    padding: 4px 6px calc(6px + env(safe-area-inset-bottom));
  }

  @media (min-width: 960px) {
    .app {
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
      grid-template-rows: auto minmax(0, 1fr);
      grid-template-areas:
        'rail stage'
        'nav stage';
      height: 100dvh;
      overflow: hidden;
    }
    .app:not(:has(.nav-slot)) {
      grid-template-areas: 'rail stage';
      grid-template-rows: minmax(0, 1fr);
    }
    header {
      grid-area: rail;
      max-width: none;
      margin: 0;
      flex-direction: column;
      align-items: stretch;
      justify-content: flex-start;
      gap: 18px;
      padding: 26px 16px 8px;
      background: var(--bg-card);
      border-right: 1px solid var(--border);
    }
    .head-right {
      flex-direction: column;
      align-items: stretch;
    }
    .head-right .pill {
      align-self: flex-start;
    }
    header button.icon {
      justify-content: center;
      width: 100%;
      border-radius: 10px;
      padding: 9px 12px;
    }
    .nav-slot {
      grid-area: nav;
      position: static;
      border-top: 0;
      border-right: 1px solid var(--border);
      background: var(--bg-card);
      backdrop-filter: none;
      padding: 6px 12px 28px;
      overflow: auto;
    }
    .main-col {
      grid-area: stage;
      max-width: none;
      margin: 0;
      min-height: 0;
      overflow: auto;
      padding: 28px 36px 56px;
    }
    .app:not(:has(.nav-slot)) header {
      border-bottom: 0;
    }
  }

  h2 {
    display: block;
    margin: 0 0 12px;
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    text-transform: none;
    color: var(--text);
  }
  h2::after {
    display: none;
  }

  .lede {
    margin-bottom: 14px;
  }
  .lede h2 {
    margin-bottom: 4px;
  }

  /* --- banners --------------------------------------------------------- */

  .banner {
    border-radius: var(--radius);
    padding: 14px 16px;
    margin: 4px 0 14px;
    font-size: 0.9rem;
    border: 1px solid;
  }
  .banner.bad {
    background: var(--bad-wash);
    border-color: var(--bad-edge);
    color: var(--bad-text);
  }
  .banner.warn {
    background: var(--warn-wash);
    border-color: var(--warn-edge);
    color: var(--warn-text);
  }
  .banner p {
    margin: 0;
  }
  .banner-sub {
    font-size: 0.82rem;
    margin: 6px 0 11px !important;
  }
  .banner button {
    background: transparent;
    border: 1px solid currentColor;
    color: inherit;
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 0.82rem;
  }

  .empty {
    text-align: center;
    padding: 56px 24px;
    margin-top: 8vh;
  }
  .empty .lead {
    margin: 0 0 6px;
    font-size: 1.2rem;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .empty .sub {
    max-width: 42ch;
    margin: 0 auto;
  }
  .empty .setup {
    margin-top: 18px;
    padding: 12px 22px;
    border-radius: 12px;
    font-size: 0.95rem;
    font-weight: 600;
  }

  .stage {
    container-type: inline-size;
    container-name: stage;
  }

  /* --- status ---------------------------------------------------------- */

  .status-layout {
    display: grid;
    gap: 14px;
  }
  @container stage (min-width: 760px) {
    .status-layout {
      grid-template-columns: minmax(0, 1.4fr) minmax(270px, 0.78fr);
      align-items: start;
    }
    .glance,
    .activity-card {
      grid-column: 1 / -1;
    }
  }

  .hero {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    column-gap: 8px;
    row-gap: 4px;
    align-items: center;
    padding: 18px 18px 14px;
    position: relative;
    overflow: hidden;
    background:
      radial-gradient(80% 90% at 0% 0%, var(--hero-glow), transparent 58%),
      var(--bg-card);
  }
  .hero.is-boost {
    border-color: var(--accent-soft);
  }
  .hero.faulty {
    border-color: var(--bad-edge);
  }

  .gauge {
    position: relative;
    width: 210px;
    text-align: center;
    justify-self: center;
  }
  .gauge svg {
    width: 210px;
    height: auto;
    display: block;
    overflow: visible;
  }
  .track {
    fill: none;
    stroke: var(--bg-card-2);
    stroke-width: 16;
    stroke-linecap: round;
  }
  .band {
    fill: none;
    stroke-width: 8;
    stroke-linecap: butt;
  }
  .band.calm {
    stroke: var(--good);
  }
  .band.warn {
    stroke: var(--warn);
  }
  .band.hot {
    stroke: var(--bad);
  }
  .tick {
    stroke: var(--text);
    stroke-width: 1.6;
    stroke-linecap: round;
  }
  .needle {
    transform-box: view-box;
    transition: transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .needle line {
    stroke: var(--text);
    stroke-width: 3;
    stroke-linecap: round;
  }
  .hub {
    fill: var(--bg-card);
    stroke: var(--text);
    stroke-width: 2;
  }
  .hub-core {
    fill: var(--accent);
  }
  .reading {
    font-size: 2.15rem;
    font-weight: 600;
    line-height: 1;
    margin-top: -18px;
    letter-spacing: -0.04em;
    font-variant-numeric: tabular-nums;
  }
  .reading-sub {
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-top: 3px;
  }

  .hero-state {
    min-width: 0;
    padding: 8px 8px 8px 4px;
  }
  .state {
    font-size: clamp(2.2rem, 5vw, 3rem);
    font-weight: 600;
    line-height: 0.95;
    margin: 4px 0 8px;
    letter-spacing: -0.045em;
  }
  /* The estimate's own spread. Deliberately quieter than the number above
     it: tau moves with the door, the season and the weather outside, and a
     single confident figure would claim a precision this does not have. */
  .learned {
    margin: 2px 0 0;
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }
  .blurb {
    margin: 0;
    color: var(--text-dim);
    font-size: 0.92rem;
  }
  .reason {
    margin: 8px 0 0;
    color: var(--text-dim);
    font-size: 0.8rem;
  }
  .boost-bar {
    margin-top: 10px;
    height: 4px;
    max-width: 160px;
    border-radius: 999px;
    background: var(--bg-card-2);
    overflow: hidden;
  }
  .boost-bar i {
    display: block;
    height: 100%;
    background: var(--accent);
    border-radius: inherit;
  }

  .facts {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0;
    margin-top: 8px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .fact {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    padding-right: 10px;
  }
  .fact + .fact {
    padding-left: 14px;
    border-left: 1px solid var(--border);
  }
  .fact-v {
    font-size: 1.02rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .fact.alert {
    grid-column: 1 / -1;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 14px;
    margin-top: 4px;
    padding: 10px 0 0;
    border-top: 1px solid var(--bad-edge);
    border-left: 0;
  }
  .fact.alert .fact-v {
    font-size: 0.95rem;
    line-height: 1.35;
  }
  button.clear {
    margin-left: auto;
    background: var(--bad-wash);
    border: 1px solid var(--bad-edge);
    color: var(--bad-text);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 0.84rem;
    font-weight: 600;
    min-height: 40px;
  }
  button.clear:hover:not(:disabled) {
    border-color: var(--bad-text);
  }
  button.clear:disabled {
    opacity: 0.4;
  }

  @container stage (max-width: 520px) {
    .hero {
      grid-template-columns: 1fr;
      justify-items: center;
    }
    .hero-state {
      text-align: center;
      padding: 0 8px 4px;
    }
    .boost-bar {
      margin-inline: auto;
    }
    .facts {
      width: 100%;
    }
  }

  .dock {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .controls {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .controls > button,
  .sheet-actions button {
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 13px 14px;
    font-size: 0.92rem;
    font-weight: 500;
    min-height: 48px;
    transition: background 0.2s, border-color 0.2s;
  }
  .controls > button:hover:not(:disabled),
  .sheet-actions button:hover:not(:disabled) {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
  }
  .controls button:disabled,
  .sheet-actions button:disabled,
  .stepper button:disabled {
    opacity: 0.38;
    cursor: not-allowed;
  }
  button.primary {
    background: var(--accent-dim);
    border-color: var(--accent-soft);
    color: var(--accent);
  }
  button.primary:hover:not(:disabled) {
    background: var(--accent-soft);
    border-color: var(--accent);
  }
  .boost {
    display: flex;
    align-items: center;
    gap: 12px;
    text-align: left;
    min-height: 68px;
  }
  .boost-ico {
    width: 38px;
    height: 38px;
    border-radius: 11px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--accent-soft);
    flex: none;
  }
  .boost-ico svg {
    width: 18px;
    height: 18px;
    stroke-width: 1.6;
  }
  .boost-copy {
    display: flex;
    flex-direction: column;
    line-height: 1.15;
  }
  .boost-title {
    font-size: 1.02rem;
    font-weight: 600;
  }
  .boost-sub {
    margin-top: 2px;
    font-size: 0.78rem;
    color: var(--text-dim);
    font-weight: 500;
  }
  .quiet-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  button.quiet {
    background: transparent;
    border-color: transparent;
    color: var(--text-dim);
    min-height: 42px;
    font-size: 0.84rem;
    font-weight: 500;
  }
  button.quiet:hover:not(:disabled) {
    background: var(--bg-card-hover);
    color: var(--text);
  }
  button.danger:not(:disabled) {
    color: var(--bad-text);
  }

  .mode {
    margin-top: 16px;
    display: grid;
    gap: 8px;
  }
  .segment {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 4px;
    padding: 4px;
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    border-radius: 12px;
  }
  .segment button {
    appearance: none;
    background: transparent;
    border: 0;
    border-radius: 9px;
    color: var(--text);
    font-size: 0.84rem;
    font-weight: 500;
    min-height: 42px;
    padding: 8px 4px;
  }
  .segment button.on {
    background: var(--bg-card);
    color: var(--accent);
    box-shadow: inset 0 0 0 1px var(--border-strong);
  }
  .segment button:hover:not(:disabled):not(.on) {
    background: var(--bg-card-hover);
  }

  .glance {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  @container stage (min-width: 640px) {
    .glance {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
  }
  .glance-item {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 13px 14px 12px;
  }
  .glance-item .v {
    display: block;
    margin-top: 3px;
    font-size: 1.28rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }

  /* --- readings / health ---------------------------------------------- */

  .readings,
  .health,
  .tune-groups {
    display: grid;
    gap: 14px;
  }
  @container stage (min-width: 680px) {
    .readings,
    .health {
      grid-template-columns: 1fr 1fr;
    }
    .panel.wide,
    .tune-group.wide {
      grid-column: 1 / -1;
    }
  }

  .read-main {
    display: grid;
    gap: 8px 24px;
    align-items: end;
  }
  @container stage (min-width: 560px) {
    .read-main {
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    }
  }
  .read-side,
  .read-row {
    display: grid;
    gap: 14px 18px;
  }
  .read-side {
    grid-template-columns: 1fr 1fr;
  }
  .read-row {
    grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid var(--border);
  }
  .v {
    display: block;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
  }
  .read-side .v,
  .read-row .v,
  .link-hero .v {
    font-size: 1.2rem;
    font-weight: 600;
    line-height: 1.15;
  }
  .v.display {
    font-size: clamp(2.05rem, 4.4vw, 2.85rem);
    font-weight: 600;
    letter-spacing: -0.045em;
    line-height: 0.95;
    margin-top: 2px;
    white-space: nowrap;
  }

  .meter {
    position: relative;
    margin-top: 18px;
  }
  .meter.slim {
    margin-top: 12px;
  }
  .meter-track {
    position: relative;
    display: flex;
    height: 8px;
    border-radius: 999px;
    overflow: hidden;
    background: var(--bg-card-2);
  }
  .meter-track .z {
    display: block;
    height: 100%;
  }
  .meter-track .calm {
    background: var(--good);
  }
  .meter-track .warn {
    background: var(--warn);
  }
  .meter-track .hot {
    background: var(--bad);
    flex: 1;
  }
  .pip {
    position: absolute;
    top: 4px;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--text);
    box-shadow: 0 0 0 3px var(--bg-card);
    transform: translate(-50%, -50%);
  }
  .meter-labels {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 7px;
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.02em;
    color: var(--text-dim);
  }

  .level {
    margin-top: 16px;
    height: 8px;
    border-radius: 999px;
    background: var(--bg-card-2);
    overflow: hidden;
  }
  .level i {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--accent);
  }

  .link-hero {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
  }
  .bars {
    display: flex;
    align-items: flex-end;
    gap: 4px;
    height: 28px;
    flex: none;
  }
  .bars i {
    width: 6px;
    display: block;
    border-radius: 2px;
    background: var(--border-strong);
  }
  .bars i:nth-child(1) {
    height: 8px;
  }
  .bars i:nth-child(2) {
    height: 14px;
  }
  .bars i:nth-child(3) {
    height: 20px;
  }
  .bars i:nth-child(4) {
    height: 28px;
  }
  .bars i.lit {
    background: var(--accent);
  }
  .panel.faulty {
    border-color: var(--bad-edge);
  }

  .mono-v {
    font-family: var(--mono);
    font-size: 1.05rem !important;
    letter-spacing: -0.01em;
  }
  .small-v {
    font-size: 1.02rem !important;
    line-height: 1.25;
    font-weight: 500;
  }
  .sub {
    display: block;
    font-size: 0.78rem;
    color: var(--text-dim);
    margin: 3px 0 0;
  }
  .k {
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  .hint {
    color: var(--text-dim);
    font-size: 0.88rem;
    margin: 0 0 14px;
    max-width: 68ch;
  }

  /* --- thresholds ------------------------------------------------------ */

  .tune-group h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .tune-grid {
    display: grid;
    gap: 14px;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    margin-top: 14px;
  }
  .tune {
    display: grid;
    gap: 6px;
    align-content: start;
  }
  .stepper {
    display: grid;
    grid-template-columns: 44px minmax(0, 1fr) 44px;
    gap: 6px;
  }
  .stepper button {
    height: 44px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--bg-card-2);
    color: var(--text);
    font-size: 1.15rem;
    line-height: 1;
  }
  .stepper button:hover:not(:disabled) {
    border-color: var(--border-strong);
    background: var(--bg-card-hover);
  }
  .tune-mode {
    margin-top: 14px;
  }
  .tune-mode .mode {
    margin-top: 0;
  }

  select,
  input {
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 11px 12px;
    font-size: 16px;
    width: 100%;
    transition: border-color 0.2s;
  }
  .stepper input {
    text-align: center;
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    padding-inline: 6px;
  }
  select:hover,
  input:hover {
    border-color: var(--border-strong);
  }
  input:disabled,
  select:disabled {
    opacity: 0.45;
  }

  /* --- charts ---------------------------------------------------------- */

  .chart-grid {
    display: grid;
    gap: 14px;
  }
  .chart-card.wide,
  .activity-card {
    grid-column: 1 / -1;
  }
  .activity-card h2 {
    margin: 0;
  }
  .activity-card .chart-head {
    margin-bottom: 10px;
  }
  @container stage (min-width: 700px) {
    .chart-grid {
      grid-template-columns: 1fr 1fr;
    }
    .chart-card:last-child {
      grid-column: 1 / -1;
    }
  }
  .chart-card {
    padding: 16px 16px 12px;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .chart-card:hover {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
  }
  .chart-card:hover .expand {
    color: var(--accent);
  }
  .chart-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
  }
  .chart-title {
    min-width: 0;
  }
  .chart-head h3 {
    font-size: 0.98rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.02em;
  }
  .chart-now {
    margin: 2px 0 0;
    font-size: 1.28rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }
  .chart-now span {
    margin-left: 4px;
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--text-dim);
  }
  .expand {
    color: var(--text-dim);
    flex: none;
    transition: color 0.2s;
  }
  .expand svg {
    width: 15px;
    height: 15px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .detail-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
  }
  .detail-head h3 {
    margin: 0 0 3px;
    font-size: 1.15rem;
    letter-spacing: -0.02em;
  }
  .ranges {
    display: flex;
    gap: 6px;
    margin: 14px 0 4px;
  }
  .ranges button {
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 7px 14px;
    font-family: var(--mono);
    font-size: 0.75rem;
  }
  .ranges button.on {
    background: var(--accent-dim);
    border-color: var(--accent-soft);
    color: var(--accent);
  }
  .stats {
    margin-top: 16px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }
  .statrow {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    padding: 5px 0;
  }
  .statrow .name {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 0.85rem;
  }
  .statrow .name i {
    width: 9px;
    height: 3px;
    border-radius: 2px;
  }
  .statrow .nums {
    display: inline-flex;
    gap: 12px;
    align-items: baseline;
    font-family: var(--mono);
    font-size: 0.74rem;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }
  .statrow .nums b {
    font-size: 0.95rem;
    color: var(--text);
    font-weight: 600;
  }
  .samples {
    margin-top: 8px;
  }
  .sheet.wide {
    width: min(760px, 100%);
  }

  /* --- sheets ---------------------------------------------------------- */

  .scrim {
    position: fixed;
    inset: 0;
    background: var(--scrim);
    backdrop-filter: blur(6px);
    border: 0;
    z-index: 30;
  }
  .sheet {
    position: fixed;
    z-index: 31;
    left: 50%;
    transform: translateX(-50%);
    bottom: 0;
    width: min(480px, 100%);
    max-height: min(88vh, 100%);
    overflow-y: auto;
    border-radius: 20px 20px 0 0;
    border-color: var(--border-strong);
    box-shadow: var(--shadow);
    display: grid;
    gap: 7px;
    align-content: start;
    padding-bottom: calc(20px + env(safe-area-inset-bottom));
  }
  .grabber {
    width: 36px;
    height: 4px;
    border-radius: 999px;
    background: var(--border-strong);
    justify-self: center;
    margin-bottom: 4px;
  }
  .sheet h3 {
    font-size: 1.2rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.02em;
  }
  .sheet label {
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-top: 8px;
  }
  /* The mode picker. Two cards rather than a select: the choice is the one
     decision in this sheet that changes what the app needs to work, and it
     deserves its consequence spelled out rather than hidden in an option
     label. Stacks at phone width like everything else. */
  .modes {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin: 4px 0 8px;
  }
  /* Three short choices fit a row on a phone where two long ones did not;
     the connection modes keep their 2-up grid and their sentence of blurb. */
  .modes.three {
    grid-template-columns: repeat(3, 1fr);
  }
  .modes.two .mode {
    justify-items: center;
    text-align: center;
    padding: 12px 8px;
  }
  /* Two controls in one section now, so each needs saying which it is. */
  .setting-label {
    margin: 14px 0 0;
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  /* One word each, centred: the connection modes need a sentence to choose
     between them, System/Light/Dark do not, and three one-line blurbs at
     phone width wrapped to two lines on exactly one of them. */
  .modes.three .mode {
    justify-items: center;
    text-align: center;
    padding: 12px 8px;
  }
  .sheet h3.section {
    margin-top: 22px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .mode {
    display: grid;
    gap: 3px;
    text-align: left;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-card-2);
    color: var(--text);
  }
  .mode:hover {
    background: var(--bg-card-hover);
  }
  .mode.active {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .mode-name {
    font-weight: 600;
    font-size: 0.92rem;
  }
  .mode-blurb {
    color: var(--text-dim);
    font-size: 0.78rem;
    line-height: 1.35;
  }
  .sheet code {
    font-family: var(--mono);
    font-size: 0.85em;
    color: var(--accent);
  }
  @media (max-width: 440px) {
    .modes {
      grid-template-columns: 1fr;
    }
  }
  .sheet-actions {
    display: flex;
    gap: 10px;
    margin-top: 16px;
  }
  .sheet-actions button {
    flex: 1;
    white-space: nowrap;
    padding-inline: 10px;
  }
  .sheet-actions button.primary {
    flex: 1.35;
  }

  @media (min-width: 560px) {
    .sheet {
      bottom: auto;
      top: 50%;
      transform: translate(-50%, -50%);
      border-radius: 20px;
      padding-bottom: 20px;
    }
    .grabber {
      display: none;
    }
  }
</style>
