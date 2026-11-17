<script>
  /**
   * Fan, light and controller state on one timeline.
   *
   * The other charts answer "how high". This one answers "when": the fan
   * bar, the light bar, and the state the controller was in, sharing a
   * clock so a shower reads as one event instead of three guesses.
   *
   * State is appended to history (F.st). Samples recorded before that
   * field existed leave a gap in the state lane rather than a guessed IDLE.
   */
  import { mode } from '$lib/link.js';
  import { F, GAP_MS, STATE_NAME, offline } from '$lib/history.js';
  import { CAUSES, burning, odorAt, runDuration } from '$lib/runcause.js';
  import { clock as clockPref, fmtClock } from '$lib/units.js';

  /* `cfg` arrives because this chart now reports WHY the fan ran, and the
     answer depends on the device's own thresholds. Defaulted to {} so the
     component still renders against a history with no live device behind
     it -- the lanes it cannot justify simply do not appear. */
  let { rows = [], compact = false, cfg = {} } = $props();

  let rhStop = $derived(Number(cfg.RH_FALL_OFF) || 0);
  let vocStop = $derived(Number(cfg.VOC_FALL_OFF) || 0);
  let vocVeto = $derived(Number(cfg.VOC_NOX_VETO) || 0);

  /* IDLE is --border-strong, not --text-dim. Both read as "quiet" on dark,
     where --text-dim is a pale gray on a dark track, but this is a FILL:
     on light the same token is a dark slate, so the idle stretches of the
     day came out heavier than the fan running. --border-strong is the
     neutral that stays one subtle step from the track in both schemes. */
  const STATE_COLOR = {
    0: 'var(--blue)',
    1: 'var(--border-strong)',
    2: 'var(--accent)',
    3: 'var(--warn)',
    4: 'var(--bad)'
  };

  let t0 = $derived(rows.length ? rows[0][F.t] : 0);
  let t1 = $derived(rows.length ? rows[rows.length - 1][F.t] : 1);

  let fanSegs = $derived(bits(F.fan, 'var(--accent)', 'Fan on'));
  let lightSegs = $derived(bits(F.lit, 'var(--purple)', 'Lights on'));
  let stateSegs = $derived(states());
  let odorSegs = $derived(odor());
  /* Outages, hatched across every lane, so an empty stretch of track reads
     as "nothing recorded" and not as "fan off, room dark". */
  let offs = $derived(
    offline(rows).map((h) => ({
      ...h,
      left: ((h.start - t0) / Math.max(t1 - t0, 1)) * 100,
      width: Math.max(0.5, ((h.end - h.start) / Math.max(t1 - t0, 1)) * 100)
    }))
  );
  /* Only drawn when the history actually carries the column. Rows written
     before 2026-11-01 are 11 or 12 wide and have no odor at all, and an
     empty lane reads as "no smell" rather than "not recorded". */
  let hasOdor = $derived(
    vocStop > 0 &&
      rows.some((r) => r.length > F.vocExc && r[F.vocExc] !== null && r[F.vocExc] !== undefined)
  );
  let hasState = $derived(rows.some((r) => r.length > F.st && r[F.st] !== null && r[F.st] !== undefined));

  let summary = $derived.by(() => {
    if (rows.length < 2) return '';
    const parts = [];
    const fan = duration(F.fan, (v) => v === 1);
    const lit = duration(F.lit, (v) => v === 1);
    parts.push(fan >= 20_000 ? `Fan on for ${fmtDur(fan)}.` : 'Fan stayed off.');
    parts.push(lit >= 20_000 ? `Lights on for ${fmtDur(lit)}.` : 'The room stayed dark.');
    if (hasState) {
      /* This used to read "Clearing humidity" for EVERY run, which was
         simply untrue once there were three reasons to run: it once said
         it over a room 0.9% above baseline while the fan ran on odor.
         Every column needed to tell them apart is recorded, so each RUN
         sample is attributed instead of assumed. */
      for (const [cause, text] of CAUSES) {
        const ms = runDuration(rows, cause, cfg);
        if (ms >= 20_000) parts.push(`${text} for ${fmtDur(ms)}.`);
      }
      const boosts = duration(F.st, (v) => v === 3);
      const faults = duration(F.st, (v) => v === 4);
      if (boosts >= 20_000) parts.push(`Boosted for ${fmtDur(boosts)}.`);
      if (faults >= 20_000) parts.push(`Faulted for ${fmtDur(faults)}.`);
    }
    /* A count, not a duration: a gap's bounds are the readings either side
       of it, which overstate how long the fan was actually off. */
    if (offs.length) {
      const n = offs.length;
      const who = $mode === 'device' ? 'Went offline' : 'Readings stopped';
      parts.push(`${who} ${n === 1 ? 'once' : n === 2 ? 'twice' : `${n} times`}.`);
    }
    return parts.join(' ');
  });

  let labels = $derived.by(() => {
    if (!rows.length || t1 === t0) return [];
    const n = compact ? 2 : 4;
    const fresh = Date.now() - t1 < 3 * 60_000;
    const out = [];
    for (let i = 0; i < n; i++) {
      const p = n === 1 ? 1 : i / (n - 1);
      out.push({
        p: p * 100,
        text: i === n - 1 && fresh ? 'now' : clock(t0 + (t1 - t0) * p),
        edge: i === 0 ? 'start' : i === n - 1 ? 'end' : ''
      });
    }
    return out;
  });

  let hover = $state(null);

  function bits(field, color, label) {
    return spans(field, (v) => (v === 1 ? { key: 'on', color, label } : null));
  }

  function states() {
    return spans(F.st, (v) => {
      const name = STATE_NAME[v];
      if (!name) return null;
      return { key: name, color: STATE_COLOR[v] || 'var(--border-strong)', label: name };
    });
  }

  function spans(field, classify) {
    return rowSpans((r) => {
      const missing = r.length <= field || r[field] === null || r[field] === undefined;
      return missing ? null : classify(r[field]);
    });
  }

  /* The odor lane needs two columns at once -- the excess, and the NOx
     rise that decides whether that excess counted -- so the span builder
     takes a row rather than a field. */
  function odor() {
    return rowSpans((r) => {
      const e = r.length > F.vocExc ? r[F.vocExc] : null;
      if (e === null || e === undefined || e <= vocStop) return null;
      return burning(r, vocVeto)
        ? { key: 'burn', color: 'var(--orange)', label: 'Combustion — odor ignored' }
        : { key: 'odor', color: 'var(--green)', label: 'Odor above baseline' };
    });
  }



  function rowSpans(classify) {
    if (rows.length < 2 || t1 === t0) return [];
    const out = [];
    let cur = null;
    let prevT = null;
    const flush = (end) => {
      if (!cur) return;
      cur.end = end ?? cur.start;
      out.push(cur);
      cur = null;
    };
    for (const r of rows) {
      const gap = prevT !== null && r[F.t] - prevT > GAP_MS;
      const mark = gap ? null : classify(r);
      if (!mark) {
        flush(prevT);
      } else if (!cur || cur.key !== mark.key) {
        flush(r[F.t]);
        cur = { ...mark, start: r[F.t], end: r[F.t] };
      } else {
        cur.end = r[F.t];
      }
      prevT = r[F.t];
    }
    flush(prevT);
    const span = t1 - t0;
    return out.map((s) => ({
      ...s,
      left: ((s.start - t0) / span) * 100,
      width: Math.max(0.7, ((s.end - s.start) / span) * 100)
    }));
  }



  function duration(field, pred) {
    let ms = 0;
    for (let i = 0; i < rows.length - 1; i++) {
      const dt = rows[i + 1][F.t] - rows[i][F.t];
      if (dt <= 0 || dt > GAP_MS) continue;
      const v = rows[i].length > field ? rows[i][field] : null;
      if (v !== null && v !== undefined && pred(v)) ms += dt;
    }
    return ms;
  }



  function fmtDur(ms) {
    const m = Math.round(ms / 60_000);
    if (m < 90) return `${m} min`;
    return `${Math.floor(m / 60)} h ${m % 60} m`;
  }

  /* One implementation, in lib/units.js. These two files had byte-identical
     copies of this, which is how a 12-hour clock ends up applied to the
     chart axis and not to the timeline underneath it. */
  function clock(t) {
    return fmtClock(t, $clockPref);
  }

  function pointer(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    if (!(rect.width > 0) || !rows.length) return;
    const p = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const target = t0 + p * (t1 - t0);
    const off = offs.find((h) => target > h.start && target < h.end);
    if (off) {
      hover = {
        pct: p * 100,
        clock: `${clock(off.start)} – ${clock(off.end)}`,
        state: 'No readings',
        fan:
          $mode === 'device'
            ? 'The fan was off or restarting.'
            : 'The fan was off, or this app was closed.',
        light: '',
        odor: ''
      };
      return;
    }
    let lo = 0;
    let hi = rows.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (rows[mid][F.t] < target) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0 && Math.abs(rows[lo - 1][F.t] - target) < Math.abs(rows[lo][F.t] - target)) lo -= 1;
    const row = rows[lo];
    if (!row) return;
    const code = row.length > F.st ? row[F.st] : null;
    hover = {
      pct: ((row[F.t] - t0) / Math.max(t1 - t0, 1)) * 100,
      clock: clock(row[F.t]),
      fan: row[F.fan] === 1 ? 'Fan on' : 'Fan off',
      light: row[F.lit] === 1 ? 'Lights on' : 'Dark',
      odor: odorAt(row, cfg),
      state: STATE_NAME[code] || 'State not recorded'
    };
  }
