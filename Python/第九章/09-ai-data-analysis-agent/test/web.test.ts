import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { Microphone } from '../web/src/recorder.js'
import { api, stream } from '../web/src/api.js'

test('普通 JSON 与文件请求保留字段，统一显示后端 message 错误', async () => {
  const original = globalThis.fetch, requests: RequestInit[] = []
  try {
    globalThis.fetch = async (_url, options) => { requests.push(options!); return Response.json({ ok: true }) }
    await api('/health')
    const form = new FormData(); form.append('file', new Blob(['样本']), 'sales.csv')
    await api('/uploads', { method: 'POST', body: form })
    assert.equal((requests[0].headers as any)['Content-Type'], 'application/json')
    assert.equal((requests[1].headers as any)['Content-Type'], undefined)
    globalThis.fetch = async () => Response.json({ message: '数据需要核对' }, { status: 400 })
    await assert.rejects(api('/datasets'), /数据需要核对/)
  } finally { globalThis.fetch = original }
})

test('NDJSON 跨 UTF-8 网络块解析进度与完整报告，末行没有换行也能处理', async () => {
  const original = globalThis.fetch, received: any[] = [], controller = new AbortController()
  const packets = [{ event: 'progress', data: { stage: 'query', message: '查询华东' } },
    { event: 'report', data: { answer: '咖啡机贡献80%。', rows: [{ region: '华东', amount: '20000.00' }] } }]
  const bytes = new TextEncoder().encode(packets.map(packet => JSON.stringify(packet)).join('\n'))
  try {
    globalThis.fetch = async (_url, options) => {
      assert.equal(options!.signal, controller.signal)
      assert.deepEqual(JSON.parse(String(options!.body)), { question: '问题', mode: 'replay' })
      return new Response(new ReadableStream({ start(stream) {
        for (let offset = 0; offset < bytes.length; offset += 2) stream.enqueue(bytes.slice(offset, offset + 2))
        stream.close()
      } }))
    }
    await stream('/analyze', { question: '问题', mode: 'replay' }, controller.signal,
      (event, data) => received.push({ event, data }))
    assert.deepEqual(received, packets)
  } finally { globalThis.fetch = original }
})

test('流式接口的 HTTP 错误不可冒充成功报告', async () => {
  const original = globalThis.fetch
  try {
    globalThis.fetch = async () => Response.json({ message: '问题过长' }, { status: 400 })
    await assert.rejects(stream('/analyze', {}, new AbortController().signal, () => assert.fail('不能回调结果')), /问题过长/)
  } finally { globalThis.fetch = original }
})

function pcm() {
  const messages: any[] = []
  let Processor: any
  class Worklet {
    port = { onmessage: undefined, postMessage: (message: any) => messages.push(message) }
  }
  const context = vm.createContext({ AudioWorkletProcessor: Worklet,
    registerProcessor: (name: string, Type: any) => { assert.equal(name, 'pcm-capture'); Processor = Type } })
  vm.runInContext(readFileSync(new URL('../web/public/pcm-worklet.js', import.meta.url), 'utf8'), context)
  return { processor: new Processor(), messages }
}

test('PCM 每 1600 采样发送一次，正确限幅、PCM16 小端和 RMS 音量', () => {
  const f = pcm()
  const values = [-2, -1, -0.5, 0, 0.5, 1, 2, ...Array(1593).fill(0)]
  assert.equal(f.processor.process([[values]]), true)
  assert.equal(f.messages.length, 1)
  const view = new DataView(f.messages[0].buffer)
  assert.equal(view.byteLength, 3200)
  assert.deepEqual(Array.from({ length: 7 }, (_, i) => view.getInt16(i * 2, true)), [-32768, -32768, -16384, 0, 16384, 32767, 32767])
  assert.equal(f.messages[0].level, Math.sqrt(4.5 / 1600))
})

