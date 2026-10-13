import 'reflect-metadata'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { test } from 'node:test'
import { Module } from '@nestjs/common'
import { NestFactory } from '@nestjs/core'
import type { ChatCompletionMessage } from 'openai/resources/chat/completions'
import { AgentService, executeOrderTool } from '../src/agent.service.js'
import { EvaluationService, buildJudgeInput, checkRules, parseJudge } from '../src/evaluation.service.js'
import { ModelService } from '../src/model.service.js'
import { loadCase } from '../src/sample.js'
import { teachingExamples } from '../src/examples.js'

const { sample, fixture } = loadCase()
const examples = teachingExamples(sample, fixture.orders)
const good = examples.find((item) => item.id === 'correct-paraphrase')!.run
type Request = Parameters<ModelService['complete']>[1]

function message(content: string | null, tool_calls?: ChatCompletionMessage['tool_calls']): ChatCompletionMessage {
  return { role: 'assistant', content, refusal: null, ...(tool_calls ? { tool_calls } : {}) }
}

class FakeModel extends ModelService {
  calls: Array<{ role: string; request: Request }> = []
  constructor(private readonly reply: (role: string, request: Request) => ChatCompletionMessage) { super() }
  override async complete(role: 'agent' | 'judge', request: Request) {
    this.calls.push({ role, request: structuredClone(request) })
    return this.reply(role, request)
  }
}

test('复用 02 中的题目、身份和参考结果', () => {
  assert.equal(sample.id, 'order-amount')
  assert.equal(sample.referenceOutputs.turns[0].facts.paidAmountYuan, 3500)
  assert.equal(fixture.orders.find((item) => item.orderId === 'A1025')!.paidAmountYuan, 800)
})

test('正常工具记录通过规则，同义改写不受字符串匹配限制', () => {
  assert.ok(checkRules(sample, good).every((item) => item.passed))
})

test('规则能识别错订单、未调用工具、空回答、错误金额和错误归属', () => {
  const wrong = examples.find((item) => item.id === 'wrong-order')!.run
  assert.equal(checkRules(sample, wrong)[0].passed, false)
  assert.equal(checkRules(sample, { ...good, toolCalls: [] })[0].passed, false)
  assert.equal(checkRules(sample, { ...good, answer: '  ' })[2].passed, false)
  for (const change of [
    { paidAmountYuan: 1 }, { userId: 'other-user' }, { tenantId: 'other-tenant' }, { orderId: 'A1025' }
  ]) {
    const run = structuredClone(good)
    Object.assign(run.toolCalls[0].result.order!, change)
    assert.equal(checkRules(sample, run)[1].passed, false)
  }
})

test('先查错再查对也能暴露问题，但允许重复查询同一个目标订单', () => {
  const wrong = examples[0].run.toolCalls[0]
  assert.equal(checkRules(sample, { ...good, toolCalls: [wrong, ...good.toolCalls] })[0].passed, false)
  assert.ok(checkRules(sample, { ...good, toolCalls: [...good.toolCalls, ...good.toolCalls] })
    .every((rule) => rule.passed))
})

test('答案包含金额和退款承诺时，工具规则仍通过，等待内容评分', () => {
  const promise = examples.find((item) => item.id === 'unsupported-promise')!.run
  assert.ok(checkRules(sample, promise).every((item) => item.passed))
})

test('工具身份由应用控制，拒绝越权参数和未知工具', () => {
  const context = sample.inputs.context
  assert.equal(executeOrderTool('delete_order', { orderId: 'A1024' }, context, fixture.orders).result.found, false)
  assert.equal(executeOrderTool('get_order', { orderId: 'A1024', userId: 'other' }, context, fixture.orders)
    .result.found, false)
  assert.equal(executeOrderTool('get_order', { orderId: 'A1024' }, { ...context, userId: 'other' }, fixture.orders)
    .result.found, false)
  const record = executeOrderTool('get_order', { orderId: 'A1024' }, context, fixture.orders)
  record.result.order!.paidAmountYuan = 1
  assert.equal(fixture.orders[0].paidAmountYuan, 3500)
})

test('规则不通过直接失败，不调用 Judge，也不会被模型高分抵消', async () => {
  const model = new FakeModel(() => { throw new Error('不应调用模型') })
  const result = await new EvaluationService(model).evaluate(sample, examples[0].run)
  assert.equal(result.status, 'failed')
  assert.equal(result.judge.status, 'skipped')
  assert.equal(model.calls.length, 0)
})

test('2 分通过，0/1 分失败，null 分数交给人工核对', async () => {
  for (const [score, status] of [[2, 'passed'], [1, 'failed'], [0, 'failed'], [null, 'needs_review']] as const) {
    const model = new FakeModel(() => message(JSON.stringify({ score, reason: '测试评分', quotes: [good.answer] })))
    const result = await new EvaluationService(model).evaluate(sample, good)
    assert.equal(result.status, status)
    assert.equal(model.calls[0].role, 'judge')
    assert.deepEqual(model.calls[0].request.response_format, { type: 'json_object' })
    assert.equal(model.calls[0].request.tools, undefined)
  }
})

