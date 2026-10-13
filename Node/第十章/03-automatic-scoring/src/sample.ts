import { readFileSync } from 'node:fs'
import { z } from 'zod'

const Text = z.string().trim().min(1)
export const OrderSchema = z.object({
  tenantId: Text, userId: Text, orderId: Text, product: Text,
  paidAmountYuan: z.number().nonnegative()
}).strict()
const SampleSchema = z.object({
  id: z.literal('order-amount'), name: Text, fixtureId: Text,
  inputs: z.object({
    context: z.object({ tenantId: Text, userId: Text }).strict(),
    turns: z.array(Text).length(1)
  }).strict(),
  referenceOutputs: z.object({ turns: z.array(z.object({
    behavior: z.literal('answer'),
    facts: z.object({ orderId: Text, paidAmountYuan: z.number().nonnegative() }).strict(),
    criteria: z.array(Text).min(1)
  }).strict()).length(1) }).strict()
}).strict()

export type Sample = z.infer<typeof SampleSchema>
export type Order = z.infer<typeof OrderSchema>
export interface ToolRecord {
  name: string
  args: unknown
  result: { found: boolean; order?: Order; error?: string }
}
export interface AgentResult {
  origin: 'live' | 'teaching_example'
  status: 'completed' | 'error'
  answer: string
  toolCalls: ToolRecord[]
  error?: string
}

/** 复用 02 的 JSON 数据，只取本节要评测的一轮订单查询，不依赖 02 的运行环境。 */
export function loadCase() {
  const read = (name: string) => JSON.parse(readFileSync(
    new URL(`../../02-evaluation-dataset/data/${name}`, import.meta.url), 'utf8'
  )) as unknown
  const dataset = z.object({
    version: Text,
    cases: z.array(z.object({ id: Text }).passthrough())
  }).parse(read('dataset.json'))
  const matches = dataset.cases.filter((item) => item.id === 'order-amount')
  if (matches.length !== 1) throw new Error('02 数据集中应有且只有一条 order-amount 样本。')
  const sample = SampleSchema.parse(matches[0])
  const fixtures = z.array(z.object({ id: Text, orders: z.array(OrderSchema) }))
    .parse(read('fixtures.json'))
  const selected = fixtures.filter((item) => item.id === sample.fixtureId)
  if (selected.length !== 1) throw new Error('找不到唯一的测试资料，请检查 fixtureId。')
  const fixture = selected[0]
  const facts = sample.referenceOutputs.turns[0].facts
  const context = sample.inputs.context
  const orders = fixture.orders.filter((order) => order.orderId === facts.orderId
    && order.tenantId === context.tenantId && order.userId === context.userId)
  if (orders.length !== 1 || orders[0].paidAmountYuan !== facts.paidAmountYuan) {
    throw new Error('参考金额与当前用户的订单资料不一致，请先核对测试样本。')
  }
  return { datasetVersion: dataset.version, sample, fixture }
}
