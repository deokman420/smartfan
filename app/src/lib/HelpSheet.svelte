<script>
  /**
   * The help center: a searchable list of topics and one topic at a time.
   *
   * Content only. The page wraps this in its own .sheet and .scrim, so the
   * sheet's drag-to-dismiss, safe-area padding and centered-dialog layout
   * stay in one place for every sheet in the app.
   *
   * `topic` is bindable so the page can open help straight at a topic (the
   * "?" beside a Thresholds group, a fault on Health). Moving between topics
   * keeps a short trail so Back returns to where you came from, not always
   * to the index; a topic opened from outside starts its own trail, and Back
   * from there goes to the index, because that is the one place you have not
   * been but certainly want.
   *
   * Everything renders through ordinary elements -- see help.js -- so no raw
   * HTML insertion and no injection surface, even though the content is ours.
   */
  import { SECTIONS, TOPICS, BY_ID, search, runs } from '$lib/help.js';

  let { topic = $bindable(null), onClose } = $props();

  let query = $state('');
  let trail = $state([]);
  let body = $state(null);

  const current = $derived(topic ? BY_ID[topic] : null);
  const results = $derived(search(query));
  const bySection = SECTIONS.map((s) => ({
    ...s,
    topics: TOPICS.filter((t) => t.section === s.id)
  }));

  function open(id) {
    if (!BY_ID[id]) return;
    if (topic) trail = [...trail, topic];
    topic = id;
    scrollTop();
  }

  function back() {
    topic = trail.length ? trail[trail.length - 1] : null;
    trail = trail.slice(0, -1);
    scrollTop();
  }

  function scrollTop() {
    // The sheet itself is the scroll container, not this component.
    queueMicrotask(() => body?.closest('.sheet')?.scrollTo({ top: 0 }));
  }
</script>

