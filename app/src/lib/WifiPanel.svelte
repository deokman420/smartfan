<script>
  import { onDestroy } from 'svelte';
  import { wifiApi, mode } from '$lib/link.js';

  /** Only polls while the sheet that contains it is actually open. */
  let { open = false, holdS = 8 } = $props();

  let api = $derived($mode === 'device' ? wifiApi() : null);

  let status = $state(null);
  let networks = $state(null);
  let scanning = $state(false);
  let ssid = $state('');
  let psk = $state('');
  let sending = $state(false);
  let outcome = $state(''); // '' | 'trying' | 'ok' | 'failed' | 'gone'
  let note = $state('');
  let timer = null;

  /* Poll while the sheet is open and only then. The fan answers this from
     the same loop that runs the fan, and there is no reason to ask it every
     two seconds about a door nobody is standing at. */
  $effect(() => {
    stop();
    if (open && api) {
      refresh();
      timer = setInterval(refresh, 2000);
    }
    return stop;
  });

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }
  onDestroy(stop);

  async function refresh() {
    if (!api) return;
    try {
      const s = await api.status();
      if (!s) return;
      status = s;
      // The device answering at all means we are still on the same network
      // as it, so it did not move. Its own state field says whether that was
      // a rollback or a change that never started.
      if (outcome === 'trying' && s.state === 'ok') {
        outcome = 'ok';
        note = `The fan is on ${s.ssid}.`;
      } else if (outcome === 'trying' && s.state === 'failed') {
        outcome = 'failed';
        note = s.error || 'It could not join that network.';
      }
    } catch {
      // Unreachable mid-attempt is the EXPECTED shape of success: the fan has
      // moved to the other network and this phone has not. Reporting a
      // failure here would be exactly backwards.
      if (outcome === 'trying') {
        outcome = 'gone';
        note = '';
      }
    }
  }

  async function scan() {
    if (!api) return;
    scanning = true;
    try {
      const r = await api.scan();
      networks = r?.ok ? r.networks : [];
    } catch {
      networks = [];
    } finally {
      scanning = false;
    }
  }

  async function connect() {
    if (!api || !ssid) return;
    sending = true;
    note = '';
    try {
      const r = await api.connect(ssid, psk);
      if (r.ok) {
        outcome = 'trying';
      } else {
        outcome = 'failed';
        note = r.reason || `The fan refused that (${r.status}).`;
      }
    } catch {
      outcome = 'failed';
      note = 'The fan did not answer.';
    } finally {
      sending = false;
      psk = '';
    }
  }

  /** Four rising bars, from the dBm the device reported. */
  const bars = (rssi) => (rssi >= -55 ? 4 : rssi >= -67 ? 3 : rssi >= -78 ? 2 : 1);
</script>

<h3 class="section">Network</h3>

