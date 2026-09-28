<script>
  /**
   * Hand-drawn time chart.
   *
   * No charting library: the app is served on a LAN with no route out, so a
   * CDN script is a blank chart, and bundling one to draw a few lines is
   * hundreds of kilobytes for something this file can do itself.
   *
   * The scale is chosen so the ticks are numbers a person would say. A
   * quantity that already sits on zero (fan speed, light) is grounded there,
   * so the axis cannot wander off into negative rpm. Humidity and
   * temperature stay zoomed on the band they actually occupy.
   *
   * Text is HTML, not SVG. The drawing is stretched to the card;
   * preserveAspectRatio would letterbox it, and text inside that SVG would
   * be stretched with it.
   */
  import { mode } from '$lib/link.js';
  import { F, segments, stats, GAP_MS } from '$lib/history.js';
  import { clock as clockPref, fmtClock } from '$lib/units.js';

  let {
    rows = [],
    series = [],
    height = 150,
    unit = '',
    bands = null,
    bandOn = '',
    bandOff = '',
    bandStory = '',
    bandIdle = '',
    bandColor = 'var(--accent)',
    refs = [],
    compact = false,
    /* Stored value -> displayed value, for a chart whose units are a viewer
       preference. Applied once here rather than threaded through every read
       site, and to the threshold lines as well, so an axis, a tooltip, a peak
       label and a reference line cannot end up in different units. */
    convert = null,
    /* "These two series are worth comparing", for the narration below. This
       used to be `unit === '°C'`, which was a type tag wearing a unit's
       clothes and stopped being true the moment the unit could be °F too. */
    compare = false
  } = $props();

  const uid = Math.random().toString(36).slice(2, 8);

  /* Everything below reads `data`, never `rows`. One pass, only when a
     conversion is actually in force, and the timestamp column is copied
     untouched so segments and gaps behave exactly as before. */
  let data = $derived.by(() => {
    if (!convert) return rows;
    const fields = series.map((s) => s.field);
    return rows.map((r) => {
      const c = r.slice();
      for (const f of fields) {
        if (c[f] !== null && c[f] !== undefined && Number.isFinite(c[f])) c[f] = convert(c[f]);
      }
      return c;
    });
  });

  /* The threshold lines, in the same units as `data`. Named refValues
     because refMarks below is already the positioned version of these. */
  let refValues = $derived(
    convert
      ? refs.map((r) => (Number.isFinite(r.value) ? { ...r, value: convert(r.value) } : r))
      : refs
  );

  let t0 = $derived(data.length ? data[0][F.t] : 0);
  let t1 = $derived(data.length ? data[data.length - 1][F.t] : 1);

  let scale = $derived.by(() => {
    const vals = [];
    for (const s of series) {
      for (const r of data) {
        const v = r[s.field];
        if (v !== null && v !== undefined && Number.isFinite(v)) vals.push(v);
      }
    }
    // refValues, not refMarks: refMarks positions each line with yPct, which
    // reads `scale` -- and this IS scale. The converted values are what the
    // axis needs, and they do not depend on it.
    for (const ref of refValues) if (Number.isFinite(ref.value)) vals.push(ref.value);
    if (!vals.length) return { lo: 0, hi: 1, ticks: [0, 1] };

    let lo0 = Math.min(...vals);
    let hi0 = Math.max(...vals);
    let span = hi0 - lo0;
    if (span < 1e-6) {
      lo0 -= 1;
      hi0 += 1;
      span = 2;
    }

    // Ground at zero only when the data already lives down there. Humidity
    // at 60% must not be drawn from an axis of zero or the shower disappears.
    if (lo0 >= 0 && lo0 <= span * 0.35) lo0 = 0;
    hi0 += Math.max(span, 1) * 0.08;

    const step = niceStep(hi0 - lo0);
    let lo = Math.floor(lo0 / step) * step;
    let hi = Math.ceil(hi0 / step) * step;
    if (lo0 >= 0 && lo < 0) lo = 0;
    // A slight dip under zero (excess at -0.4) should still be visible,
    // without spending a whole grid band on empty negative space.
    if (lo0 < 0 && lo <= -step && lo0 > -step * 0.35) lo = lo0 - step * 0.15;
    if (!(hi > lo)) hi = lo + step;

    const ticks = [];
    const i0 = Math.ceil(lo / step - 1e-6);
    const i1 = Math.floor(hi / step + 1e-6);
    for (let i = i0; i <= i1 && ticks.length < 7; i++) {
      const v = i * step;
      if (v < lo - step * 0.01 || v > hi + step * 0.01) continue;
      ticks.push(roundTick(v, step));
    }
    if (ticks.length < 2) return { lo, hi, ticks: [roundTick(lo, step), roundTick(hi, step)] };
    return { lo, hi, ticks };
  });

  function y(v) {
    const { lo, hi } = scale;
    if (!(hi > lo)) return 500;
    return 1000 - ((v - lo) / (hi - lo)) * 1000;
  }

  function x(t) {
    if (t1 === t0) return 1000;
    return ((t - t0) / (t1 - t0)) * 1000;
  }

  function yPct(v) {
    return y(v) / 10;
  }

  let guides = $derived(series.filter((s) => !s.fill));
  let zeroInView = $derived(scale.lo < 0 && scale.hi > 0);

  let bandsBetween = $derived.by(() => {
    const out = [];
    for (const s of series) {
      if (!s.fill) continue;
      const partner = guides[0];
      if (!partner) continue;
      out.push({ color: s.color, paths: between(s.field, partner.field) });
    }
    return out;
  });

  let areas = $derived.by(() => {
    if (guides.length && series.some((s) => s.fill)) return [];
    const floor = zeroInView ? 0 : scale.lo;
    const out = [];
    for (const s of series) {
      if (!s.fill) continue;
      for (const seg of segments(data, s.field)) {
        if (seg.length < 2) continue;
        const line = seg
          .map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`)
          .join(' ');
        const y0 = y(floor).toFixed(1);
        out.push({
          color: s.color,
          d:
            line +
            ` L${x(seg[seg.length - 1][0]).toFixed(1)} ${y0}` +
            ` L${x(seg[0][0]).toFixed(1)} ${y0} Z`
        });
      }
    }
    return out;
  });

  let lines = $derived(
    series.map((s) => ({
      field: s.field,
      color: s.color,
      dash: !!s.dash,
      paths: segments(data, s.field).map((seg) =>
        seg.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(' ')
      )
    }))
  );

  let refMarks = $derived.by(() => {
    const marks = refValues
      .filter((r) => Number.isFinite(r.value))
      .map((r) => ({ ...r, pct: yPct(r.value) }));
    marks.sort((a, b) => a.pct - b.pct);
    for (let i = 1; i < marks.length; i++) {
      if (marks[i].pct - marks[i - 1].pct < 10) marks[i].pct = marks[i - 1].pct + 10;
    }
    return marks;
  });

  let rail = $derived.by(() => {
    if (bands === null || bands === undefined || data.length < 2) return [];
    const out = [];
    let start = null;
    let prev = null;
    for (const r of data) {
      const on = r[bands] === 1;
      const gap = prev !== null && r[F.t] - prev > GAP_MS;
      if (gap && start !== null) {
        out.push([start, prev]);
        start = null;
      }
      if (on && start === null) start = r[F.t];
      if (!on && start !== null) {
        out.push([start, r[F.t]]);
        start = null;
      }
      prev = r[F.t];
    }
    if (start !== null && prev !== null) out.push([start, prev]);
    const span = Math.max(t1 - t0, 1);
    return out.map(([a, b]) => ({
      left: ((a - t0) / span) * 100,
      width: Math.max(0.6, ((b - a) / span) * 100)
    }));
  });

  let ends = $derived(
    series.map((s) => {
      for (let i = data.length - 1; i >= 0; i--) {
        const v = data[i][s.field];
        if (v !== null && v !== undefined && Number.isFinite(v)) {
          return {
            field: s.field,
            color: s.color,
            v,
            left: x(data[i][F.t]) / 10,
            top: yPct(v)
          };
        }
      }
      return null;
    }).filter(Boolean)
  );

  let peak = $derived.by(() => {
    if (compact || !series.length) return null;
    const field = series[0].field;
    let best = null;
    for (const r of data) {
      const v = r[field];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      if (!best || v > best.v) best = { v, t: r[F.t] };
    }
    if (!best) return null;
    const st = stats(data, field);
    if (!st || st.max - st.min < (unit === 'rpm' ? 80 : 1.5)) return null;
    return {
      v: best.v,
      left: x(best.t) / 10,
      top: yPct(best.v),
      color: series[0].color
    };
  });

  let xLabels = $derived.by(() => {
    if (!data.length || t1 === t0) return [];
    const n = compact ? 2 : 4;
    const out = [];
    const fresh = Date.now() - t1 < 3 * 60_000;
    for (let i = 0; i < n; i++) {
      const p = n === 1 ? 1 : i / (n - 1);
      const t = t0 + (t1 - t0) * p;
      out.push({
        p: p * 100,
        text: i === n - 1 && fresh ? 'now' : clock(t),
        edge: i === 0 ? 'start' : i === n - 1 ? 'end' : ''
      });
    }
    return out;
  });

  let summary = $derived.by(() => {
    if (compact || data.length < 2 || !series.length) return '';
    const field = series[0].field;
    const st = stats(data, field);
    if (!st) return '';
    const suffix = unit === 'rpm' ? ' rpm' : unit;
    const steady = st.max - st.min < (unit === 'rpm' ? 80 : 1);
    const head = steady
      ? `Steady near ${fmtVal(st.last)}${suffix}`
      : `Peaked at ${fmtVal(st.max)}${suffix}`;
    const parts = [];
    // refValues, not refs: this threshold is compared against converted data
    // and then printed, so a raw ref would put the narration in one unit and
    // the number it quotes in another.
    const levels = refValues.map((r) => r.value).filter((v) => Number.isFinite(v));
    if (levels.length) {
      const start = Math.max(...levels);
      const above = durationWhere(field, (v) => v >= start);
      parts.push(
        above >= 60_000
          ? `${head}, over the ${fmtVal(start)}${suffix} line for ${fmtDur(above)}.`
          : `${head}. Stayed under the ${fmtVal(start)}${suffix} line.`
      );
    } else {
      parts.push(`${head}.`);
    }
    if (bands !== null && bands !== undefined && bandStory) {
      const on = durationWhere(bands, (v) => v === 1);
      parts.push(on >= 20_000 ? `${bandStory} for ${fmtDur(on)}.` : `${bandIdle || bandOff}.`);
    }
    if (compare && series.length >= 2) {
      let closest = Infinity;
      const a = series[0].field;
      const b = series[1].field;
      for (const r of data) {
        if (r[a] === null || r[b] === null || r[a] === undefined || r[b] === undefined) continue;
        const g = Math.abs(r[a] - r[b]);
        if (g < closest) closest = g;
      }
      if (Number.isFinite(closest) && closest < Infinity) {
        parts.push(`Closest those two came: ${fmtVal(closest)}${suffix}.`);
      }
    }
    return parts.join(' ');
  });

  let hover = $state(null);

  function pointer(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    if (!(rect.width > 0)) return;
    const p = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const target = t0 + p * (t1 - t0);
    let lo = 0;
    let hi = data.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (data[mid][F.t] < target) lo = mid + 1;
      else hi = mid;
    }
    let idx = lo;
    if (idx > 0 && Math.abs(data[idx - 1][F.t] - target) < Math.abs(data[idx][F.t] - target)) {
      idx -= 1;
    }
    const row = data[idx];
    if (!row) return;
    const span = Math.max(t1 - t0, 1);
    hover = {
      pct: ((row[F.t] - t0) / span) * 100,
      clock: clock(row[F.t]),
      values: series.map((s) => ({
        label: s.label,
        color: s.color,
        text:
          row[s.field] === null || row[s.field] === undefined ? '—' : `${fmtVal(row[s.field])}${unit === 'rpm' ? '' : unit}`
      })),
      band:
        bands === null || bands === undefined
          ? ''
          : row[bands] === 1
            ? bandOn || 'On'
            : bandOff || 'Off'
    };
  }

  function between(fieldA, fieldB) {
    const polys = [];
    let run = [];
    const flush = () => {
      if (run.length > 1) polys.push(run);
      run = [];
    };
    for (const r of data) {
      const a = r[fieldA];
      const b = r[fieldB];
      const broke = run.length && r[F.t] - run[run.length - 1][0] > GAP_MS;
      if (a === null || a === undefined || b === null || b === undefined || broke) {
        flush();
        if (a === null || a === undefined || b === null || b === undefined) continue;
      }
      run.push([r[F.t], a, b]);
    }
    flush();
    return polys.map((run) => {
      const fwd = run
        .map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`)
        .join(' ');
      const back = [...run]
        .reverse()
        .map((p) => `L${x(p[0]).toFixed(1)} ${y(p[2]).toFixed(1)}`)
        .join(' ');
      return `${fwd} ${back} Z`;
    });
  }

  function durationWhere(field, pred) {
    let ms = 0;
    for (let i = 0; i < data.length - 1; i++) {
      const dt = data[i + 1][F.t] - data[i][F.t];
      if (dt <= 0 || dt > GAP_MS) continue;
      const v = data[i][field];
      if (v !== null && v !== undefined && pred(v)) ms += dt;
    }
    return ms;
  }

  function fmtVal(v) {
    if (!Number.isFinite(v)) return '—';
    if (unit === 'rpm' || Math.abs(v) >= 100) return Math.round(v).toLocaleString();
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  function fmtTick(v) {
    if (!Number.isFinite(v)) return '';
    if (Math.abs(v) >= 100) return Math.round(v).toLocaleString();
    if (Math.abs(v - Math.round(v)) < 1e-6) return String(Math.round(v));
    return v.toFixed(1);
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

  function latest(field) {
    for (let i = data.length - 1; i >= 0; i--) {
      const v = data[i][field];
      if (v !== null && v !== undefined && Number.isFinite(v)) return fmtVal(v);
    }
    return '—';
  }

  function niceStep(span) {
    const rough = span / 4;
    if (!(rough > 0)) return 1;
    const pow = 10 ** Math.floor(Math.log10(rough));
    const err = rough / pow;
    if (err < 1.5) return pow;
    if (err < 3) return 2 * pow;
    if (err < 7) return 5 * pow;
    return 10 * pow;
  }

  function roundTick(v, step) {
    const d = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    const f = 10 ** d;
    return Math.round(v * f) / f;
  }
</script>

{#if data.length < 2}
  <div class="nodata" style="height:{height}px">
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
  <div class="plot" style="height:{height}px">
    <div class="ylabels" aria-hidden="true">
      {#each scale.ticks as v (v)}
        <span style="top:{yPct(v)}%">{fmtTick(v)}</span>
      {/each}
    </div>

    <div class="stage">
    <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
      {#each scale.ticks as v (v)}
        <line x1="0" y1={y(v)} x2="1000" y2={y(v)} class="grid" class:axis={Math.abs(v) < 1e-6} />
      {/each}

      {#each bandsBetween as band, i (i)}
        {#each band.paths as d, j (j)}
          <path {d} class="area" style="fill:{band.color}" />
        {/each}
      {/each}

      {#each areas as area, i (i)}
        <path d={area.d} class="area fade" style="fill:url(#{uid}-{i})" />
      {/each}

      <defs>
        {#each areas as area, i (i)}
          <linearGradient id="{uid}-{i}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color={area.color} stop-opacity="0.32" />
            <stop offset="100%" stop-color={area.color} stop-opacity="0.02" />
          </linearGradient>
        {/each}
      </defs>

      {#each refMarks as r (r.label)}
        {#if Number.isFinite(r.value)}
          <line x1="0" y1={y(r.value)} x2="1000" y2={y(r.value)} class="ref" />
        {/if}
      {/each}

      {#each lines as s (s.field)}
        {#each s.paths as d, i (i)}
          <path {d} class="line" class:dash={s.dash} style="stroke:{s.color}" />
        {/each}
      {/each}
    </svg>

    {#if !compact}
      <!-- The card underneath is the button. This layer exists only in the
           detail sheet, where dragging is how you read one moment. -->
      <div
        class="scrub"
        onpointerdown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          pointer(e);
        }}
        onpointermove={pointer}
        onpointercancel={() => (hover = null)}
        onpointerleave={() => (hover = null)}
      ></div>
    {/if}

    {#each ends as dot (dot.field)}
      <i class="end" style="left:{dot.left}%; top:{dot.top}%; background:{dot.color}"></i>
    {/each}

    {#if peak}
      <i class="end peak" style="left:{peak.left}%; top:{peak.top}%; background:{peak.color}"></i>
      <span class="peaklab" style="left:{peak.left}%; top:{peak.top}%" class:below={peak.top < 16}>
        {fmtVal(peak.v)}
      </span>
    {/if}

    {#if !compact}
      {#each refMarks as r (r.label)}
        <span class="reflab" style="top:{r.pct}%">{r.label} {fmtVal(r.value)}</span>
      {/each}
    {/if}

    {#if hover}
      <i class="rule" style="left:{hover.pct}%"></i>
      <div class="tip" style="left:{hover.pct}%" class:flip={hover.pct > 62}>
        <b>{hover.clock}</b>
        {#each hover.values as v (v.label)}
          <span><i style="background:{v.color}"></i>{v.label} {v.text}</span>
        {/each}
        {#if hover.band}<span class="band">{hover.band}</span>{/if}
      </div>
    {/if}
    </div>
  </div>

  {#if rail.length || (bands !== null && bands !== undefined)}
    <div class="rail" aria-hidden="true" title={bandOn || 'on'}>
      {#each rail as seg, i (i)}
        <i style="left:{seg.left}%; width:{seg.width}%; background:{bandColor}"></i>
      {/each}
    </div>
  {/if}

  <div class="xlabels" aria-hidden="true">
    {#each xLabels as lab (lab.p)}
      <span style="left:{lab.p}%" class={lab.edge}>{lab.text}</span>
    {/each}
  </div>

  <div class="legend">
    {#each series as s (s.field)}
      <span><i style="background:{s.color}"></i>{s.label} <b>{latest(s.field)}{unit === 'rpm' ? '' : unit}</b></span>
    {/each}
    {#each refMarks as r (r.label)}
      <span><i class="swatch-ref"></i>{r.label} {fmtVal(r.value)}</span>
    {/each}
    {#if bands !== null && bands !== undefined}
      <span><i class="swatch-run" style="background:{bandColor}"></i>{bandOn || 'on'}</span>
    {/if}
    {#if unit}<span class="unit">{unit}</span>{/if}
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
  }
  .nodata p {
    margin: 0;
    font-size: 0.88rem;
  }
  .nodata .hint {
    font-size: 0.76rem;
    margin-top: 4px;
  }

  .plot {
    position: relative;
  }

  .ylabels {
    position: absolute;
    inset: 0 auto 0 0;
    width: 36px;
    pointer-events: none;
  }
  .ylabels span {
    position: absolute;
    right: 0;
    transform: translateY(-50%);
    font-family: var(--mono);
    font-size: 0.66rem;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }

  .stage {
    position: absolute;
    inset: 0 0 0 42px;
  }

  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    display: block;
    overflow: hidden;
  }

  .grid {
    stroke: var(--border);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .grid.axis {
    stroke: var(--border-strong);
  }

  .area {
    stroke: none;
    opacity: 0.22;
  }
  .area.fade {
    opacity: 1;
  }

  .line {
    fill: none;
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .line.dash {
    stroke-dasharray: 5 4;
  }

  /* Thresholds. text-dim, not the brand muted grey: that one disappears
     on these cards, and this line is the thing the chart is here to show. */
  .ref {
    stroke: var(--text-dim);
    stroke-width: 1.25;
    stroke-dasharray: 2 3;
    vector-effect: non-scaling-stroke;
  }

  .scrub {
    position: absolute;
    inset: 0;
    touch-action: none;
    cursor: crosshair;
  }

  .end {
    position: absolute;
    width: 7px;
    height: 7px;
    margin: -3.5px 0 0 -3.5px;
    border-radius: 50%;
    box-shadow: 0 0 0 2px var(--bg-card);
    pointer-events: none;
  }
  .end.peak {
    width: 8px;
    height: 8px;
    margin: -4px 0 0 -4px;
  }
  .peaklab {
    position: absolute;
    transform: translate(-50%, -16px);
    font-family: var(--mono);
    font-size: 0.66rem;
    color: var(--text);
    pointer-events: none;
    font-variant-numeric: tabular-nums;
  }
  .peaklab.below {
    transform: translate(-50%, 8px);
  }

  .reflab {
    position: absolute;
    right: 0;
    transform: translateY(-50%);
    font-family: var(--mono);
    font-size: 0.64rem;
    color: var(--text-dim);
    background: var(--bg-card);
    padding: 0 3px;
    pointer-events: none;
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
    top: 8px;
    z-index: 2;
    transform: translateX(8px);
    background: var(--bg-card-2);
    border: 1px solid var(--border-strong);
    border-radius: 9px;
    padding: 6px 8px 7px;
    display: grid;
    gap: 2px;
    min-width: 132px;
    pointer-events: none;
    box-shadow: var(--shadow);
  }
  .tip.flip {
    transform: translateX(calc(-100% - 8px));
  }
  .tip b {
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--text);
  }
  .tip span {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.75rem;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .tip i {
    width: 8px;
    height: 3px;
    border-radius: 2px;
    flex: none;
  }
  .tip .band {
    color: var(--text-dim);
    font-size: 0.7rem;
  }

  .rail {
    position: relative;
    height: 6px;
    margin: 6px 0 0 42px;
    border-radius: 99px;
    background: var(--bg-card-2);
    overflow: hidden;
  }
  .rail i {
    position: absolute;
    top: 0;
    bottom: 0;
  }

  .xlabels {
    position: relative;
    height: 16px;
    margin: 3px 0 0 42px;
  }
  .xlabels span {
    position: absolute;
    top: 0;
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
    gap: 4px 14px;
    margin-top: 8px;
    font-size: 0.75rem;
    color: var(--text-dim);
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .legend b {
    color: var(--text);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .legend i {
    width: 10px;
    height: 3px;
    border-radius: 2px;
    display: inline-block;
  }
  .legend .swatch-ref {
    height: 0;
    width: 12px;
    border-top: 1px dashed var(--text-dim);
    border-radius: 0;
  }
  .legend .swatch-run {
    height: 8px;
    border-radius: 2px;
    opacity: 0.85;
  }
  .legend .unit {
    margin-left: auto;
    font-family: var(--mono);
  }

  .summary {
    margin: 8px 0 0;
    font-size: 0.86rem;
    color: var(--text);
    max-width: 68ch;
  }
</style>
