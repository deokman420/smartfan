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
    <!-- What is it doing, and is it about to do something -->
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
          <p class="blurb tone-active">{dur(t.boost_left)} remaining</p>
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
        {#if t.fault}<span class="tone-bad">Fault</span>{/if}
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
        <span class="chip icon-blue"><svg viewBox="0 0 16 16">{@html ICONS.humidity}</svg></span>
        <span class="k">Humidity</span>
        <span class="v">{num(t.rh, 1, '%')}</span>
        <span class="sub">baseline {num(t.base, 1, '%')}</span>
      </div>
      <div class="stat">
        <span class="chip icon-orange"><svg viewBox="0 0 16 16">{@html ICONS.temp}</svg></span>
        <span class="k">Temperature</span>
        <span class="v">{num(t.temp, 1, '°C')}</span>
        <span class="sub">dew point {num(t.dp, 1, '°C')}</span>
      </div>
      <div class="stat">
        <span class="chip icon-purple"><svg viewBox="0 0 16 16">{@html ICONS.light}</svg></span>
        <span class="k">Light</span>
        <span class="v">{num(t.light_pc, 1, '%')}</span>
        <span class="sub">raw {int(t.light)}</span>
      </div>
      <div class="stat">
        <span class="chip icon-teal"><svg viewBox="0 0 16 16">{@html ICONS.fan}</svg></span>
        <span class="k">Fan speed</span>
        <span class="v">{int(t.rpm)}</span>
        <span class="sub">rpm</span>
      </div>
      <div class="stat">
        <span class="chip icon-green"><svg viewBox="0 0 16 16">{@html ICONS.clock}</svg></span>
        <span class="k">Run today</span>
        <span class="v">{durMin(t.run_min)}</span>
      </div>
      <div class="stat">
        <span class="chip icon-blue"><svg viewBox="0 0 16 16">{@html ICONS.uptime}</svg></span>
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
        <span class="chip icon-teal"><svg viewBox="0 0 16 16">{@html ICONS.wifi}</svg></span>
        <span class="k">Wi-Fi</span>
        <span class="v">{rssiWord(t.rssi)}</span>
        <span class="sub">{int(t.rssi)} dBm</span>
      </div>
      <div class="stat">
        <span class="chip icon-purple"><svg viewBox="0 0 16 16">{@html ICONS.build}</svg></span>
        <span class="k">Build</span>
        <span class="v mono-v">{t.fw ?? '—'}</span>
      </div>
      <div class="stat">
        <span class="chip icon-orange"><svg viewBox="0 0 16 16">{@html ICONS.link}</svg></span>
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
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 20px;
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
    margin-top: 14px;
    font-family: var(--mono);
    font-size: 0.72rem;
  }

  /* Wide enough for three columns: the facts become their own column
     against the right edge instead of leaving half the hero empty. */
  @media (min-width: 720px) {
    .hero {
      gap: 32px;
    }
    .hero-state {
      flex: 1 1 auto;
    }
    .hero-facts {
      flex-direction: column;
      align-items: flex-end;
      text-align: right;
      gap: 8px;
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

  .sub {
    font-size: 0.78rem;
    color: var(--text-dim);
    margin: 0;
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
