<script>
  /**
   * A small multi-series time chart, hand-drawn in SVG.
   *
   * No charting library: the app is served over a LAN with no route out, so
   * a CDN script is a blank chart, and bundling one to draw four lines is
   * several hundred kilobytes for something this file does in eighty.
   *
   * Every label names a value the chart actually reaches -- the y axis is
   * taken from the data, not from a round number that may sit off the top
   * of the drawing.
   */
  import { F, segments, GAP_MS } from '$lib/history.js';

  let {
    rows = [],
    series = [],
    height = 150,
    unit = '',
    bands = null,
    refs = [],
    compact = false
  } = $props();

  const W = 600;
  const PAD_L = 38;
  const PAD_R = 8;
  const PAD_T = 10;
  const PAD_B = 20;

  let H = $derived(height);
  let plotW = W - PAD_L - PAD_R;
  let plotH = $derived(H - PAD_T - PAD_B);

  let t0 = $derived(rows.length ? rows[0][F.t] : 0);
  let t1 = $derived(rows.length ? rows[rows.length - 1][F.t] : 1);

  let bounds = $derived.by(() => {
    const vals = [];
    for (const s of series) {
      for (const r of rows) {
        const v = r[s.field];
        if (v !== null && v !== undefined) vals.push(v);
      }
    }
    for (const r of refs) if (Number.isFinite(r.value)) vals.push(r.value);
    if (!vals.length) return { lo: 0, hi: 1 };
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    if (hi - lo < 1e-6) {
      // A flat series would collapse to a zero-height box and the line
      // would sit on the axis looking like missing data.
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.12;
    return { lo: lo - pad, hi: hi + pad };
  });

  function x(t) {
    if (t1 === t0) return PAD_L + plotW;
    return PAD_L + ((t - t0) / (t1 - t0)) * plotW;
  }

  function y(v) {
    const { lo, hi } = bounds;
    return PAD_T + plotH - ((v - lo) / (hi - lo)) * plotH;
  }

  function path(seg) {
    return seg.map((p, i) => `${i ? 'L' : 'M'} ${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(' ');
  }

  function area(seg) {
    if (seg.length < 2) return '';
    const base = PAD_T + plotH;
    return (
      path(seg) +
      ` L ${x(seg[seg.length - 1][0]).toFixed(1)} ${base}` +
      ` L ${x(seg[0][0]).toFixed(1)} ${base} Z`
    );
  }

  let ticks = $derived.by(() => {
    const { lo, hi } = bounds;
    const out = [];
    for (let i = 0; i <= 2; i++) {
      const v = lo + ((hi - lo) * i) / 2;
      out.push({ v, y: y(v) });
    }
    return out;
  });

  let xticks = $derived.by(() => {
    if (!rows.length || t1 === t0) return [];
    const out = [];
    for (let i = 0; i <= (compact ? 1 : 3); i++) {
      const t = t0 + ((t1 - t0) * i) / (compact ? 1 : 3);
      out.push({ t, x: x(t) });
    }
    return out;
  });

  function clock(t) {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  function fmt(v) {
    const span = bounds.hi - bounds.lo;
    return span < 10 ? v.toFixed(1) : String(Math.round(v));
  }

  // Shaded periods when the fan was running, drawn behind everything. The
  // question a humidity chart is actually asked is "did the fan deal with
  // it", which needs both on one drawing.
  let runs = $derived.by(() => {
    if (!bands) return [];
    const out = [];
    let start = null;
    let prev = null;
    for (const r of rows) {
      const on = r[bands] === 1;
      if (on && start === null) start = r[F.t];
      if (!on && start !== null) {
        out.push([start, r[F.t]]);
        start = null;
      }
      if (prev !== null && r[F.t] - prev > GAP_MS && start !== null) {
        out.push([start, prev]);
        start = null;
      }
      prev = r[F.t];
    }
    if (start !== null && prev !== null) out.push([start, prev]);
    return out;
  });
</script>

{#if rows.length < 2}
  <div class="nodata" style="height:{H}px">
    <p>Not enough history yet</p>
    <p class="hint">Readings are collected while this app is open.</p>
  </div>
{:else}
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="chart">
    {#each runs as [a, b] (a)}
      <rect
        x={x(a)}
        y={PAD_T}
        width={Math.max(1, x(b) - x(a))}
        height={plotH}
        class="run"
      />
    {/each}

    {#each ticks as tk (tk.v)}
      <line x1={PAD_L} y1={tk.y} x2={W - PAD_R} y2={tk.y} class="grid" />
      <text x={PAD_L - 6} y={tk.y + 3.5} class="ylab">{fmt(tk.v)}</text>
    {/each}

    {#each xticks as tk (tk.t)}
      <text x={tk.x} y={H - 6} class="xlab">{clock(tk.t)}</text>
    {/each}

    {#each refs as r (r.label)}
      <line x1={PAD_L} y1={y(r.value)} x2={W - PAD_R} y2={y(r.value)} class="ref" />
      {#if !compact}
        <text x={W - PAD_R - 2} y={y(r.value) - 4} class="reflab">{r.label}</text>
      {/if}
    {/each}

    {#each series as s (s.field)}
      {#each segments(rows, s.field) as seg, i (i)}
        {#if s.fill}
          <path d={area(seg)} class="area" style="fill:{s.color}" />
        {/if}
        <path d={path(seg)} class="line" style="stroke:{s.color}" />
      {/each}
    {/each}
  </svg>

  <div class="legend">
    {#each series as s (s.field)}
      <span><i style="background:{s.color}"></i>{s.label}</span>
    {/each}
    {#each refs as r (r.label)}
      <span><i class="swatch-ref"></i>{r.label}</span>
    {/each}
    {#if bands}
      <span><i class="swatch-run"></i>fan running</span>
    {/if}
    {#if unit}<span class="unit">{unit}</span>{/if}
  </div>
{/if}

<style>
  svg {
    width: 100%;
    display: block;
    overflow: visible;
  }

  .grid {
    stroke: var(--border);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }

  /* The thresholds that actually decide whether the fan runs. Without
     them the reader can see the shape but not whether it mattered. */
  .ref {
    stroke: var(--text-muted);
    stroke-width: 1;
    stroke-dasharray: 3 3;
    vector-effect: non-scaling-stroke;
  }
  .reflab {
    fill: var(--text-dim);
    font-family: var(--mono);
    font-size: 8.5px;
    text-anchor: end;
  }

  .run {
    fill: var(--accent);
    opacity: 0.1;
  }

  .line {
    fill: none;
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }

  .area {
    opacity: 0.12;
    stroke: none;
  }

  .ylab,
  .xlab {
    fill: var(--text-dim);
    font-family: var(--mono);
    font-size: 9px;
  }
  .ylab {
    text-anchor: end;
  }
  .xlab {
    text-anchor: middle;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 5px 14px;
    margin-top: 10px;
    font-size: 0.72rem;
    color: var(--text-dim);
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .legend i {
    width: 9px;
    height: 3px;
    border-radius: 2px;
    display: inline-block;
  }
  .legend .swatch-ref {
    height: 0;
    width: 11px;
    border-top: 1px dashed var(--text-dim);
    border-radius: 0;
  }
  .legend .swatch-run {
    height: 9px;
    border-radius: 2px;
    background: var(--accent);
    opacity: 0.25;
  }
  .legend .unit {
    margin-left: auto;
    font-family: var(--mono);
  }

  .nodata {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--text-dim);
    border: 1px dashed var(--border);
    border-radius: 10px;
  }
  .nodata p {
    margin: 0;
    font-size: 0.88rem;
  }
  .nodata .hint {
    font-size: 0.76rem;
    margin-top: 4px;
    opacity: 0.8;
  }
</style>
