import { Inject, Injectable } from '@nestjs/common'
import { randomUUID } from 'node:crypto'
import { readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { Storage } from './storage.js'
import { createAnalysisGraph } from './workflow.js'
import { reportHtml } from './report.js'
import type { Mode, Progress, Report, Session } from '../shared/types.js'

/** 固定会话的数据版本，保存追问条件与已完成报告；同一会话串行执行。 */
@Injectable()
export class AnalysisService {
	private busy = new Map<string, AbortSignal>()
	constructor(@Inject(Storage) readonly storage: Storage) {}
	async listSessions() {
		const names = await readdir(join(this.storage.directory, 'sessions')).catch(
			() => [] as string[]
		)
		const sessions = await Promise.all(
			names
				.filter((name) => name.endsWith('.json'))
				.map((name) => this.storage.session(name.slice(0, -5)))
		)
		return sessions.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
	}

	/** 创建数据分析会话；只有通过质量检查的数据集才能进入查询流程。 */
	async createSession(datasetId: string) {
		// 根据数据集 ID 加载对应的数据集元信息。
		const dataset = await this.storage.dataset(datasetId)

		// 如果数据集存在校验问题，则禁止创建会话。
		// 必须先修复原始文件并重新上传，避免错误数据进入分析流程。
		if (dataset.status !== 'ready')
			throw new Error('数据存在质量问题，请修改原表后重新上传')

		// 创建新的分析会话记录。
		const session: Session = {
			// 为当前会话生成唯一 ID，用于后续查询和报告关联。
			id: randomUUID(),

			// 记录当前会话所使用的数据集。
			datasetId,

			// 默认使用数据集名称作为会话标题。
			title: dataset.name,

			// 保存会话创建时间。
			createdAt: new Date().toISOString(),

			// 初始状态下还没有生成任何分析报告。
			reports: [],

			// 保存最近一次分析规格，初始为空。
			// 后续可用于恢复上下文或继续分析。
			lastSpec: null
		}

		// 持久化保存会话信息。
		await this.storage.saveSession(session)

		// 返回创建成功的会话。
		return session
	}

	/**
	 * 执行一次完整的数据分析任务；负责任务互斥、创建分析流程、保存分析结果。
	 */
	async analyze(
		// 当前分析会话 ID，用于关联数据集和历史报告。
		sessionId: string,

		// 用户提出的自然语言分析问题。
		question: string,

		// 当前分析模式，例如普通分析、深度分析等。
		mode: Mode,

		// 用于支持任务取消，当客户端断开或主动停止时终止执行。
		signal: AbortSignal,

		// 分析过程中的进度回调，用于实时推送状态。
		emit: (progress: Progress) => void
	) {
		// 检查当前会话是否已有任务正在执行。
		// 同一个会话不允许同时运行多个分析任务，避免状态冲突。
		if (this.busy.has(sessionId) && !this.busy.get(sessionId)!.aborted)
			throw new Error('本会话还有任务在运行，请等停止完成后再提问')

		// 标记当前会话正在执行任务，用于后续并发控制。
		this.busy.set(sessionId, signal)

		try {
			// 加载当前分析会话信息。
			const session = await this.storage.session(sessionId)

			// 根据会话关联的数据集 ID 加载数据集元信息。
			const dataset = await this.storage.dataset(session.datasetId)

			// 只有通过数据质量检查的数据集才能参与分析。
			if (dataset.status !== 'ready') throw new Error('当前数据不允许查询')

			// 创建分析流程图。
			// Graph 内部会按照节点定义依次完成：
			// 问题理解 → 查询决策 → 数据执行 → 结果解释 → 生成报告。
			const graph = createAnalysisGraph({
				question,
				dataset,
				session,
				mode,

				// 传递取消信号，使流程中的异步任务支持中断。
				signal,

				// 将节点执行过程实时反馈给外部。
				emit,

				// 指定当前数据集所在目录，供查询节点读取数据文件。
				directory: this.storage.datasetDir(dataset.id)
			})

			// 启动分析流程。
			// 初始状态包含空的决策、证据、错误信息和报告。
			const state = await graph.invoke(
				{
					decision: null,
					evidence: null,
					error: '',
					attempts: 0,
					report: null
				},
				{
					// 将取消信号传入 Graph。
					signal,

					// 限制最大节点递归次数，避免异常流程无限循环。
					recursionLimit: 12
				}
			)

			// 分析结束后再次检查任务是否已被取消。
			signal.throwIfAborted()

			// 获取 Graph 最终生成的分析报告。
			const report = state.report!

			// 将本次报告加入当前会话历史。
			session.reports.push(report)

			// 如果报告中包含有效分析依据，则保存最近一次分析规格。
			// 后续可以用于继续分析或恢复上下文。
			if (report.evidence) session.lastSpec = report.evidence.spec

			// 第一次分析后，使用用户问题前 32 个字符作为会话标题。
			session.title = session.reports[0].question.slice(0, 32)

			// 保存更新后的会话信息。
			await this.storage.saveSession(session)

			// 返回最终分析报告。
			return report
		} finally {
			// 任务结束后清理 busy 状态。
			// 只有当前任务仍然是登记中的任务时才删除，
			// 避免旧任务结束时误删新任务状态。
			if (this.busy.get(sessionId) === signal) this.busy.delete(sessionId)
		}
	}
	async findReport(sessionId: string, reportId: string): Promise<Report> {
		const session = await this.storage.session(sessionId)
		const report = session.reports.find((r) => r.id === reportId)
		if (!report) throw new Error('报告不存在')
		return report
	}
	/** 导出分析报告；生成 HTML 文件，同时保存 JSON 格式报告用于后续追踪和恢复。 */
	async export(sessionId: string, reportId: string) {
		// 根据会话 ID 和报告 ID 查找对应的分析报告。
		// 通过会话校验报告归属，避免访问其他会话的数据。
		const report = await this.findReport(sessionId, reportId)

		// 将报告数据渲染为完整 HTML 页面。
		// HTML 内容包含分析结论、数据表格、图表等展示信息。
		const html = reportHtml(report)

		// 保存 JSON 格式报告。
		// JSON 保留结构化数据，方便后续重新加载、二次处理或审计。
		await this.storage.save(
			join(this.storage.directory, 'reports', `${report.id}.json`),
			report
		)

		// 动态引入文件系统方法，用于保存导出的 HTML 文件。
		const { writeFile, mkdir } = await import('node:fs/promises')

		// 创建报告存储目录。
		// recursive: true 表示目录已存在时不会报错。
		await mkdir(join(this.storage.directory, 'reports'), { recursive: true })

		// 将生成的 HTML 报告保存到本地文件。
		await writeFile(
			join(this.storage.directory, 'reports', `${report.id}.html`),
			html
		)

		// 返回 HTML 内容，由 Controller 直接作为下载响应返回给客户端。
		return html
	}
}
