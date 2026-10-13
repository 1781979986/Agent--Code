import { Inject, Injectable } from '@nestjs/common'
import type {
	ChatCompletionMessageParam,
	ChatCompletionTool
} from 'openai/resources/chat/completions'
import { z } from 'zod'
import { ModelService } from './model.service.js'
import type { AgentResult, Order, Sample, ToolRecord } from './sample.js'

export const OrderArgs = z
	.object({ orderId: z.string().trim().min(1) })
	.strict()
const tools: ChatCompletionTool[] = [
	{
		type: 'function',
		function: {
			name: 'get_order',
			description: '根据订单号查询当前登录用户的订单与实付金额。',
			parameters: {
				type: 'object',
				properties: { orderId: { type: 'string' } },
				required: ['orderId'],
				additionalProperties: false
			}
		}
	}
]

/** 执行只读订单工具，身份从应用传入，模型只能提供订单号。 */
export function executeOrderTool(
	name: string,
	args: unknown,
	context: Sample['inputs']['context'],
	orders: Order[]
): ToolRecord {
	const parsed = OrderArgs.safeParse(args)
	if (name !== 'get_order' || !parsed.success) {
		return {
			name,
			args,
			result: { found: false, error: '工具名称或参数不合法。' }
		}
	}
	const order = orders.find(
		(item) =>
			item.orderId === parsed.data.orderId &&
			item.tenantId === context.tenantId &&
			item.userId === context.userId
	)
	return {
		name,
		args,
		result: order
			? { found: true, order: structuredClone(order) }
			: { found: false }
	}
}

@Injectable()
export class AgentService {
	constructor(@Inject(ModelService) private readonly model: ModelService) {}

	/**
	 * 运行一个最小化的 Tool Calling Agent 循环。
	 *
	 * 执行流程：
	 * 1. 初始化系统提示词和用户问题；
	 * 2. 调用模型判断是否需要使用工具；
	 * 3. 如果模型返回 Tool Call，则执行对应工具；
	 * 4. 将工具结果重新交给模型；
	 * 5. 模型生成最终回答后结束。
	 *
	 * 返回：
	 * - Agent 最终回答；
	 * - 实际调用过的工具记录；
	 * - 执行过程中产生的错误信息。
	 */
	async run(inputs: Sample['inputs'], orders: Order[]): Promise<AgentResult> {
		// 保存本次 Agent 执行期间所有工具调用记录。
		const toolCalls: ToolRecord[] = []

		// 构造模型上下文。
		// system 消息负责约束 Agent 行为，
		// user 消息保存用户真实问题。
		const messages: ChatCompletionMessageParam[] = [
			{
				role: 'system',
				content:
					'你是售后查询助手。订单事实必须先通过 get_order 查询。按用户问题回答，金额说明币种。' +
					'资料没有提供的信息要说明不知道。不要承诺退款已批准、已提交或到账时间。'
			},
			{
				role: 'user',
				content: inputs.turns[0]
			}
		]

		try {
			// Tool Calling 多轮循环。
			// 每一轮：
			// 1. 将当前消息发送给模型；
			// 2. 判断模型是否需要调用工具；
			// 3. 执行工具并把结果追加回上下文。
			for (let round = 0; round < 4; round++) {
				// 调用模型。
				// tools 参数会告诉模型当前有哪些可调用工具。
				const message = await this.model.complete('agent', {
					messages,
					tools
				})

				// 如果模型没有返回工具调用，
				// 说明它已经能够直接回答用户问题，Agent 流程结束。
				if (!message.tool_calls?.length) {
					return {
						origin: 'live',
						status: 'completed',
						answer: message.content || '',
						toolCalls
					}
				}

				// 保存模型产生的 assistant 消息。
				// 后续模型需要知道自己之前提出过哪些 Tool Call。
				messages.push({
					role: 'assistant',
					content: message.content,
					tool_calls: message.tool_calls
				})

				// 逐个执行模型请求的工具。
				for (const call of message.tool_calls) {
					// 限制一次 Agent 最大工具调用数量，
					// 防止模型陷入无限调用循环。
					if (toolCalls.length >= 6)
						throw new Error('工具调用次数已达到本例上限。')

					// 当前示例只支持 Function Calling 类型工具。
					if (call.type !== 'function')
						throw new Error('本例只支持 Function Tool。')

					// 模型返回的参数是 JSON 字符串，
					// 需要解析成 JavaScript 对象后才能执行工具。
					let args: unknown
					try {
						args = JSON.parse(call.function.arguments)
					} catch {
						// 如果模型返回非法 JSON，
						// 保留原始字符串，交给工具层处理。
						args = call.function.arguments
					}

					// 根据模型指定的工具名称执行实际业务逻辑。
					// 例如：
					// get_order({ orderId: 'A1024' })
					const record = executeOrderTool(
						call.function.name,
						args,
						inputs.context,
						orders
					)

					// 保存工具调用记录，
					// 后续评测程序可以检查：
					// - 是否调用正确工具；
					// - 参数是否正确；
					// - 工具返回结果是否符合预期。
					toolCalls.push(record)

					// 将工具执行结果作为 tool 消息重新放回上下文。
					// 下一轮模型调用时，可以基于真实数据继续生成回答。
					messages.push({
						role: 'tool',
						tool_call_id: call.id,
						content: JSON.stringify(record.result)
					})
				}
			}

			// 超过最大循环次数，说明 Agent 没有正常结束。
			throw new Error('模型调用轮次已达到本例上限。')
		} catch (error) {
			// 捕获模型调用、工具执行等过程中的异常，
			// 返回统一错误结构，方便评测系统统计失败原因。
			return {
				origin: 'live',
				status: 'error',
				answer: '',
				toolCalls,
				error: error instanceof Error ? error.message : String(error)
			}
		}
	}
}
