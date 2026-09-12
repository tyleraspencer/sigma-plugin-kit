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

  // Hashed asset names are a liability here, not an asset. GitHub Pages serves
  // index.html with `Cache-Control: max-age=600`, and every deploy REPLACES
  // dist/assets wholesale -- so for up to ten minutes a browser holding the
  // previous index.html asks for a hashed bundle that has just been deleted,
  // gets a 404, and never mounts. The plugin renders as an empty iframe in its
  // own background colour and Sigma's loading bar spins forever. Stable names
  // make the worst case "a ten-minute-old build that works" instead.
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
    },
  },
});
