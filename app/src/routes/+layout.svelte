<script>
  import '../app.css';
  import { onMount, onDestroy } from 'svelte';
  import { connect, disconnect } from '$lib/broker.js';

  let { children } = $props();

  onMount(() => {
    connect();
  });

  onDestroy(() => {
    // Only meaningful in the dev server's hot reload, but leaving sockets
    // behind there produces duplicate-client-id disconnect storms that look
    // exactly like a broker fault.
    disconnect();
  });
</script>

{@render children()}