{#if !api}
  <p class="sub">
    Only over the fan's own address. A broker reached across the network the fan
    is about to leave is not the way to change that network — switch to
    <strong>The fan itself</strong> above.
  </p>
{:else if outcome === 'gone'}
  <!-- The good ending, and the one that looks like a failure unless it is
       said out loud. The fan is on the other network now; this phone is not,
       so it cannot confirm anything at all. -->
  <p class="sub">
    The fan has left this network, which is what success looks like from here.
    Join <strong>{ssid}</strong> on this phone and open
    <code>http://fan.local/</code> again.
  </p>
  <p class="sub">
    If it is not there, it could not join either and has gone back to its old
    network — nothing was written, so it is still where it was.
  </p>
{:else}
  <p class="sub">Currently on <strong>{status?.ssid || '—'}</strong>.</p>

  {#if !status?.open}
    <div class="locked">
      <p class="sub">
        Changing this needs you at the fan. Hold its button for
        <strong>{holdS} seconds</strong> — both lights flash together once you
        are past it — then come back here.
      </p>
      <p class="sub quiet">
        There is no password on this API, so the button is what stands between a
        guest on your wifi and a fan they can take off it.
      </p>
    </div>
  {:else}
    <p class="open-for">Unlocked — {status.secs_left}s left</p>

    <div class="pickrow">
      <label for="wifi-ssid">Network</label>
      <button class="scan" onclick={scan} disabled={scanning}>
        {scanning ? 'Scanning…' : 'Scan'}
      </button>
    </div>
    <input
      id="wifi-ssid"
      bind:value={ssid}
      placeholder="Network name"
      autocapitalize="off"
      autocorrect="off"
      spellcheck="false"
    />

    {#if networks}
      {#if networks.length}
        <div class="nets">
          {#each networks as n (n.ssid)}
            <button class="net" class:active={ssid === n.ssid} onclick={() => (ssid = n.ssid)}>
              <span class="net-name">{n.ssid}</span>
              <span class="net-meta">
                {n.secure ? '' : 'open · '}{n.rssi} dBm
                <i class="bars" data-bars={bars(n.rssi)} aria-hidden="true"></i>
              </span>
            </button>
          {/each}
        </div>
      {:else}
        <p class="sub">Nothing came back. The scan runs on the fan's own radio.</p>
      {/if}
    {/if}

    <label for="wifi-psk">Password</label>
    <input
      id="wifi-psk"
      type="password"
      bind:value={psk}
      placeholder="Leave empty for an open network"
    />

    <p class="sub">
      The fan joins this network before it writes anything down. If it cannot, it
      goes back to {status.ssid} and nothing is saved — so a typo costs a retry,
      not a ladder.
    </p>

    <div class="sheet-actions">
      <button
        class="primary"
        onclick={connect}
        disabled={!ssid || sending || outcome === 'trying'}
      >
        {outcome === 'trying' ? 'Trying…' : sending ? 'Sending…' : 'Join this network'}
      </button>
    </div>
  {/if}

  {#if outcome === 'trying'}
    <p class="sub tone-active">Trying {status?.trying || ssid}…</p>
  {:else if outcome === 'ok'}
    <p class="sub tone-ok">{note}</p>
  {:else if outcome === 'failed'}
    <p class="sub tone-bad">{note}</p>
  {/if}
{/if}

<style>
  .sub {
    margin: 4px 0 0;
    font-size: 0.82rem;
    color: var(--text-dim);
    line-height: 1.5;
  }
  .sub.quiet {
    font-size: 0.76rem;
  }
  .locked {
    margin-top: 8px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-card-2);
  }
  .open-for {
    margin: 8px 0 0;
    font-family: var(--mono);
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .pickrow {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }
  .scan {
    background: none;
    border: 0;
    padding: 0;
    font-size: 0.78rem;
    color: var(--accent);
  }
  .scan:disabled {
    color: var(--text-dim);
  }
  .nets {
    display: grid;
    gap: 4px;
    margin-top: 8px;
    /* Tall lists happen in flats. Scroll the list, not the sheet. */
    max-height: 190px;
    overflow-y: auto;
  }
  .net {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    text-align: left;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--bg-card-2);
    color: var(--text);
  }
  .net.active {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .net-name {
    font-size: 0.88rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .net-meta {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex: none;
    font-family: var(--mono);
    font-size: 0.66rem;
    color: var(--text-dim);
  }
  /* Four rising bars, drawn from the tokens rather than an icon font, and
     filled by however many the dBm earns. */
  .bars {
    display: inline-block;
    width: 14px;
    height: 10px;
    background-repeat: no-repeat;
    background-position: 0 100%, 4px 100%, 8px 100%, 12px 100%;
    background-size: 2px 3px, 2px 5px, 2px 8px, 2px 10px;
    background-image: linear-gradient(var(--text-muted) 0 0),
      linear-gradient(var(--text-muted) 0 0), linear-gradient(var(--text-muted) 0 0),
      linear-gradient(var(--text-muted) 0 0);
  }
  .bars[data-bars='1'] {
    background-image: linear-gradient(var(--accent) 0 0),
      linear-gradient(var(--text-muted) 0 0), linear-gradient(var(--text-muted) 0 0),
      linear-gradient(var(--text-muted) 0 0);
  }
  .bars[data-bars='2'] {
    background-image: linear-gradient(var(--accent) 0 0), linear-gradient(var(--accent) 0 0),
      linear-gradient(var(--text-muted) 0 0), linear-gradient(var(--text-muted) 0 0);
  }
  .bars[data-bars='3'] {
    background-image: linear-gradient(var(--accent) 0 0), linear-gradient(var(--accent) 0 0),
      linear-gradient(var(--accent) 0 0), linear-gradient(var(--text-muted) 0 0);
  }
  .bars[data-bars='4'] {
    background-image: linear-gradient(var(--accent) 0 0), linear-gradient(var(--accent) 0 0),
      linear-gradient(var(--accent) 0 0), linear-gradient(var(--accent) 0 0);
  }
</style>
