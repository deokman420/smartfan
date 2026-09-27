// No server, ever. The page is prerendered to static files and everything
// it shows arrives over MQTT in the browser, so server rendering would only
// produce a frame with no data in it.
export const prerender = true;
export const ssr = false;
