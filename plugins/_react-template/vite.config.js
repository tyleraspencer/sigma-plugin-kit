import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],

  // RELATIVE asset paths. The default `base: '/'` emits `/assets/index-x.js`,
  // which 404s once the plugin is served from a subpath like
  // `https://<user>.github.io/sigma-plugins/plugins/<name>/` -- the page loads,
  // the bundle doesn't, and Sigma shows a blank iframe with no error. './'
  // makes the build work at any depth.
  base: './',

  // Vite's default port, and the default `devUrl` Sigma registers. Keep them
  // aligned so "Point to Development URL" works with no extra configuration.
  server: { port: 5173 },

  build: { outDir: 'dist', emptyOutDir: true },
});
