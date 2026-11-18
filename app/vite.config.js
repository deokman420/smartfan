import { sveltekit } from '@sveltejs/kit/vite';

export default {
  plugins: [sveltekit()],
  server: {
    host: true,
    port: 5173,
    // app.css imports ../../design-tokens/tokens.css, which is above the
    // Vite root, so that one folder is allowed -- and only that one. This
    // used to allow '..', the whole repo root, and with `host: true` the
    // dev server then handed secrets.py, ota.key and the rest to anyone on
    // the LAN who asked through /@fs (audit NV-9; Vite's default deny list
    // covers .env and certificates, not those). '.' is the app itself,
    // which listing fs.allow at all stops being allowed by default.
    fs: { allow: ['.', '../design-tokens'] }
  }
};
