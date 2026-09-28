import { sveltekit } from '@sveltejs/kit/vite';

export default {
  plugins: [sveltekit()],
  server: {
    host: true,
    port: 5173,
    // app.css imports ../../design-tokens/tokens.css, which is above the
    // Vite root. The dev server refuses to serve outside the root unless
    // the path is allowed.
    fs: { allow: ['..', '../design-tokens'] }
  }
};
