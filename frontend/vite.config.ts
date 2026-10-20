import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'


// https://vite.dev/config/
export default defineConfig({
  server: {
    proxy: {
      '/ws/transcribe': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
  plugins: [
    react(), 
    tailwindcss()
  ],
})
