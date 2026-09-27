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

  import { onMount } from 'svelte';

  let showSettings = $state(false);
  let draft = $state({ ...DEFAULTS });

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
  const R = 68;

  function angle(v) {
    const clamped = Math.max(G_MIN, Math.min(G_MAX, Number(v) || 0));
    return -180 + ((clamped - G_MIN) / (G_MAX - G_MIN)) * 180;
  }

  function arc(from, to) {
    const p = (deg) => {
      const rad = (deg * Math.PI) / 180;
      return [90 + R * Math.cos(rad), 90 + R * Math.sin(rad)];
    };
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
  }
</script>

<svelte:head>
  <title>Smartfan</title>
</svelte:head>

<div class="wrap">
  <header>
    <div class="titles">
      <p class="eyebrow">{$settings.room}</p>
      <h1>Smartfan</h1>
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
      <p>Connect to your broker to get started.</p>
      <p class="sub">
        Everything stays on your network. The app talks straight to MQTT —
        there is no account and no server in between.
      </p>
      <button class="primary setup" onclick={openSettings}>
        Set up connection
      </button>
    </div>
  {:else if !t}
    <div class="card empty">
      <p>Waiting for the first reading…</p>
      <p class="sub">
        The fan reports every 30 seconds. If nothing arrives, check the broker
        settings.
      </p>
    </div>
  {:else}
    <!-- What is it doing, and is it about to do something -->
    <section class="hero card" class:dimmed={!live}>
      <div class="gauge">
        <svg viewBox="0 0 180 104" role="img" aria-label="Humidity above baseline">
          <path d={arc(-180, 0)} class="track" />
          <path d={arc(angle(G_MIN), angle(cfg.RH_FALL_OFF ?? 3))} class="band calm" />
          <path
            d={arc(angle(cfg.RH_FALL_OFF ?? 3), angle(cfg.RH_RISE_ON ?? 8))}
            class="band warn"
          />
          <path d={arc(angle(cfg.RH_RISE_ON ?? 8), angle(G_MAX))} class="band active" />
          <line
            x1="90"
            y1="90"
            x2={90 + (R - 12) * Math.cos((angle(t.exc) * Math.PI) / 180)}
            y2={90 + (R - 12) * Math.sin((angle(t.exc) * Math.PI) / 180)}
            class="needle"
          />
          <circle cx="90" cy="90" r="4" class="hub" />
        </svg>
        <div class="reading tone-{excTone}">{num(t.exc, 1, '%')}</div>
        <div class="reading-sub">above baseline</div>
      </div>

      <div class="hero-state">
        <p class="eyebrow">Controller</p>
        <p class="state tone-{tone}">{state}</p>
        <p class="blurb">{STATE_BLURB[state] ?? ''}</p>
        {#if state === 'BOOST' && t.boost_left > 0}
          <p class="blurb tone-active">{dur(t.boost_left)} remaining</p>
        {/if}
        {#if t.reason}
          <p class="reason">{t.reason}</p>
        {/if}
        <div class="hero-facts">
          <span class={t.fan_on ? 'tone-active' : 'tone-dim'}>
            Fan {t.fan_on ? `running · ${int(t.rpm)} rpm` : 'off'}
          </span>
          <span class={t.light_on ? 'tone-warn' : 'tone-dim'}>
            Light {t.light_on ? 'on' : 'off'}
          </span>
          {#if t.fault}<span class="tone-bad">Fault</span>{/if}
        </div>
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
      <button onclick={() => command('relearn')} disabled={!live}>Relearn baseline</button>
      <button onclick={() => command('clear')} disabled={!live || !t.fault}>Clear fault</button>
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
        Force on and Force off override the humidity logic until set back to
        Auto.
      </p>
    </div>

    <h2>Readings</h2>
    <div class="grid">
      <div class="stat">
        <span class="k">Humidity</span>
        <span class="v">{num(t.rh, 1, '%')}</span>
        <span class="sub">baseline {num(t.base, 1, '%')}</span>
      </div>
      <div class="stat">
        <span class="k">Temperature</span>
        <span class="v">{num(t.temp, 1, '°C')}</span>
        <span class="sub">dew point {num(t.dp, 1, '°C')}</span>
      </div>
      <div class="stat">
        <span class="k">Light</span>
        <span class="v">{num(t.light_pc, 1, '%')}</span>
        <span class="sub">raw {int(t.light)}</span>
      </div>
      <div class="stat">
        <span class="k">Fan speed</span>
        <span class="v">{int(t.rpm)}</span>
        <span class="sub">rpm</span>
      </div>
      <div class="stat">
        <span class="k">Run today</span>
        <span class="v">{durMin(t.run_min)}</span>
      </div>
      <div class="stat">
        <span class="k">Uptime</span>
        <span class="v">{durMin(t.up_min)}</span>
        <span class="sub">since {t.boot}</span>
      </div>
    </div>

    <h2>Thresholds</h2>
    <p class="hint">
      The device validates every value against its own ranges and rejects
      anything outside them. A change only counts once it comes back in the
      next reading.
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

    <h2>Health</h2>
    <div class="grid">
      <div class="stat">
        <span class="k">Wi-Fi</span>
        <span class="v">{rssiWord(t.rssi)}</span>
        <span class="sub">{int(t.rssi)} dBm</span>
      </div>
      <div class="stat">
        <span class="k">Build</span>
        <span class="v mono-v">{t.fw ?? '—'}</span>
      </div>
      <div class="stat">
        <span class="k">Reconnects</span>
        <span class="v">{int(t.reconn)}</span>
        <span class="sub">last outage {dur(t.outage_s)}</span>
      </div>
    </div>
  {/if}

  {#if showSettings}
    <div
      class="scrim"
      role="button"
      tabindex="-1"
      aria-label="Close settings"
      onclick={() => (showSettings = false)}
      onkeydown={(e) => e.key === 'Escape' && (showSettings = false)}
    ></div>
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
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }

  h1 {
    font-family: var(--cond);
    font-size: 28px;
    font-weight: 700;
    margin: 2px 0 0;
    letter-spacing: -0.01em;
  }

  .head-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  button.icon {
    background: var(--raised);
    border: 1px solid var(--line);
    color: var(--ink-mid);
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
  }

  .banner {
    border-radius: 10px;
    padding: 11px 14px;
    margin: 0 0 16px;
    font-size: 14px;
    border: 1px solid;
  }
  .banner.bad {
    background: #2a1619;
    border-color: #6b2b31;
    color: #ffb4ba;
  }
  .banner.warn {
    background: #2a2316;
    border-color: #6b552b;
    color: #ffd9a0;
  }

  .empty {
    text-align: center;
    color: var(--ink-mid);
  }
  .empty p {
    margin: 4px 0;
  }
  .empty .setup {
    margin-top: 14px;
    border-radius: 10px;
    padding: 12px 20px;
    border: 1px solid #2f6577;
    font-size: 14px;
    font-weight: 500;
  }

  .banner-sub {
    font-size: 13px;
    opacity: 0.8;
    margin: 6px 0 10px !important;
  }
  .banner button {
    background: transparent;
    border: 1px solid currentColor;
    color: inherit;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
  }

  .hero {
    display: flex;
    gap: 22px;
    align-items: center;
    flex-wrap: wrap;
  }
  .hero.dimmed {
    opacity: 0.55;
  }

  .gauge {
    position: relative;
    flex: none;
    width: 180px;
    text-align: center;
  }
  .gauge svg {
    width: 180px;
    height: 104px;
    display: block;
  }
  .track {
    fill: none;
    stroke: var(--line-soft);
    stroke-width: 13;
    stroke-linecap: round;
  }
  .band {
    fill: none;
    stroke-width: 13;
  }
  .band.calm {
    stroke: var(--ok);
  }
  .band.warn {
    stroke: var(--warn);
  }
  .band.active {
    stroke: var(--bad);
  }
  .needle {
    stroke: var(--ink);
    stroke-width: 3;
    stroke-linecap: round;
  }
  .hub {
    fill: var(--ink);
  }
  .reading {
    font-family: var(--cond);
    font-size: 30px;
    font-weight: 700;
    line-height: 1;
    margin-top: -10px;
    font-variant-numeric: tabular-nums;
  }
  .reading-sub {
    font-size: 12px;
    color: var(--ink-dim);
  }

  .hero-state {
    flex: 1 1 220px;
    min-width: 0;
  }
  .state {
    font-family: var(--cond);
    font-size: 34px;
    font-weight: 700;
    line-height: 1;
    margin: 4px 0 6px;
  }
  .blurb {
    margin: 0 0 4px;
    color: var(--ink-mid);
    font-size: 14px;
  }
  .reason {
    margin: 8px 0 0;
    color: var(--ink-dim);
    font-size: 13px;
    font-style: italic;
  }
  .hero-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin-top: 12px;
    font-family: var(--mono);
    font-size: 12px;
  }

  .controls {
    display: grid;
    gap: 10px;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  }
  .controls button,
  .sheet-actions button {
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 13px 14px;
    font-size: 14px;
    font-weight: 500;
  }
  .controls button:disabled,
  .sheet-actions button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  button.primary {
    background: #1d3a44;
    border-color: #2f6577;
    color: #bfe8f2;
  }

  .mode {
    margin-top: var(--gap);
    display: grid;
    gap: 8px;
  }
  .mode label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-dim);
  }

  select,
  input {
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px 12px;
    /* 16px stops iOS zooming the page when a field takes focus. */
    font-size: 16px;
    width: 100%;
  }
  input:disabled,
  select:disabled {
    opacity: 0.5;
  }

  .hint {
    color: var(--ink-dim);
    font-size: 13px;
    margin: -4px 0 12px;
    max-width: 62ch;
  }

  .tune {
    display: grid;
    gap: 6px;
  }
  .tune input {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }

  .mono-v {
    font-family: var(--mono);
    font-size: 17px;
  }

  .sub {
    font-size: 12px;
    color: var(--ink-dim);
    margin: 0;
  }

  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    border: 0;
    z-index: 10;
  }
  .sheet {
    position: fixed;
    z-index: 11;
    left: 50%;
    transform: translateX(-50%);
    bottom: 0;
    width: min(460px, 100%);
    max-height: 88vh;
    overflow-y: auto;
    border-radius: 14px 14px 0 0;
    display: grid;
    gap: 8px;
    align-content: start;
  }
  .sheet h3 {
    font-family: var(--cond);
    font-size: 20px;
    margin: 0;
  }
  .sheet label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-dim);
    margin-top: 6px;
  }
  .sheet-actions {
    display: flex;
    gap: 10px;
    margin-top: 16px;
  }
  .sheet-actions button {
    flex: 1;
  }

  @media (min-width: 560px) {
    .sheet {
      bottom: auto;
      top: 50%;
      transform: translate(-50%, -50%);
      border-radius: 14px;
    }
  }
</style>
