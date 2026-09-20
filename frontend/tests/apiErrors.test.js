import { describe, expect, test, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import service from '../src/api/index'


describe('API error extraction', () => {
  test('uses same-origin API requests by default', () => {
    expect(service.defaults.baseURL).toBe('')
  })

  test('Vite proxy injects the server-side control token', () => {
    const source = readFileSync(resolve(process.cwd(), 'vite.config.js'), 'utf8')

    expect(source).toContain("process.env.MIROFISH_CONTROL_TOKEN")
    expect(source).toContain("'X-MiroFish-Control-Token'")
    expect(source).not.toContain('VITE_MIROFISH_CONTROL_TOKEN')
  })

  test('uses the stable error envelope message instead of stringifying the object', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const request = service.get('/test-error', {
      adapter: async config => ({
        data: {
          success: false,
          error: {
            code: 'bad_request',
            message: 'Useful validation message',
            request_id: 'request-123'
          }
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config
      })
    })

    await expect(request).rejects.toThrow('Useful validation message')
  })

  test('extracts the stable message from non-2xx Axios responses', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const request = service.get('/test-http-error', {
      adapter: async () => Promise.reject({
        message: 'Request failed with status code 400',
        response: {
          data: {
            success: false,
            error: {
              code: 'bad_request',
              message: 'Actionable HTTP error',
              request_id: 'request-456'
            }
          }
        }
      })
    })

    await expect(request).rejects.toThrow('Actionable HTTP error')
  })
})
