<script>
  /**
   * Section navigation: a sticky tab strip on phones, a sidebar on desktop.
   *
   * Deliberately not a hamburger. A hamburger is the right answer when
   * there are many destinations or the nav is secondary to the content;
   * here there are four, they are the whole app, and the thing is operated
   * one-handed in a steamy room. Hiding four buttons behind a tap costs an
   * interaction every time and hides the fault badge, which is the one
   * thing you want to see without looking for it.
   */
  import { ICONS } from '$lib/icons.js';

  let { items, current, onSelect, alert = null } = $props();
</script>

<nav aria-label="Sections">
  {#each items as item (item.id)}
    <button
      class:active={current === item.id}
      aria-current={current === item.id ? 'page' : undefined}
      onclick={() => onSelect(item.id)}
    >
      <span class="ico" aria-hidden="true">
        <svg viewBox="0 0 16 16">{@html ICONS[item.icon]}</svg>
      </span>
      <span class="label">{item.label}</span>
      {#if alert === item.id}
        <span class="badge" aria-label="needs attention"></span>
      {/if}
    </button>
  {/each}
</nav>

<style>
  nav {
    display: flex;
    gap: 4px;
  }

  button {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    position: relative;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 9px 4px;
    color: var(--text-dim);
    font-size: 0.72rem;
    font-weight: 500;
    transition: background 0.18s, color 0.18s, border-color 0.18s;
  }

  button:hover {
    color: var(--text);
    background: var(--bg-card);
  }

  button.active {
    color: var(--accent);
    background: var(--accent-dim);
    border-color: var(--accent-soft);
  }

  .ico {
    display: inline-flex;
  }
  .ico svg {
    width: 17px;
    height: 17px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* A fault must be visible from any section, or you only find it by
     going looking for it. */
  .badge {
    position: absolute;
    top: 6px;
    right: calc(50% - 20px);
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--bad);
    box-shadow: 0 0 0 2px var(--bg);
  }

  @media (min-width: 960px) {
    nav {
      flex-direction: column;
      gap: 2px;
    }
    button {
      flex-direction: row;
      justify-content: flex-start;
      gap: 11px;
      padding: 10px 13px;
      font-size: 0.88rem;
      text-align: left;
    }
    .badge {
      top: 50%;
      right: 13px;
      transform: translateY(-50%);
    }
  }
</style>
