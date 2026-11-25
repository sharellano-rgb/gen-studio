import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({
  plugins: [tailwindcss()],
  build: {rollupOptions: {input: {main:'index.html',guidedPreview:'guided-preview.html'}}},
  server: {proxy: {'/api': {target: 'http://127.0.0.1:8790', changeOrigin: true}}},
});
