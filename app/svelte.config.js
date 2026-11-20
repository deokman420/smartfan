import adapter from '@sveltejs/adapter-static';

// Static, with a single-page fallback. There is no server: the app is a
// bundle of files talking straight to the MQTT broker, which is what makes
// "local first" literal rather than a slogan.
export default {
  kit: {
    // SMARTFAN_BUILD_OUT is set by scripts/build-demo.mjs, so the public
    // demo never lands in build/, which is what goes onto the fan.
    adapter: adapter({
      pages: process.env.SMARTFAN_BUILD_OUT || 'build',
      assets: process.env.SMARTFAN_BUILD_OUT || 'build',
      // The demo is served under /demo/, and a fallback page always uses
      // root-absolute paths. It has one route, so the prerendered page (with
      // relative paths) is all it needs.
      fallback: process.env.SMARTFAN_BUILD_OUT ? undefined : 'index.html',
      precompress: false
    }),
    paths: { relative: true }
  }
};
