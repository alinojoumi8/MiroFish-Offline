// @vitest-environment node

import { describe, expect, test } from 'vitest'

import config from '../vite.config.js'


const proxy = config.server.proxy['/api']

const responseRecorder = () => {
  const result = { statusCode: 200, body: undefined, writableEnded: false }
  result.end = body => {
    result.body = body
    result.writableEnded = true
  }
  return result
}

describe('Vite API proxy origin guard', () => {
  test('allows a same-origin browser mutation to reach the token-injecting proxy', () => {
    expect(proxy.bypass).toBeTypeOf('function')
    if (!proxy.bypass) return

    const response = responseRecorder()
    const result = proxy.bypass({
      method: 'POST',
      url: '/api/graph/build',
      headers: { host: 'localhost:3000', origin: 'http://localhost:3000' },
      socket: { encrypted: false }
    }, response)

    expect(result).toBeUndefined()
    expect(response.body).toBeUndefined()
  })

  test('rejects a hostile browser origin before the proxy can inject its token', () => {
    expect(proxy.bypass).toBeTypeOf('function')
    if (!proxy.bypass) return

    const response = responseRecorder()
    const result = proxy.bypass({
      method: 'DELETE',
      url: '/api/projects/proj_0123456789ab',
      headers: { host: 'localhost:3000', origin: 'https://hostile.example' },
      socket: { encrypted: false }
    }, response)

    expect(result).toBe('/api/projects/proj_0123456789ab')
    expect(response.statusCode).toBe(403)
    expect(response.body).toBe('Forbidden')
    expect(response.writableEnded).toBe(true)
  })

  test('keeps OPTIONS and safe methods usable', () => {
    expect(proxy.bypass).toBeTypeOf('function')
    if (!proxy.bypass) return

    for (const method of ['GET', 'HEAD', 'OPTIONS']) {
      const response = responseRecorder()
      const result = proxy.bypass({
        method,
        url: '/api/health',
        headers: { host: 'localhost:3000', origin: 'https://hostile.example' },
        socket: { encrypted: false }
      }, response)
      expect(result).toBeUndefined()
      expect(response.body).toBeUndefined()
    }
  })
})