test('评分异常单独记录，不能当作 Agent 零分', async () => {
  for (const value of ['', 'not json', '{"score":100}', JSON.stringify({
    score: 2, reason: '引用错误', quotes: ['这段文字不在回答中']
  })]) {
    const result = await new EvaluationService(new FakeModel(() => message(value))).evaluate(sample, good)
    assert.equal(result.status, 'evaluation_error')
    assert.equal(result.judge.status, 'error')
  }
  const result = await new EvaluationService(new FakeModel(() => { throw new Error('网络超时') }))
    .evaluate(sample, good)
  assert.equal(result.status, 'evaluation_error')
})

test('Agent 执行异常与评分异常分开', async () => {
  const model = new FakeModel(() => { throw new Error('服务不可用') })
  const run = await new AgentService(model).run(sample.inputs, fixture.orders)
  assert.equal(run.status, 'error')
  assert.equal((await new EvaluationService(model).evaluate(sample, run)).status, 'agent_error')
  assert.equal(model.calls.length, 1)
})

test('Judge 接收问题、参考、工具结果和答案，不接收人工预期分数', () => {
  const input = buildJudgeInput(sample, good)
  assert.deepEqual(Object.keys(input), ['question', 'reference', 'toolResults', 'answer'])
  assert.equal(JSON.stringify(input).includes('expectedScore'), false)
  assert.throws(() => parseJudge('{"score":2,"reason":"正确","quotes":[]}', good.answer))
})

test('Agent 先调用工具，再根据实际 Tool Message 回答；参考答案不在第一次模型请求中', async () => {
  const model = new FakeModel((_role, request) => request.messages.some((item) => item.role === 'tool')
    ? message('订单 A1024 的实付金额为 3500 元。')
    : message(null, [{ id: 'call-order', type: 'function', function: {
      name: 'get_order', arguments: '{"orderId":"A1024"}'
    } }]))
  const run = await new AgentService(model).run(structuredClone(sample.inputs), structuredClone(fixture.orders))
  assert.equal(run.status, 'completed')
  assert.equal(run.toolCalls[0].result.order?.paidAmountYuan, 3500)
  assert.equal(model.calls.length, 2)
  const firstRequest = JSON.stringify(model.calls[0].request)
  assert.equal(firstRequest.includes('3500'), false)
  assert.equal(firstRequest.includes('referenceOutputs'), false)
  const tool = model.calls[1].request.messages.find((item) => item.role === 'tool')!
  assert.equal(tool.tool_call_id, 'call-order')
  assert.equal(JSON.parse(tool.content as string).order.paidAmountYuan, 3500)
})

test('Agent 遇到非法参数会把错误回传模型；循环不会无限执行', async () => {
  const model = new FakeModel(() => message(null, [{ id: 'bad', type: 'function', function: {
    name: 'get_order', arguments: 'not-json'
  } }]))
  const run = await new AgentService(model).run(sample.inputs, fixture.orders)
  assert.equal(run.status, 'error')
  assert.equal(model.calls.length, 4)
  assert.match(run.toolCalls[0].result.error!, /参数/)
})

test('NestJS + OpenAI SDK 本地 HTTP 联调，不访问真实模型接口', async () => {
  const requests: Array<Record<string, any>> = []
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(Buffer.from(chunk))
    const body = JSON.parse(Buffer.concat(chunks).toString())
    requests.push(body)
    const judge = body.response_format?.type === 'json_object'
    const hasTool = body.messages.some((item: { role: string }) => item.role === 'tool')
    const response = judge
      ? message('{"score":2,"reason":"测试接口返回的评分","quotes":["3500 元"]}')
      : hasTool ? message('订单 A1024 的实付金额为 3500 元。')
        : message(null, [{ id: 'call-http', type: 'function', function: {
          name: 'get_order', arguments: '{"orderId":"A1024"}'
        } }])
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({
      id: 'mock-completion', object: 'chat.completion', created: 1, model: 'local-test',
      choices: [{ index: 0, message: response, finish_reason: response.tool_calls ? 'tool_calls' : 'stop' }]
    }))
  })
  const keys = ['DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'JUDGE_MODEL'] as const
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]))
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  try {
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    process.env.DEEPSEEK_API_KEY = 'local-test-key'
    process.env.DEEPSEEK_BASE_URL = `http://127.0.0.1:${address.port}`
    process.env.DEEPSEEK_MODEL = 'local-agent'
    process.env.JUDGE_MODEL = 'local-judge'
    @Module({ providers: [ModelService, AgentService, EvaluationService] })
    class TestModule {}
    const app = await NestFactory.createApplicationContext(TestModule, { logger: false })
    try {
      const run = await app.get(AgentService).run(sample.inputs, fixture.orders)
      const evaluation = await app.get(EvaluationService).evaluate(sample, run)
      assert.equal(evaluation.status, 'passed')
      assert.deepEqual(requests.map((item) => item.model), ['local-agent', 'local-agent', 'local-judge'])
      assert.ok(requests.every((item) => item.thinking.type === 'disabled'))
      assert.equal(requests[2].tools, undefined)
    } finally { await app.close() }
  } finally {
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  }
})
