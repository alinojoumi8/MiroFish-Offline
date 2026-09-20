// @vitest-environment node

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

import config from '../vite.config.js'


const frontendPackage = JSON.parse(
  readFileSync(resolve(process.cwd(), 'package.json'), 'utf8')
)
const composeSource = readFileSync(resolve(process.cwd(), '../docker-compose.yml'), 'utf8')

describe('Vite bind policy', () => {
  test('manual development binds the trusted proxy to loopback only', () => {
    expect(frontendPackage.scripts.dev).toBe('vite')
    expect(config.server.host).toBe('127.0.0.1')
  })

  test('Compose explicitly opts into a container-internal UI bind', () => {
    expect(composeSource).toContain('MIROFISH_UI_HOST: 0.0.0.0')
    expect(composeSource).toContain('127.0.0.1:3000:3000')
  })
})
