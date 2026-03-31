import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { readFileSync } from 'fs';

const packageJson = JSON.parse(readFileSync('./package.json', 'utf8'));

export default defineConfig({
  plugins: [react()],
  root: 'src/renderer',
  base: './',
  publicDir: 'public',
  define: {
    __APP_VERSION__: JSON.stringify(packageJson.version),
  },
  build: {
    outDir: '../../dist/renderer',
    emptyOutDir: true,
    chunkSizeWarningLimit: 600, // Three.js (525 kB) is one irreducible library, already lazy-loaded
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-three': ['three'],
          'vendor-markdown': ['react-markdown', 'remark-gfm'],
          'vendor-react': ['react', 'react-dom'],
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5199, // Unique port to avoid collision with other Vite projects
    strictPort: true,
  },
});
