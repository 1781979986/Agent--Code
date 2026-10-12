import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildEnvironment } from '../scripts/build-sandbox.mjs'

const systemProxy = `<dictionary> {
  HTTPEnable : 1
  HTTPProxy : 127.0.0.1
  HTTPPort : 17890
  HTTPSEnable : 1
  HTTPSProxy : 127.0.0.1
  HTTPSPort : 17890
}`

test('将启用的 macOS 代理传给构建子进程，不修改调用者环境', () => {
  const env = { PATH: '/usr/bin' }
  assert.deepEqual(buildEnvironment(env, 'darwin', () => systemProxy), {
    PATH: '/usr/bin', HTTP_PROXY: 'http://127.0.0.1:17890', HTTPS_PROXY: 'http://127.0.0.1:17890'
  })
  assert.deepEqual(env, { PATH: '/usr/bin' })
})

test('终端明确设置的代理和绕过规则优先', () => {
  const env = { http_proxy: 'http://localhost:8888', HTTPS_PROXY: 'http://localhost:8889', NO_PROXY: 'localhost' }
  assert.deepEqual(buildEnvironment(env, 'darwin', () => { throw new Error('不应读取系统设置') }), env)
})

test('代理关闭或运行在其他系统时，不自动指定代理', () => {
  assert.deepEqual(buildEnvironment({}, 'darwin', () => systemProxy.replaceAll('Enable : 1', 'Enable : 0')), {})
  assert.deepEqual(buildEnvironment({}, 'linux', () => { throw new Error('不应调用 scutil') }), {})
})
