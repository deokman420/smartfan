import adapter from '@sveltejs/adapter-static';

// Static, with a single-page fallback. There is no server: the app is a
// bundle of files talking straight to the MQTT broker, which is what makes
// "local first" literal rather than a slogan.
export default {
  kit: {
    adapter: adapter({ fallback: 'index.html', precompress: false }),
    paths: { relative: true }
  }
};
