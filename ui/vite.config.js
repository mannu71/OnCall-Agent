import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { visualizer } from 'rollup-plugin-visualizer'

export default defineConfig(({ mode }) => {
  // Single source of truth for the dev proxy target: reuse VITE_AGENT_API_URL
  // from ui/.env so it can't drift from the client's own base-URL resolution.
  const env = loadEnv(mode, __dirname, '')
  const apiTarget = env.VITE_AGENT_API_URL || env.VITE_API_URL || 'http://localhost:48000'

  return {
  base: './',
  plugins: [
    react(),
    mode === 'analyze' && visualizer({ open: false, filename: 'dist/stats.html', gzipSize: true }),
  ].filter(Boolean),
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Uncommon default so it doesn't clash with other Vite apps; PORT still wins,
    // and strictPort stays off so Vite auto-bumps if 45173 is taken.
    port: process.env.PORT ? parseInt(process.env.PORT) : 45173,
    strictPort: false,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'esnext',
    sourcemap: false,
    chunkSizeWarningLimit: 500,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
    },
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'reactflow': ['reactflow'],
          'markdown': ['react-markdown', 'remark-gfm'],
          // Radix primitives are ~300-500KB combined; split out of the main bundle.
          'radix': [
            '@radix-ui/react-collapsible',
            '@radix-ui/react-dialog',
            '@radix-ui/react-dropdown-menu',
            '@radix-ui/react-icons',
            '@radix-ui/react-label',
            '@radix-ui/react-select',
            '@radix-ui/react-separator',
            '@radix-ui/react-slot',
            '@radix-ui/react-tooltip',
            '@radix-ui/react-checkbox',
          ],
          'tanstack': ['@tanstack/react-query'],
          // three.js + r3f only load when the codegraph 3D graph view is opened,
          // but keep them off the main bundle regardless (~600KB+ combined).
          'three-vendor': [
            'three',
            '@react-three/fiber',
            '@react-three/drei',
            '@react-three/postprocessing',
            'postprocessing',
          ],
        },
      },
    },
  },
  optimizeDeps: {
    include: ['react', 'react-dom', 'react-router-dom', 'reactflow'],
  },
  }
})
