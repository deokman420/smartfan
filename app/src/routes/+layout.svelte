<script>
  import '../app.css';
  import { onMount, onDestroy } from 'svelte';
  import { connect, disconnect, configured } from '$lib/broker.js';
  import { get } from 'svelte/store';

  let { children } = $props();

  onMount(() => {
    // Only dial out once there is something to dial with. Connecting with
    // no credentials against a broker that requires them produces an
    // authentication error as the first thing a new user ever sees, which
    // reads as "broken" rather than "not set up yet".
    if (get(configured)) connect();
  });

  onDestroy(() => {
    // Only meaningful in the dev server's hot reload, but leaving sockets
    // behind there produces duplicate-client-id disconnect storms that look
    // exactly like a broker fault.
    disconnect();
  });
</script>

{@render children()}
