import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const controlToken = process.env.MIROFISH_CONTROL_TOKEN
const proxyHeaders = controlToken
  ? { 'X-MiroFish-Control-Token': controlToken }
  : {}
const mutationMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const trustedOrigins = new Set(
  (process.env.MIROFISH_ALLOWED_ORIGINS || '')
    .split(',')
    .map(origin => origin.trim())
    .filter(Boolean)
)

const guardProxyOrigin = (request, response) => {
  if (!mutationMethods.has(request.method?.toUpperCase()) || !request.url?.startsWith('/api')) {
    return undefined
  }

  const origin = request.headers.origin
  if (!origin) return undefined

  const scheme = request.socket?.encrypted ? 'https' : 'http'
  const requestOrigin = `${scheme}://${request.headers.host}`
  let normalizedOrigin
  try {
    normalizedOrigin = new URL(origin).origin
  } catch {
    normalizedOrigin = null
  }

  if (normalizedOrigin === requestOrigin || trustedOrigins.has(normalizedOrigin)) {
    return undefined
  }

  response.statusCode = 403
  response.setHeader?.('Content-Type', 'text/plain; charset=utf-8')
  response.end('Forbidden')
  return request.url
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: process.env.MIROFISH_UI_HOST || '127.0.0.1',
    port: 3000,
    open: true,
    proxy: {
      '/api': {
        target: process.env.MIROFISH_BACKEND_URL || 'http://127.0.0.1:5001',
        changeOrigin: true,
        secure: false,
        headers: proxyHeaders,
        bypass: guardProxyOrigin
      }
    }
  }
})
