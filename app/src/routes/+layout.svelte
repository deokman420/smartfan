<script>
  import '../app.css';
  import { onMount, onDestroy } from 'svelte';
  import { connect, disconnect, configured, fanRunning } from '$lib/link.js';
  import { watchAppIcon } from '$lib/appicon.js';
  import { startTheme } from '$lib/theme.js';
  import { get } from 'svelte/store';

  let { children } = $props();

  /** Teardown for the tab icon and status-bar tint; see lib/appicon.js. */
  let releaseAppIcon = () => {};
  /** Teardown for the light/dark resolver; see lib/theme.js. */
  let releaseTheme = () => {};

  onMount(() => {
    // Before the icon: appicon.js reads --bar out of whichever scheme is up,
    // and app.html has already set the attribute, so this only takes over
    // the following of it.
    releaseTheme = startTheme();

    // Browser-only, and it touches <head> tags that app.html owns, so it
    // starts here rather than at module scope.
    releaseAppIcon = watchAppIcon(fanRunning);

    // Only dial out once there is something to dial with. Connecting with
    // no credentials against a broker that requires them produces an
    // authentication error as the first thing a new user ever sees, which
    // reads as "broken" rather than "not set up yet".
    //
    // Served by the fan itself, `configured` is true on the first load --
    // the address is the one in the address bar and there is nothing to
    // ask -- so this connects immediately and the app just works.
    if (get(configured)) connect();
  });

  onDestroy(() => {
    releaseAppIcon();
    releaseTheme();
    // Only meaningful in the dev server's hot reload, but leaving sockets
    // behind there produces duplicate-client-id disconnect storms that look
    // exactly like a broker fault.
    disconnect();
  });
</script>

{@render children()}