test('停止录音先发送不足 100ms 的尾块，空尾块只确认 flush', () => {
  const f = pcm()
  f.processor.process([[[0.25, -0.25]]])
  assert.equal(f.messages.length, 0)
  f.processor.port.onmessage({ data: 'flush' })
  assert.equal(f.messages[0].buffer.byteLength, 4)
  assert.equal(f.messages[1].flushed, true)
  f.processor.port.onmessage({ data: 'flush' })
  assert.equal(f.messages.length, 3)
  assert.equal(f.messages[2].flushed, true)
})

/** 安装/恢复浏览器替身，测试不会取得真实设备权限。 */
function microphoneFixture(getUserMedia?: () => Promise<any>) {
  let trackStops = 0, portClosed = 0
  const modules: string[] = [], nodes: any[] = [], contexts: any[] = []
  const stream = { getTracks: () => [{ stop: () => trackStops++ }] }
  const graph = () => ({ connect: (target: any) => target, disconnect: () => {} })
  class Context {
    sampleRate = 16000
    state = 'running'
    destination = {}
    audioWorklet = { addModule: async (path: string) => { modules.push(path) } }
    constructor(public options: any) { contexts.push(this) }
    async resume() {}
    async close() { this.state = 'closed' }
    createMediaStreamSource() { return graph() }
    createGain() { return { ...graph(), gain: { value: 1 } } }
  }
  class Node {
    port = {
      onmessage: undefined as any,
      close: () => { portClosed++ },
      postMessage: (message: string) => {
        assert.equal(message, 'flush')
        this.port.onmessage({ data: { buffer: new Uint8Array([1, 2]).buffer, level: 0.2 } })
        this.port.onmessage({ data: { flushed: true } })
      },
    }
    constructor() { nodes.push(this) }
    connect(target: any) { return target }
    disconnect() {}
  }
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({ AudioContext: Context, AudioWorkletNode: Node,
    navigator: { mediaDevices: { getUserMedia: getUserMedia || (async () => stream) } } })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value })
  }
  return { stream, modules, nodes, contexts, trackStops: () => trackStops, portClosed: () => portClosed,
    restore() { for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    } } }
}

test('麦克风正常结束发送尾块后关闭，打断丢弃尾块，关闭后忽略迟到数据', async () => {
  const f = microphoneFixture(), chunks: string[] = []
  try {
    const microphone = new Microphone(chunk => chunks.push(chunk))
    await microphone.prepare()
    assert.equal(f.contexts[0].options.sampleRate, 16000)
    assert.deepEqual(f.modules, ['/pcm-worklet.js'])
    microphone.start()
    await microphone.stop()
    assert.deepEqual(chunks, ['AQI='])
    assert.equal(f.trackStops(), 1)
    assert.equal(f.portClosed(), 1)
    assert.equal(f.contexts[0].state, 'closed')
    f.nodes[0].port.onmessage({ data: { buffer: new Uint8Array([3, 4]).buffer, level: 1 } })
    assert.deepEqual(chunks, ['AQI='])
    const interrupted = new Microphone(chunk => chunks.push(chunk))
    await interrupted.prepare()
    interrupted.start()
    await interrupted.stop(true)
    assert.deepEqual(chunks, ['AQI='])
    assert.equal(f.trackStops(), 2)
  } finally { f.restore() }
})

test('申请麦克风权限期间打断，迟到的设备授权也立即关闭', async () => {
  let allow!: (stream: any) => void
  const f = microphoneFixture(() => new Promise(resolve => { allow = resolve }))
  try {
    const microphone = new Microphone(() => assert.fail('不应发送音频'))
    const preparing = microphone.prepare()
    while (!allow) await Promise.resolve()
    await microphone.stop(true)
    allow(f.stream)
    await preparing
    microphone.start()
    assert.equal(f.trackStops(), 1)
    assert.equal(f.nodes.length, 0)
    assert.equal(f.contexts[0].state, 'closed')
  } finally { f.restore() }
})
