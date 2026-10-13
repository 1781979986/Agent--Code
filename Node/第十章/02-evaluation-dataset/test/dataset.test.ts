import 'reflect-metadata'
import assert from 'node:assert/strict'
import test from 'node:test'
import { DatasetSchema, DatasetService, readData } from '../src/dataset.service.js'

test('数据集包含单轮、多轮、澄清、资料不足和金额边界样本', () => {
  const service = new DatasetService()
  assert.equal(service.list().length, 7)
  assert.equal(service.prepare('follow-up-refund').inputs.turns.length, 2)
  assert.equal(service.getReference('missing-order-id').turns[0].behavior, 'clarify')
  assert.equal(service.getReference('warranty-unknown').turns[0].behavior, 'insufficient_evidence')
  assert.equal(service.getReference('refund-at-threshold').turns[0].facts.requiresManualReview, false)
  assert.equal(service.getReference('refund-over-threshold').turns[0].facts.requiresManualReview, true)
})

test('运行参数排除 referenceOutputs，参考结果可以独立获取', () => {
  const service = new DatasetService()
  const run = service.prepare('order-amount')
  assert.deepEqual(Object.keys(run).sort(), ['caseId', 'datasetVersion', 'environment', 'inputs', 'runId'])
  assert.deepEqual(Object.keys(run.inputs).sort(), ['context', 'turns'])
  assert.equal(service.getReference('order-amount').turns[0].facts.paidAmountYuan, 3500)
})

test('每次运行有独立 ID、订单、输入、消息和记忆，修改副本不污染下一次', () => {
  const service = new DatasetService()
  const first = service.prepare('order-amount')
  first.environment.orders[0].paidAmountYuan = 1
  first.environment.messages.push({ role: 'assistant', content: '旧回答' })
  first.environment.memories.preference = '旧记忆'
  first.inputs.turns[0] = '被修改的问题'
  first.inputs.context.userId = 'another-user'
  const second = service.prepare('order-amount')
  assert.notEqual(first.runId, second.runId)
  assert.equal(second.environment.orders[0].paidAmountYuan, 3500)
  assert.deepEqual(second.environment.messages, [])
  assert.deepEqual(second.environment.memories, {})
  assert.equal(second.inputs.turns[0], '帮我查一下订单 A1024 的实付金额。')
  assert.equal(second.inputs.context.userId, 'user-001')
})

test('修改评分参考不影响原始数据集', () => {
  const service = new DatasetService()
  service.getReference('order-amount').turns[0].facts.paidAmountYuan = 1
  assert.equal(service.getReference('order-amount').turns[0].facts.paidAmountYuan, 3500)
})

test('缺少参考结果、重复 ID、轮次不匹配时，结构检查失败', () => {
  const original = DatasetSchema.parse(readData('dataset.json'))
  const missing = structuredClone(original) as any
  delete missing.cases[0].referenceOutputs
  assert.equal(DatasetSchema.safeParse(missing).success, false)
  const duplicate = structuredClone(original)
  duplicate.cases.push(structuredClone(duplicate.cases[0]))
  assert.equal(DatasetSchema.safeParse(duplicate).success, false)
  const mismatched = structuredClone(original)
  mismatched.cases[0].inputs.turns.push('多出来的一轮')
  assert.equal(DatasetSchema.safeParse(mismatched).success, false)
})

test('拼错样本 ID 时明确报错', () => {
  assert.throws(() => new DatasetService().prepare('not-a-case'), /找不到样本/)
})

test('人工编写的金额参考与固定业务资料一致', () => {
  const service = new DatasetService()
  const { environment, inputs } = service.prepare('order-amount')
  const order = environment.orders.find((item) => item.orderId === 'A1024'
    && item.tenantId === inputs.context.tenantId && item.userId === inputs.context.userId)!
  assert.equal(service.getReference('order-amount').turns[0].facts.paidAmountYuan, order.paidAmountYuan)
  for (const id of ['refund-at-threshold', 'refund-over-threshold']) {
    const facts = service.getReference(id).turns[0].facts
    assert.equal(facts.requiresManualReview, Number(facts.refundAmountYuan) > environment.policy.manualReviewAboveYuan)
    assert.equal(facts.policyId, environment.policy.id)
  }
  const followUp = service.getReference('follow-up-refund').turns[1].facts
  assert.equal(followUp.refundAmountYuan, order.paidAmountYuan)
  assert.equal(followUp.requiresManualReview, order.paidAmountYuan > environment.policy.manualReviewAboveYuan)
})
