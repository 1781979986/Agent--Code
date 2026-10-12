import { Annotation, StateGraph, START, END } from '@langchain/langgraph'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { AnalysisProvider, verificationSql } from './model.js'
import { executeQuery } from './sandbox.js'
import { buildReport } from './analysis.js'
import type {
	Dataset,
	Decision,
	Evidence,
	Mode,
	Progress,
	Report,
	Row,
	Session
} from '../shared/types.js'

const Flow = Annotation.Root({
	decision: Annotation<Decision | null>(),
	evidence: Annotation<Evidence | null>(),
	error: Annotation<string>(),
	attempts: Annotation<number>(),
	report: Annotation<Report | null>()
})
export type QueryExecutor = typeof executeQuery

/**
 * 创建分析流程图；从模型生成查询决策开始，经过受限查询，最终基于真实结果生成报告。
 * 查询失败时最多重新规划一次，避免无限循环。
 */
export function createAnalysisGraph(options: {
	question: string
	dataset: Dataset
	session: Session
	mode: Mode
	directory: string
	signal: AbortSignal
	emit: (event: Progress) => void
	provider?: AnalysisProvider
	execute?: QueryExecutor
}) {
	// 解构流程运行所需的基础参数。
	const { question, dataset, session, mode, directory, signal, emit } = options

	// 使用默认分析模型提供者。
	const provider = options.provider || new AnalysisProvider()

	// 使用默认查询执行器执行 SQL。
	const execute = options.execute || executeQuery

	// 创建状态图流程。
	return (
		new StateGraph(Flow)

			// =========================
			// 决策节点：生成查询计划
			// =========================
			.addNode('decide', async (state) => {
				// 检查任务是否已经被取消。
				signal.throwIfAborted()

				// 通知前端当前处于决策阶段。
				// 如果上一轮查询失败，则提示模型根据错误信息重新规划。
				emit({
					stage: 'decide',
					message: state.error
						? '根据查询错误修正计划'
						: '确认本轮指标、时间和区域'
				})

				// 调用模型，根据用户问题、数据集信息和历史上下文生成查询决策。
				const decision = await provider.decide(
					question,
					dataset,
					session,
					mode,
					signal,

					// 如果存在上一次错误，将错误信息反馈给模型。
					state.error
				)

				// 保存新的查询决策，并清空旧错误。
				return {
					decision,

					// 记录当前已经尝试的次数。
					attempts: state.attempts + 1,

					error: ''
				}
			})

			// =========================
			// 查询节点：执行真实数据查询
			// =========================
			.addNode('query', async (state) => {
				// 当前节点必须已经存在查询决策。
				const decision = state.decision!

				// 通知前端正在执行受限查询。
				emit({
					stage: 'query',
					message: '在只读容器中计算，并核对分析口径'
				})

				// 记录查询开始时间，用于统计执行耗时。
				const started = performance.now()

				try {
					// 在指定数据集的 DuckDB 文件中执行 SQL。
					const rows = await execute(
						join(directory, 'data.duckdb'),
						decision.sql!,
						signal
					)

					// AI 模式下增加一次口径验证：
					// 模型生成 SQL 后，再使用系统确认 SQL 查询一次。
					// 两次结果不一致时，认为模型查询不可靠。
					if (
						mode === 'ai' &&
						decision.sql!.trim() !== verificationSql(decision).trim()
					) {
						const verified = await execute(
							join(directory, 'data.duckdb'),
							verificationSql(decision),
							signal
						)

						// 对比两个查询结果，确保统计口径一致。
						if (canonical(rows) !== canonical(verified))
							throw new Error(
								`模型 SQL 结果与已确认口径不一致。标准查询：${verificationSql(decision)}`
							)
					}

					// 查询成功后生成证据对象。
					// 后续报告、图表和解释都基于该真实查询结果。
					const evidence: Evidence = {
						id: randomUUID(),

						// 保存实际执行的 SQL。
						sql: decision.sql!,

						// 保存数据库返回的数据。
						rows,

						// 关联当前数据集。
						dataset,

						// 保存本次分析规格。
						spec: decision.spec!,

						// 记录创建时间。
						createdAt: new Date().toISOString(),

						// 记录查询耗时。
						durationMs: Math.round(performance.now() - started)
					}

					return {
						evidence,
						error: ''
					}
				} catch (error) {
					// 查询失败时检查任务是否被取消。
					signal.throwIfAborted()

					// 将错误返回到状态中。
					// 后续流程会判断是否需要重新生成 SQL。
					return {
						error: (error as Error).message
					}
				}
			})

			// =========================
			// 报告节点：生成最终结果
			// =========================
			.addNode('prepare_report', (state) => {
				// 检查任务是否取消。
				signal.throwIfAborted()

				// 通知前端开始整理报告。
				emit({
					stage: 'report',
					message: '从查询结果生成图表、说明与依据'
				})

				// 如果查询成功，则根据真实证据生成完整报告。
				// 如果查询失败，则生成错误说明报告。
				const report: Report = state.evidence
					? buildReport(question, mode, state.evidence)
					: {
							id: randomUUID(),

							question,

							mode,

							// 根据决策结果判断失败类型：
							// clarify：需要用户补充信息；
							// insufficient：没有有效查询结果。
							status:
								state.decision?.route === 'clarify'
									? 'clarify'
									: 'insufficient',

							// 返回失败原因或模型给出的说明。
							answer: state.error
								? `本轮未取得有效结果：${state.error}`
								: state.decision!.message,

							evidence: null,
							table: [],
							chart: null,
							notes: [],

							createdAt: new Date().toISOString()
						}

				return { report }
			})

			// =========================
			// 流程连接
			// =========================

			// 开始后进入决策节点。
			.addEdge(START, 'decide')

			// 根据模型决策结果选择下一步：
			// query：执行查询；
			// prepare_report：无需查询，直接返回说明。
			.addConditionalEdges(
				'decide',
				(state) =>
					state.decision?.route === 'query' ? 'query' : 'prepare_report',
				['query', 'prepare_report']
			)

			// 查询结束后的分支：
			// 有错误且尝试次数小于 2 → 返回决策节点重新生成方案；
			// 否则直接生成报告。
			.addConditionalEdges(
				'query',
				(state) =>
					state.error && state.attempts < 2 ? 'decide' : 'prepare_report',
				['decide', 'prepare_report']
			)

			// 报告生成完成后结束流程。
			.addEdge('prepare_report', END)

			// 编译状态图，生成可执行 Graph。
			.compile()
	)
}

function canonical(rows: Row[]) {
	return JSON.stringify(
		rows
			.map((row) =>
				Object.fromEntries(
					Object.entries(row)
						.sort(([a], [b]) => a.localeCompare(b))
						.map(([key, value]) => [
							key,
							key === 'amount' ? Number(value) : value
						])
				)
			)
			.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
	)
}
