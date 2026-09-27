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
    DEFAULTS
  } from '$lib/broker.js';
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
  import { history, since, stats, F } from '$lib/history.js';
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
  const CHARTS = [
    {
      id: 'humidity',
      title: 'Humidity against its baseline',
      blurb: 'The fan runs on the gap between the two, not on humidity itself.',
      unit: '%',
      bands: F.fan,
      series: [
        { field: F.rh, label: 'Humidity', color: 'var(--blue)', fill: true },
        { field: F.base, label: 'Baseline', color: 'var(--purple)' }
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
      unit: '°C',
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
      series: [{ field: F.light, label: 'Light', color: 'var(--purple)', fill: true }]
    },
    {
      id: 'fan',
      title: 'Fan speed',
      blurb: 'Measured by the tachometer, not by what the fan was told.',
      unit: 'rpm',
      bands: F.fan,
      series: [{ field: F.rpm, label: 'Fan speed', color: 'var(--accent)', fill: true }]
    }
  ];

  const RANGES = [
    { hours: 1, label: '1 h' },
    { hours: 6, label: '6 h' },
    { hours: 24, label: '24 h' }
  ];

  onMount(() => {
    // First run on this browser: ask for the broker rather than dialling
    // out with nothing and reporting the refusal as if it were a fault.
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

  // Reachable means both hops are up: our socket to the broker, and the
  // device's own retained availability. Either one down makes the readings
  // history rather than status.
  let live = $derived($link === 'online' && $deviceOnline && !$stale);

  let cardRows = $derived(since($history, 6));
  let detailRows = $derived(since($history, range));
  let detailChart = $derived(detail ? CHARTS.find((c) => c.id === detail) : null);

  function openSettings() {
    draft = { ...$settings };
    showSettings = true;
  }

  function applySettings() {
    saveSettings({ ...draft, port: Number(draft.port) || DEFAULTS.port });
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

  // Gauge geometry. -5..20 %RH above baseline, matching the Home Assistant
  // gauge so the two never disagree about where the fan starts.
  const G_MIN = -5;
  const G_MAX = 20;
  const R = 70;

  function angle(v) {
    const clamped = Math.max(G_MIN, Math.min(G_MAX, Number(v) || 0));
    return -180 + ((clamped - G_MIN) / (G_MAX - G_MIN)) * 180;
  }

  function arc(from, to) {
    const p = (deg) => {
      const rad = (deg * Math.PI) / 180;
      return [92 + R * Math.cos(rad), 92 + R * Math.sin(rad)];
    };
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
  }
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

<div class="wrap">
  <header>
    <div class="brand">
      <span class="mark" aria-hidden="true">
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
      <button class="icon" onclick={openSettings} aria-label="Connection settings">
        Settings
      </button>
    </div>
  </header>

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
      The broker is reachable but the fan is not reporting. It keeps running on
      its own logic when the network is down — this is a reporting problem, not
      a fan problem.
    </p>
  {:else if $stale}
    <p class="banner warn">
      Nothing heard for over 90 seconds. The fan publishes every 30.
    </p>
  {/if}

  {#if !t && !$configured}
    <div class="card empty">
      <p class="lead">Connect to your broker to get started.</p>
      <p class="sub">
        Everything stays on your network. The app talks straight to MQTT —
        there is no account and no server in between.
      </p>
      <button class="primary setup" onclick={openSettings}>Set up connection</button>
    </div>
  {:else if !t}
    <div class="card empty">
      <p class="lead">Waiting for the first reading…</p>
      <p class="sub">
        The fan reports every 30 seconds. If nothing arrives, check the broker
        settings.
      </p>
    </div>
  {:else}
    <div class="shell">
      <div class="nav-slot">
        <Nav
          items={VIEWS}
          current={view}
          onSelect={(id) => (view = id)}
          alert={t.fault ? 'status' : null}
        />
      </div>

      <main>
        {#if view === 'status'}
          <section class="hero card" class:dimmed={!live}>
            <div class="gauge">
              <svg viewBox="0 0 184 108" role="img" aria-label="Humidity above baseline">
                <path d={arc(-180, 0)} class="track" />
                <path d={arc(angle(G_MIN), angle(cfg.RH_FALL_OFF ?? 3))} class="band calm" />
                <path
                  d={arc(angle(cfg.RH_FALL_OFF ?? 3), angle(cfg.RH_RISE_ON ?? 8))}
                  class="band warn"
                />
                <path d={arc(angle(cfg.RH_RISE_ON ?? 8), angle(G_MAX))} class="band hot" />
                <line
                  x1="92"
                  y1="92"
                  x2={92 + (R - 13) * Math.cos((angle(t.exc) * Math.PI) / 180)}
                  y2={92 + (R - 13) * Math.sin((angle(t.exc) * Math.PI) / 180)}
                  class="needle"
                />
                <circle cx="92" cy="92" r="4.5" class="hub" />
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
              {/if}
              {#if t.reason}
                <p class="reason">{t.reason}</p>
              {/if}
            </div>

            <div class="hero-facts">
              <span class={t.fan_on ? 'tone-active' : 'tone-dim'}>
                Fan {t.fan_on ? `running · ${int(t.rpm)} rpm` : 'off'}
              </span>
              <span class={t.light_on ? 'tone-warn' : 'tone-dim'}>
                Light {t.light_on ? 'on' : 'off'}
              </span>
              <span class={t.fault ? 'tone-bad' : 'tone-dim'}>
                {t.fault ? 'Fault' : 'No fault'}
              </span>
            </div>
          </section>

          <h2>Control</h2>
          <div class="controls">
            <button class="primary" onclick={() => command('boost')} disabled={!live}>
              Boost {dur(cfg.BOOST_S)}
            </button>
            <button onclick={() => command('cancel')} disabled={!live || state !== 'BOOST'}>
              Cancel boost
            </button>
            <button onclick={() => command('relearn')} disabled={!live}>
              Relearn baseline
            </button>
            <button onclick={() => command('clear')} disabled={!live || !t.fault}>
              Clear fault
            </button>
          </div>

          <div class="card mode">
            <label for="mode">Mode</label>
            <select
              id="mode"
              value={cfg.MODE ?? 'Auto'}
              disabled={!live}
              onchange={(e) => setParam('MODE', e.currentTarget.value)}
            >
              {#each MODES as m (m)}
                <option value={m}>{m}</option>
              {/each}
            </select>
            <p class="sub">
              Force on and Force off override the humidity logic until set back
              to Auto.
            </p>
          </div>
        {/if}

        {#if view === 'readings'}
          <h2>Humidity</h2>
          <div class="grid">
            <div class="stat">
              <span class="chip icon-blue"
                ><svg viewBox="0 0 16 16">{@html ICONS.humidity}</svg></span
              >
              <span class="k">Humidity</span>
              <span class="v">{num(t.rh, 1, '%')}</span>
            </div>
            <div class="stat">
              <span class="chip icon-blue"
                ><svg viewBox="0 0 16 16">{@html ICONS.gauge}</svg></span
              >
              <span class="k">Baseline</span>
              <span class="v">{num(t.base, 1, '%')}</span>
              <span class="sub">what this room sits at</span>
            </div>
            <div class="stat">
              <span class="chip icon-teal"
                ><svg viewBox="0 0 16 16">{@html ICONS.pulse}</svg></span
              >
              <span class="k">Excess</span>
              <span class="v tone-{excTone}">{num(t.exc, 1, '%')}</span>
              <span class="sub">fan starts at {num(cfg.RH_RISE_ON, 1, '%')}</span>
            </div>
            <div class="stat">
              <span class="chip icon-orange"
                ><svg viewBox="0 0 16 16">{@html ICONS.temp}</svg></span
              >
              <span class="k">Temperature</span>
              <span class="v">{num(t.temp, 1, '°C')}</span>
            </div>
            <div class="stat">
              <span class="chip icon-orange"
                ><svg viewBox="0 0 16 16">{@html ICONS.drop}</svg></span
              >
              <span class="k">Dew point</span>
              <span class="v">{num(t.dp, 1, '°C')}</span>
            </div>
          </div>

          <h2>Light</h2>
          <div class="grid">
            <div class="stat">
              <span class="chip icon-purple"
                ><svg viewBox="0 0 16 16">{@html ICONS.light}</svg></span
              >
              <span class="k">Light level</span>
              <span class="v">{num(t.light_pc, 1, '%')}</span>
            </div>
            <div class="stat">
              <span class="chip icon-purple"
                ><svg viewBox="0 0 16 16">{@html ICONS.sliders}</svg></span
              >
              <span class="k">Raw reading</span>
              <span class="v mono-v">{int(t.light)}</span>
              <span class="sub">on above {int(cfg.LIGHT_ON)}</span>
            </div>
            <div class="stat">
              <span class="chip {t.light_on ? 'icon-orange' : 'icon-blue'}">
                <svg viewBox="0 0 16 16">{@html ICONS.power}</svg>
              </span>
              <span class="k">Reads as</span>
              <span class="v">{t.light_on ? 'On' : 'Off'}</span>
            </div>
          </div>

          <h2>Fan</h2>
          <div class="grid">
            <div class="stat">
              <span class="chip icon-teal"><svg viewBox="0 0 16 16">{@html ICONS.fan}</svg></span>
              <span class="k">Fan speed</span>
              <span class="v">{int(t.rpm)}</span>
              <span class="sub">rpm</span>
            </div>
            <div class="stat">
              <span class="chip {t.fan_on ? 'icon-teal' : 'icon-blue'}">
                <svg viewBox="0 0 16 16">{@html ICONS.power}</svg>
              </span>
              <span class="k">Running</span>
              <span class="v">{t.fan_on ? 'Yes' : 'No'}</span>
            </div>
            <div class="stat">
              <span class="chip icon-green"
                ><svg viewBox="0 0 16 16">{@html ICONS.clock}</svg></span
              >
              <span class="k">Run today</span>
              <span class="v">{durMin(t.run_min)}</span>
            </div>
            <div class="stat">
              <span class="chip icon-teal"
                ><svg viewBox="0 0 16 16">{@html ICONS.uptime}</svg></span
              >
              <span class="k">Boost remaining</span>
              <span class="v">{t.boost_left > 0 ? dur(t.boost_left * 60) : '—'}</span>
              <span class="sub">{state === 'BOOST' ? 'boosting now' : 'not boosting'}</span>
            </div>
          </div>
        {/if}

        {#if view === 'tuning'}
          <h2>Thresholds</h2>
          <p class="hint">
            The device validates every value against its own ranges and rejects
            anything outside them. A change only counts once it comes back in
            the next reading.
          </p>
          <div class="grid">
            {#each TUNABLES as p (p.key)}
              <div class="stat tune">
                <label class="k" for={p.key}>{p.label}</label>
                <input
                  id={p.key}
                  type="number"
                  step={p.step}
                  disabled={!live}
                  value={cfg[p.key] ?? ''}
                  onchange={(e) => writeParam(p.key, e.currentTarget.value)}
                />
                <span class="sub">{p.unit ? `${p.unit} · ` : ''}{p.hint}</span>
              </div>
            {/each}
          </div>

          <h2>Mode</h2>
          <div class="card mode">
            <label for="mode-t">Mode</label>
            <select
              id="mode-t"
              value={cfg.MODE ?? 'Auto'}
              disabled={!live}
              onchange={(e) => setParam('MODE', e.currentTarget.value)}
            >
              {#each MODES as m (m)}
                <option value={m}>{m}</option>
              {/each}
            </select>
            <p class="sub">
              Force on and Force off override the humidity logic until set back
              to Auto.
            </p>
          </div>
        {/if}

        {#if view === 'charts'}
          <h2>Charts</h2>
          <p class="hint">
            Collected while this app is open, and kept in this browser for 24
            hours. Periods nobody recorded are drawn as breaks rather than a
            straight line across them. Tap a chart for detail.
          </p>
          <div class="chart-grid">
            {#each CHARTS as c (c.id)}
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
                  <h3>{c.title}</h3>
                  <span class="expand" aria-hidden="true">
                    <svg viewBox="0 0 16 16">{@html ICONS.expand}</svg>
                  </span>
                </div>
                <Chart
                  rows={cardRows}
                  series={c.series}
                  bands={c.bands}
                  refs={c.refs ? c.refs() : []}
                  unit={c.unit}
                  height={132}
                  compact
                />
              </div>
            {/each}
          </div>
        {/if}

        {#if view === 'health'}
          <h2>Link</h2>
          <div class="grid">
            <div class="stat">
              <span class="chip icon-teal"><svg viewBox="0 0 16 16">{@html ICONS.wifi}</svg></span>
              <span class="k">Wi-Fi</span>
              <span class="v">{rssiWord(t.rssi)}</span>
              <span class="sub">{int(t.rssi)} dBm</span>
            </div>
            <div class="stat">
              <span class="chip icon-orange"><svg viewBox="0 0 16 16">{@html ICONS.link}</svg></span>
              <span class="k">Reconnects</span>
              <span class="v">{int(t.reconn)}</span>
            </div>
            <div class="stat">
              <span class="chip icon-orange"
                ><svg viewBox="0 0 16 16">{@html ICONS.clock}</svg></span
              >
              <span class="k">Last outage</span>
              <span class="v">{dur(t.outage_s)}</span>
            </div>
          </div>

          <h2>Device</h2>
          <div class="grid">
            <div class="stat">
              <span class="chip icon-blue"
                ><svg viewBox="0 0 16 16">{@html ICONS.uptime}</svg></span
              >
              <span class="k">Uptime</span>
              <span class="v">{durMin(t.up_min)}</span>
            </div>
            <div class="stat">
              <span class="chip icon-blue"
                ><svg viewBox="0 0 16 16">{@html ICONS.power}</svg></span
              >
              <span class="k">Last restart</span>
              <span class="v small-v">{t.boot ?? '—'}</span>
            </div>
            <div class="stat">
              <span class="chip icon-purple"
                ><svg viewBox="0 0 16 16">{@html ICONS.build}</svg></span
              >
              <span class="k">Build</span>
              <span class="v mono-v">{t.fw ?? '—'}</span>
            </div>
            <div class="stat">
              <span class="chip {t.fault ? 'icon-red' : 'icon-green'}">
                <svg viewBox="0 0 16 16">{@html ICONS.pulse}</svg>
              </span>
              <span class="k">Fault</span>
              <span class="v">{t.fault ? 'Yes' : 'None'}</span>
              {#if t.reason}<span class="sub">{t.reason}</span>{/if}
            </div>
          </div>
        {/if}
      </main>
    </div>
  {/if}

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

      <Chart
        rows={detailRows}
        series={detailChart.series}
        bands={detailChart.bands}
        refs={detailChart.refs ? detailChart.refs() : []}
        unit={detailChart.unit}
        height={230}
      />

      <div class="stats">
        {#each detailChart.series as sdef (sdef.field)}
          {@const st = stats(detailRows, sdef.field)}
          <div class="statrow">
            <span class="name"><i style="background:{sdef.color}"></i>{sdef.label}</span>
            {#if st}
              <span class="nums">
                <b>{st.last.toFixed(1)}</b>
                <span>min {st.min.toFixed(1)}</span>
                <span>avg {st.mean.toFixed(1)}</span>
                <span>max {st.max.toFixed(1)}</span>
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
    </div>
  {/if}

  {#if showSettings}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="scrim" aria-hidden="true" onclick={() => (showSettings = false)}></div>
    <div class="sheet card" role="dialog" aria-label="Connection settings">
      <h3>Connection</h3>
      <p class="sub">
        The app talks straight to your MQTT broker. Nothing leaves the network.
      </p>

      <label for="host">Broker address</label>
      <input id="host" bind:value={draft.host} autocapitalize="off" autocorrect="off" />

      <label for="port">WebSocket port</label>
      <input id="port" type="number" bind:value={draft.port} />
      <p class="sub">
        1884 on the Home Assistant Mosquitto add-on. Port 1883 is plain TCP and
        a browser cannot open it.
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

      <div class="sheet-actions">
        <button onclick={() => (showSettings = false)}>Cancel</button>
        <button class="primary" onclick={applySettings}>Save and reconnect</button>
      </div>
    </div>
  {/if}
</div>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .mark {
    width: 42px;
    height: 42px;
    border-radius: 12px;
    background: var(--accent-dim);
    color: var(--accent);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    border: 1px solid var(--accent-soft);
  }
  .mark svg {
    width: 22px;
    height: 22px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin: 1px 0 0;
    letter-spacing: -0.025em;
  }

  .head-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  button.icon {
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-dim);
    border-radius: 10px;
    padding: 7px 14px;
    font-size: 0.82rem;
    transition: background 0.2s, border-color 0.2s, color 0.2s;
  }
  button.icon:hover {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
    color: var(--text);
  }

  /* --- shell ----------------------------------------------------------- */

  .nav-slot {
    position: sticky;
    top: 0;
    z-index: 5;
    background: var(--bg);
    padding: 6px 0 9px;
    margin-bottom: 2px;
    border-bottom: 1px solid var(--border);
  }

  main > :global(h2:first-child) {
    margin-top: 20px;
  }

  @media (min-width: 960px) {
    .shell {
      display: grid;
      grid-template-columns: 196px 1fr;
      gap: 30px;
      align-items: start;
    }
    .nav-slot {
      top: 22px;
      padding: 0;
      margin: 6px 0 0;
      border-bottom: 0;
      background: transparent;
    }
    main > :global(h2:first-child) {
      margin-top: 0;
    }
  }

  /* --- banners --------------------------------------------------------- */

  .banner {
    border-radius: var(--radius);
    padding: 14px 16px;
    margin: 0 0 18px;
    font-size: 0.9rem;
    border: 1px solid;
  }
  .banner.bad {
    background: rgba(239, 68, 68, 0.08);
    border-color: rgba(239, 68, 68, 0.35);
    color: #fca5a5;
  }
  .banner.warn {
    background: rgba(245, 158, 11, 0.08);
    border-color: rgba(245, 158, 11, 0.35);
    color: #fcd34d;
  }
  .banner p {
    margin: 0;
  }
  .banner-sub {
    font-size: 0.82rem;
    opacity: 0.85;
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
    padding: 44px 24px;
  }
  .empty .lead {
    margin: 0 0 6px;
    font-size: 1.05rem;
    font-weight: 500;
  }
  .empty .sub {
    max-width: 44ch;
    margin: 0 auto;
  }
  .empty .setup {
    margin-top: 18px;
    padding: 12px 22px;
    border-radius: 10px;
    font-size: 0.9rem;
    font-weight: 500;
  }

  /* --- hero ------------------------------------------------------------ */

  .hero {
    display: flex;
    gap: 26px;
    align-items: center;
    flex-wrap: wrap;
    padding: 22px;
    position: relative;
    overflow: hidden;
    background:
      radial-gradient(120% 90% at 0% 0%, rgba(0, 212, 170, 0.07), transparent 60%),
      var(--bg-card);
  }
  .hero.dimmed {
    opacity: 0.5;
  }

  .gauge {
    position: relative;
    flex: none;
    width: 184px;
    text-align: center;
  }
  .gauge svg {
    width: 184px;
    height: 108px;
    display: block;
    overflow: visible;
  }
  .track {
    fill: none;
    stroke: var(--bg-card-2);
    stroke-width: 12;
    stroke-linecap: round;
  }
  .band {
    fill: none;
    stroke-width: 12;
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
  .needle {
    stroke: var(--text);
    stroke-width: 2.6;
    stroke-linecap: round;
    transition: all 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .hub {
    fill: var(--text);
  }
  .reading {
    font-size: 2rem;
    font-weight: 600;
    line-height: 1;
    margin-top: -12px;
    letter-spacing: -0.03em;
    font-variant-numeric: tabular-nums;
  }
  .reading-sub {
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-top: 3px;
  }

  .hero-state {
    flex: 1 1 210px;
    min-width: 0;
  }
  .state {
    font-size: 2.1rem;
    font-weight: 700;
    line-height: 1;
    margin: 5px 0 7px;
    letter-spacing: -0.035em;
  }
  .blurb {
    margin: 0 0 3px;
    color: var(--text-dim);
    font-size: 0.88rem;
  }
  .reason {
    margin: 9px 0 0;
    color: var(--text-dim);
    font-size: 0.8rem;
    font-style: italic;
  }
  .hero-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    font-family: var(--mono);
    font-size: 0.72rem;
  }

  @media (min-width: 720px) {
    .hero {
      gap: 30px;
    }
    .hero-state {
      flex: 1 1 auto;
    }
    .hero-facts {
      flex-direction: column;
      align-items: flex-end;
      text-align: right;
      gap: 9px;
      margin: 0 0 0 auto;
      flex: none;
      padding-left: 26px;
      border-left: 1px solid var(--border);
      align-self: stretch;
      justify-content: center;
      font-size: 0.78rem;
    }
  }

  /* --- controls -------------------------------------------------------- */

  .controls {
    display: grid;
    gap: 10px;
    grid-template-columns: repeat(auto-fit, minmax(162px, 1fr));
  }
  .controls button,
  .sheet-actions button {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px;
    font-size: 0.9rem;
    font-weight: 500;
    transition: background 0.2s, border-color 0.2s, transform 0.2s;
  }
  .controls button:hover:not(:disabled),
  .sheet-actions button:hover:not(:disabled) {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
    transform: translateY(-1px);
  }
  .controls button:disabled,
  .sheet-actions button:disabled {
    opacity: 0.35;
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

  .mode {
    margin-top: 12px;
    display: grid;
    gap: 8px;
  }
  .mode label {
    font-family: var(--mono);
    font-size: 0.63rem;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  select,
  input {
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 11px 13px;
    /* 16px stops iOS zooming the page when a field takes focus. */
    font-size: 16px;
    width: 100%;
    transition: border-color 0.2s;
  }
  select:hover,
  input:hover {
    border-color: var(--border-strong);
  }
  input:disabled,
  select:disabled {
    opacity: 0.45;
  }

  .hint {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin: -6px 0 14px;
    max-width: 62ch;
  }

  .tune {
    display: grid;
    gap: 6px;
    align-content: start;
  }
  .tune input {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }

  .mono-v {
    font-family: var(--mono);
    font-size: 1.05rem;
    letter-spacing: -0.01em;
  }
  .small-v {
    font-size: 1rem;
    line-height: 1.3;
  }

  .sub {
    font-size: 0.78rem;
    color: var(--text-dim);
    margin: 0;
  }

  /* --- charts ----------------------------------------------------------- */

  .chart-grid {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  }

  .chart-card {
    padding: 16px 16px 12px;
    cursor: pointer;
    transition: background 0.2s, border-color 0.2s, transform 0.2s;
  }
  .chart-card:hover {
    background: var(--bg-card-hover);
    border-color: var(--border-strong);
    transform: translateY(-2px);
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
  .chart-head h3 {
    font-size: 0.92rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.01em;
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
  }

  .ranges {
    display: flex;
    gap: 6px;
    margin: 14px 0 4px;
  }
  .ranges button {
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    color: var(--text-dim);
    border-radius: 8px;
    padding: 6px 14px;
    font-family: var(--mono);
    font-size: 0.74rem;
    transition: background 0.18s, color 0.18s, border-color 0.18s;
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
    width: min(720px, 100%);
  }

  /* --- settings sheet -------------------------------------------------- */

  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(5, 7, 11, 0.72);
    backdrop-filter: blur(2px);
    border: 0;
    z-index: 10;
  }
  .sheet {
    position: fixed;
    z-index: 11;
    left: 50%;
    transform: translateX(-50%);
    bottom: 0;
    width: min(468px, 100%);
    max-height: 88vh;
    overflow-y: auto;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    border-color: var(--border-strong);
    box-shadow: var(--shadow);
    display: grid;
    gap: 7px;
    align-content: start;
  }
  .sheet h3 {
    font-size: 1.15rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.02em;
  }
  .sheet label {
    font-family: var(--mono);
    font-size: 0.63rem;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-top: 8px;
  }
  .sheet-actions {
    display: flex;
    gap: 10px;
    margin-top: 18px;
  }
  .sheet-actions button {
    flex: 1;
  }

  @media (min-width: 560px) {
    .sheet {
      bottom: auto;
      top: 50%;
      transform: translate(-50%, -50%);
      border-radius: var(--radius-lg);
    }
  }
</style>
