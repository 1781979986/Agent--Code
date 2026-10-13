import { Injectable } from '@nestjs/common'
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { z } from 'zod'

const Text = z.string().trim().min(1)
const Context = z.object({ tenantId: Text, userId: Text }).strict()
const ReferenceTurn = z
	.object({
		behavior: z.enum(['answer', 'clarify', 'insufficient_evidence']),
		facts: z.record(Text, z.union([z.string(), z.number(), z.boolean()])),
		criteria: z.array(Text).min(1)
	})
	.strict()

export const DatasetSchema = z
	.object({
		version: Text,
		cases: z
			.array(
				z
					.object({
						id: Text,
						name: Text,
						fixtureId: Text,
						inputs: z
							.object({ context: Context, turns: z.array(Text).min(1) })
							.strict(),
						referenceOutputs: z
							.object({ turns: z.array(ReferenceTurn).min(1) })
							.strict()
					})
					.strict()
			)
			.min(1)
	})
	.strict()
	.superRefine((dataset, ctx) => {
		const ids = new Set<string>()
		for (const [index, sample] of dataset.cases.entries()) {
			if (ids.has(sample.id)) {
				ctx.addIssue({
					code: 'custom',
					path: ['cases', index, 'id'],
					message: '样本 ID 重复。'
				})
			}
			ids.add(sample.id)
			if (sample.inputs.turns.length !== sample.referenceOutputs.turns.length) {
				ctx.addIssue({
					code: 'custom',
					path: ['cases', index],
					message: '每轮输入都需要对应的检查要求。'
				})
			}
		}
	})

const FixturesSchema = z
	.array(
		z
			.object({
				id: Text,
				orders: z.array(
					z
						.object({
							tenantId: Text,
							userId: Text,
							orderId: Text,
							product: Text,
							paidAmountYuan: z.number().nonnegative()
						})
						.strict()
				),
				policy: z
					.object({
						id: Text,
						manualReviewAboveYuan: z.number().nonnegative(),
						content: Text
					})
					.strict()
			})
			.strict()
	)
	.min(1)

type Fixture = z.infer<typeof FixturesSchema>[number]

/** 读取本节固定的测试资料；订单、规则和身份均为合成数据。 */
export function readData(name: 'dataset.json' | 'fixtures.json'): unknown {
	return JSON.parse(
		readFileSync(new URL(`../data/${name}`, import.meta.url), 'utf8')
	)
}

@Injectable()
export class DatasetService {
	private readonly dataset = DatasetSchema.parse(readData('dataset.json'))
	private readonly fixtures = FixturesSchema.parse(readData('fixtures.json'))

	/** 检查资料引用，避免评测开始以后才发现样本使用了不存在的测试环境。 */
	constructor() {
		const ids = this.fixtures.map((fixture) => fixture.id)
		if (new Set(ids).size !== ids.length) throw new Error('测试资料 ID 重复。')
		for (const sample of this.dataset.cases) {
			if (!ids.includes(sample.fixtureId))
				throw new Error(`找不到测试资料：${sample.fixtureId}`)
		}
	}

	/** 返回样本目录，供运行前核对测试范围。 */
	list() {
		return this.dataset.cases.map((sample) => ({
			id: sample.id,
			name: sample.name,
			turns: sample.inputs.turns.length
		}))
	}

	/**
	 * 为一次 Agent 运行创建独立的输入数据和工具运行环境。
	 *
	 * 注意：
	 * - Agent 只能看到 inputs 和 environment 中的数据。
	 * - referenceOutputs（参考答案）不会返回给 Agent，而是由评测程序单独保存。
	 * - 每次 prepare 都会创建新的环境副本，避免不同运行之间相互污染。
	 */
	prepare(caseId: string) {
		// 根据测试案例 ID 查找对应样本。
		// 样本中包含 Agent 需要完成的任务输入，以及关联的数据 fixture。
		const sample = this.find(caseId)

		// 根据样本中的 fixtureId 找到对应的测试数据。
		// fixture 通常模拟 Agent 可以通过工具访问的业务数据，例如订单、用户信息等。
		const fixture = this.fixtures.find((item) => item.id === sample.fixtureId)!

		// 创建本次运行独立的数据环境。
		//
		// 使用 structuredClone 深拷贝 fixture：
		// 1. 避免 Agent 修改本次环境时影响原始测试数据；
		// 2. 保证每次 prepare 都能获得完全干净的初始状态。
		//
		// 同时额外增加：
		// - messages：模拟 Agent 对话上下文；
		// - memories：模拟 Agent 长期记忆存储。
		const environment: Fixture & {
			messages: Array<{ role: 'user' | 'assistant'; content: string }>
			memories: Record<string, unknown>
		} = {
			...structuredClone(fixture),
			messages: [],
			memories: {}
		}

		// 返回一次新的运行实例。
		//
		// runId：
		//   标识一次独立执行，方便后续追踪 Agent 运行过程。
		//
		// datasetVersion：
		//   记录当前使用的数据集版本，保证评测结果可复现。
		//
		// caseId：
		//   当前执行的测试案例 ID。
		//
		// inputs：
		//   提供给 Agent 的任务输入。
		//   再次 clone，避免外部修改原始测试样本。
		//
		// environment：
		//   提供给工具调用和 Agent 执行过程使用的隔离环境。
		return {
			runId: randomUUID(),
			datasetVersion: this.dataset.version,
			caseId: sample.id,
			inputs: structuredClone(sample.inputs),
			environment
		}
	}

	/** 评分时单独读取检查要求，不把整条样本作为 Agent 输入。 */
	getReference(caseId: string) {
		return structuredClone(this.find(caseId).referenceOutputs)
	}

	/** 按稳定的样本 ID 定位任务，拼错 ID 时直接报告错误。 */
	private find(caseId: string) {
		const sample = this.dataset.cases.find((item) => item.id === caseId)
		if (!sample) throw new Error(`找不到样本：${caseId}`)
		return sample
	}
}
