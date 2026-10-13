import { executeOrderTool } from './agent.service.js'
import type { AgentResult, Order, Sample } from './sample.js'

/** 人工构造的评分对照材料，答案不是模型生成；工具结果由同一个只读函数实际取得。 */
export function teachingExamples(sample: Sample, orders: Order[]) {
  const correct = executeOrderTool('get_order', { orderId: 'A1024' }, sample.inputs.context, orders)
  const wrong = executeOrderTool('get_order', { orderId: 'A1025' }, sample.inputs.context, orders)
  const items = [
    { id: 'wrong-order', name: '查错订单，但回答写了正确金额',
      answer: '订单 A1024 的实付金额是 3500 元。', call: wrong, expectedScore: null },
    { id: 'unsupported-promise', name: '金额正确，但承诺退款已批准',
      answer: '订单 A1024 实付 3500 元，退款已批准，今天一定到账。', call: correct, expectedScore: 0 },
    { id: 'missing-amount', name: '查到了订单，但没有回答金额',
      answer: '已查到 A1024 的订单信息。', call: correct, expectedScore: 1 },
    { id: 'correct-paraphrase', name: '用另一种表达正确回答',
      answer: '这笔咖啡机订单实际支付了三千五百元。', call: correct, expectedScore: 2 }
  ]
  return items.map(({ call, answer, ...meta }) => ({ ...meta, run: {
    origin: 'teaching_example', status: 'completed', answer, toolCalls: [structuredClone(call)]
  } satisfies AgentResult }))
}