</script>

{#if rows.length < 2}
  <div class="nodata">
    <p>Not enough history yet</p>
    <p class="hint">
      {#if $mode === 'device'}
        The fan keeps a week of its own, written every five minutes. A board
        that has just started has none to give yet.
      {:else}
        Readings are collected while this app is open.
      {/if}
    </p>
  </div>
{:else}
  {#snippet gaps()}
    {#each offs as h, i (i)}
      <i class="off" style="left:{h.left}%; width:{h.width}%"></i>
    {/each}
  {/snippet}
  <div class="lanes">
    <div class="lane">
      <span class="name">Fan</span>
      <div class="track">
        {#each fanSegs as s, i (i)}
          <i style="left:{s.left}%; width:{s.width}%; background:{s.color}"></i>
        {/each}
        {@render gaps()}
      </div>
    </div>
    <div class="lane">
      <span class="name">Light</span>
      <div class="track">
        {#each lightSegs as s, i (i)}
          <i style="left:{s.left}%; width:{s.width}%; background:{s.color}"></i>
        {/each}
        {@render gaps()}
      </div>
    </div>
    {#if hasOdor}
      <div class="lane">
        <span class="name">Odor</span>
        <div class="track">
          {#each odorSegs as s, i (i)}
            <i style="left:{s.left}%; width:{s.width}%; background:{s.color}"></i>
          {/each}
          {@render gaps()}
        </div>
      </div>
    {/if}
    <div class="lane">
      <span class="name">State</span>
      <div class="track">
        {#each stateSegs as s, i (i)}
          <i style="left:{s.left}%; width:{s.width}%; background:{s.color}"></i>
        {/each}
        {@render gaps()}
      </div>
    </div>
    {#if !compact}
      <div class="overlay">
        <div
          class="scrub"
          onpointerdown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            pointer(e);
          }}
          onpointermove={pointer}
          onpointerleave={() => (hover = null)}
          onpointercancel={() => (hover = null)}
        ></div>
        {#if hover}
          <i class="rule" style="left:{hover.pct}%"></i>
          <div class="tip" style="left:{hover.pct}%" class:flip={hover.pct > 62}>
            <b>{hover.clock}</b>
            <span>{hover.state}</span>
            <span>{hover.fan}</span>
            {#if hover.light}<span>{hover.light}</span>{/if}
            {#if hover.odor}<span>{hover.odor}</span>{/if}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  <div class="xlabels" aria-hidden="true">
    {#each labels as lab (lab.p)}
      <span style="left:{lab.p}%" class={lab.edge}>{lab.text}</span>
    {/each}
  </div>

  <div class="legend">
    <span><i style="background:var(--accent)"></i>Fan on</span>
    <span><i style="background:var(--purple)"></i>Lights on</span>
    {#if hasOdor}
      <span><i style="background:var(--green)"></i>Odor</span>
      {#if vocVeto}
        <span><i style="background:var(--orange)"></i>Combustion, ignored</span>
      {/if}
    {/if}
    <span><i style="background:var(--accent)"></i>Run</span>
    <span><i style="background:var(--warn)"></i>Boost</span>
    <span><i style="background:var(--bad)"></i>Fault</span>
    {#if offs.length}<span><i class="off"></i>Offline</span>{/if}
    {#if !hasState}<span class="pending">State starts with the next readings</span>{/if}
  </div>

  {#if summary}<p class="summary">{summary}</p>{/if}
{/if}

<style>
  .nodata {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--text-dim);
    background: var(--bg-card-2);
    border-radius: 10px;
    min-height: 96px;
  }
  .nodata p {
    margin: 0;
    font-size: 0.88rem;
  }
  .nodata .hint {
    font-size: 0.76rem;
    margin-top: 4px;
  }

  .lanes {
    position: relative;
    display: grid;
    gap: 7px;
  }
  .lane {
    display: grid;
    grid-template-columns: 46px minmax(0, 1fr);
    gap: 8px;
    align-items: center;
  }
  .name {
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  .track {
    position: relative;
    height: 12px;
    border-radius: 99px;
    background: var(--bg-card-2);
    overflow: hidden;
  }
  .track i {
    position: absolute;
    top: 0;
    bottom: 0;
  }
  /* Same hatch as the charts' offline band. */
  .track i.off,
  .legend i.off {
    background: repeating-linear-gradient(
      135deg,
      color-mix(in srgb, var(--text-dim) 30%, transparent) 0 2px,
      transparent 2px 6px
    );
  }

  .overlay {
    position: absolute;
    left: 54px;
    right: 0;
    top: 0;
    bottom: 0;
  }
  .scrub {
    position: absolute;
    inset: 0;
    cursor: crosshair;
    touch-action: none;
  }
  .rule {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    background: var(--text-dim);
    pointer-events: none;
  }
  .tip {
    position: absolute;
    top: 0;
    z-index: 2;
    transform: translateX(8px);
    background: var(--bg-card-2);
    border: 1px solid var(--border-strong);
    border-radius: 9px;
    padding: 6px 8px;
    display: grid;
    gap: 1px;
    pointer-events: none;
    box-shadow: var(--shadow);
  }
  .tip.flip {
    transform: translateX(calc(-100% - 8px));
  }
  .tip b,
  .tip span {
    font-size: 0.75rem;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .tip b {
    font-family: var(--mono);
    font-weight: 500;
  }

  .xlabels {
    position: relative;
    height: 16px;
    margin: 4px 0 0 54px;
  }
  .xlabels span {
    position: absolute;
    transform: translateX(-50%);
    font-family: var(--mono);
    font-size: 0.64rem;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .xlabels .start {
    transform: none;
  }
  .xlabels .end {
    transform: translateX(-100%);
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    margin-top: 8px;
    font-size: 0.72rem;
    color: var(--text-dim);
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .legend i {
    width: 10px;
    height: 8px;
    border-radius: 2px;
  }
  .pending {
    margin-left: auto;
  }
  .summary {
    margin: 8px 0 0;
    font-size: 0.86rem;
    color: var(--text);
  }
</style>