<div class="help" bind:this={body}>
  <div class="grabber" data-sheet-handle aria-hidden="true"></div>

  <div class="bar">
    {#if current}
      <button type="button" class="back" onclick={back}>
        <span aria-hidden="true">‹</span> {trail.length ? 'Back' : 'All topics'}
      </button>
    {:else}
      <h3>Help</h3>
    {/if}
    <button type="button" class="close" onclick={onClose} aria-label="Close help">✕</button>
  </div>

  {#if current}
    <article aria-labelledby="help-title">
      <p class="eyebrow">{SECTIONS.find((s) => s.id === current.section)?.title}</p>
      <h3 id="help-title">{current.title}</h3>
      <p class="summary">{current.summary}</p>

      {#each current.blocks as b, i (i)}
        {#if b.p}
          <p>{#each runs(b.p) as r, j (j)}{#if r.strong}<strong>{r.text}</strong>{:else}{r.text}{/if}{/each}</p>
        {:else if b.note}
          <p class="note">{#each runs(b.note) as r, j (j)}{#if r.strong}<strong>{r.text}</strong>{:else}{r.text}{/if}{/each}</p>
        {:else if b.steps}
          <ol>
            {#each b.steps as s, j (j)}
              <li>{#each runs(s) as r, k (k)}{#if r.strong}<strong>{r.text}</strong>{:else}{r.text}{/if}{/each}</li>
            {/each}
          </ol>
        {:else if b.list}
          <ul>
            {#each b.list as s, j (j)}
              <li>{#each runs(s) as r, k (k)}{#if r.strong}<strong>{r.text}</strong>{:else}{r.text}{/if}{/each}</li>
            {/each}
          </ul>
        {:else if b.rows}
          <dl>
            {#each b.rows as [term, meaning], j (j)}
              <div>
                <dt>{term}</dt>
                <dd>{#each runs(meaning) as r, k (k)}{#if r.strong}<strong>{r.text}</strong>{:else}{r.text}{/if}{/each}</dd>
              </div>
            {/each}
          </dl>
        {:else if b.see}
          <nav class="see" aria-label="Related topics">
            <p class="eyebrow">Related</p>
            {#each b.see.filter((id) => BY_ID[id]) as id (id)}
              <button type="button" class="topic" onclick={() => open(id)}>
                <span class="t-title">{BY_ID[id].title}</span>
                <span class="chev" aria-hidden="true">›</span>
              </button>
            {/each}
          </nav>
        {/if}
      {/each}
    </article>
  {:else}
    <input
      class="search"
      type="search"
      placeholder="Search help, e.g. “fault” or “wifi”"
      aria-label="Search help"
      bind:value={query}
      autocapitalize="off"
      autocorrect="off"
      spellcheck="false"
    />

    {#if query.trim()}
      {#if results.length}
        <div class="group" aria-live="polite">
          {#each results as t (t.id)}
            <button type="button" class="topic" onclick={() => open(t.id)}>
              <span class="t-text">
                <span class="t-title">{t.title}</span>
                <span class="t-sum">{t.summary}</span>
              </span>
              <span class="chev" aria-hidden="true">›</span>
            </button>
          {/each}
        </div>
      {:else}
        <p class="empty">
          Nothing matches “{query.trim()}”. Try one word, such as <em>light</em>,
          <em>boost</em> or <em>offline</em>.
        </p>
      {/if}
    {:else}
      {#each bySection as s (s.id)}
        <section class="group" aria-labelledby="help-sec-{s.id}">
          <p class="eyebrow" id="help-sec-{s.id}">{s.title}</p>
          {#each s.topics as t (t.id)}
            <button type="button" class="topic" onclick={() => open(t.id)}>
              <span class="t-text">
                <span class="t-title">{t.title}</span>
                <span class="t-sum">{t.summary}</span>
              </span>
              <span class="chev" aria-hidden="true">›</span>
            </button>
          {/each}
        </section>
      {/each}
    {/if}
  {/if}
</div>

<style>
  .help {
    display: grid;
    gap: 10px;
    align-content: start;
    min-width: 0;
  }

  /* The page's .grabber rule is scoped to the page, so the handle this
     component carries needs its own copy of the look. sheetDrag finds it by
     data-sheet-handle, wherever it lives inside the sheet. */
  .grabber {
    width: 56px;
    height: 20px;
    box-sizing: border-box;
    padding: 8px 10px;
    background: var(--border-strong);
    background-clip: content-box;
    border-radius: 999px;
    justify-self: center;
    margin: -6px 0 -2px;
    cursor: grab;
    touch-action: none;
  }
  @media (min-width: 560px) {
    .grabber {
      display: none;
    }
  }

  .bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 36px;
  }

  h3 {
    font-size: 1.2rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.02em;
  }

  .back,
  .close {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    border-radius: 999px;
    font-size: 0.85rem;
    min-height: 36px;
  }
  .back {
    padding: 6px 14px 6px 10px;
  }
  .back span {
    font-size: 1.1rem;
    line-height: 0;
  }
  .close {
    width: 36px;
    padding: 0;
    flex: none;
  }
  .back:hover,
  .close:hover {
    color: var(--text);
    border-color: var(--border-strong);
  }

  /* The page's input look, repeated because page styles are scoped. */
  .search {
    width: 100%;
    box-sizing: border-box;
    background: var(--bg-card-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 11px 12px;
    font-size: 16px; /* below this iOS zooms the page on focus */
    color: var(--text);
    transition: border-color 0.2s;
  }
  .search:hover,
  .search:focus {
    border-color: var(--border-strong);
  }

  .eyebrow {
    margin: 0;
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  .group {
    display: grid;
    gap: 4px;
    margin-top: 6px;
  }
  .group .eyebrow {
    margin: 4px 0 2px;
  }

  .topic {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    text-align: left;
    padding: 10px 12px;
    min-height: 44px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-card-2);
    color: var(--text);
  }
  .topic:hover {
    background: var(--bg-card-hover);
  }
  .t-text {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .t-title {
    font-weight: 600;
    font-size: 0.92rem;
  }
  .t-sum {
    color: var(--text-dim);
    font-size: 0.8rem;
    line-height: 1.35;
  }
  .chev {
    color: var(--text-dim);
    font-size: 1.2rem;
    flex: none;
  }

  .empty {
    color: var(--text-dim);
    font-size: 0.9rem;
    margin: 8px 0;
  }

  article {
    display: grid;
    gap: 10px;
    min-width: 0;
    line-height: 1.5;
    font-size: 0.95rem;
  }
  article h3 {
    font-size: 1.3rem;
  }
  article p,
  article ol,
  article ul,
  article dl {
    margin: 0;
  }
  .summary {
    color: var(--text-dim);
  }
  ol,
  ul {
    padding-left: 1.3em;
    display: grid;
    gap: 6px;
  }
  dl {
    display: grid;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }
  dl div {
    display: grid;
    grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.6fr);
    gap: 12px;
    padding: 9px 12px;
    border-top: 1px solid var(--border);
  }
  dl div:first-child {
    border-top: 0;
  }
  dt {
    font-weight: 600;
  }
  dd {
    margin: 0;
    color: var(--text-dim);
  }
  @media (max-width: 400px) {
    dl div {
      grid-template-columns: minmax(0, 1fr);
      gap: 2px;
    }
  }
  .note {
    padding: 10px 12px;
    border-left: 3px solid var(--accent);
    background: var(--accent-dim);
    border-radius: 0 var(--radius) var(--radius) 0;
  }
  .see {
    display: grid;
    gap: 4px;
    margin-top: 6px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .see .eyebrow {
    margin-bottom: 2px;
  }
</style>
