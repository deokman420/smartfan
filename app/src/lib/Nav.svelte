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
    gap: 2px;
  }

  button {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    position: relative;
    background: transparent;
    border: 0;
    border-radius: 12px;
    padding: 8px 2px 7px;
    min-height: 52px;
    color: var(--text-dim);
    font-size: 0.68rem;
    font-weight: 500;
    letter-spacing: -0.01em;
    transition: background 0.18s, color 0.18s;
  }

  button:hover {
    color: var(--text);
  }

  button.active {
    color: var(--accent);
  }

  button.active::before {
    content: '';
    position: absolute;
    top: 0;
    left: 22%;
    right: 22%;
    height: 2px;
    border-radius: 2px;
    background: var(--accent);
  }

  .label {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ico {
    display: inline-flex;
  }
  .ico svg {
    width: 18px;
    height: 18px;
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
    top: 8px;
    right: calc(50% - 16px);
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--bad);
    box-shadow: 0 0 0 2px var(--bg-card);
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
      min-height: 42px;
      padding: 9px 12px;
      font-size: 0.92rem;
      text-align: left;
    }
    button:hover {
      background: var(--bg-card-hover);
    }
    button.active {
      background: var(--accent-dim);
    }
    button.active::before {
      top: 9px;
      bottom: 9px;
      left: 0;
      right: auto;
      width: 2px;
      height: auto;
    }
    .badge {
      top: 50%;
      right: 12px;
      transform: translateY(-50%);
      box-shadow: 0 0 0 2px var(--bg-card);
    }
  }
</style>
