import {
	Controller,
	Get,
	Post,
	Param,
	Body,
	Res,
	UploadedFile,
	UseInterceptors,
	Inject
} from '@nestjs/common'
import { FileInterceptor } from '@nestjs/platform-express'
import type { Response } from 'express'
import { z } from 'zod'
import { join } from 'node:path'
import { readFile } from 'node:fs/promises'
import { DatasetService } from './dataset.service.js'
import { AnalysisService } from './analysis.service.js'
import { MultimodalService } from './multimodal.service.js'
import { Storage, root } from './storage.js'
import { resultCsv } from './report.js'

const Mode = z.enum(['replay', 'ai'])
const ImportRequest = z.object({
	uploadId: z.uuid(),
	sheet: z.string().min(1),
	mapping: z.object({
		date: z.string(),
		region: z.string(),
		product: z.string(),
		paid: z.string(),
		refund: z.string()
	})
})

/** HTTP 入口只接收业务参数，数据路径和执行权限由服务端确定。 */
@Controller('api')
export class AppController {
	constructor(
		@Inject(DatasetService) private datasets: DatasetService,
		@Inject(AnalysisService) private analyses: AnalysisService,
		@Inject(MultimodalService) private multimodal: MultimodalService,
		@Inject(Storage) private storage: Storage
	) {}
	@Get('health') health() {
		return { ok: true }
	}
	@Get('meta') meta() {
		return {
			ai: Boolean(process.env.DEEPSEEK_API_KEY),
			speech: Boolean(
				process.env.DASHSCOPE_API_KEY && process.env.DASHSCOPE_BASE_URL
			),
			vision: Boolean(
				process.env.DASHSCOPE_API_KEY && process.env.DASHSCOPE_BASE_URL
			),
			model: process.env.DEEPSEEK_MODEL || 'deepseek-v4-flash'
		}
	}
	@Get('datasets') listDatasets() {
		return this.datasets.list()
	}
	// 定义 POST 上传接口，路径为当前控制器路由下的 uploads。
	@Post('uploads')
	@UseInterceptors(
		// 使用文件上传拦截器，接收 multipart/form-data 中字段名为 file 的文件。
		FileInterceptor('file', {
			limits: {
				fileSize: 5 * 1024 * 1024, // 单个文件大小上限为 5 MiB，超过限制会拒绝上传。
				files: 1 // 每次请求最多上传 1 个文件。
			}
		})
	)
	// @UploadedFile() 获取拦截器解析出的文件对象，Express.Multer.File 提供类型声明。
	upload(@UploadedFile() file: Express.Multer.File) {
		// 请求中没有上传文件时直接报错，不再执行后续处理。
		if (!file) throw new Error('请选择文件')

		// 将文件交给数据集服务处理，并返回其处理结果。
		return this.datasets.upload(
			file.buffer, // 内存中的文件二进制内容。
			file.originalname // 客户端上传时提供的原始文件名。
		)
	}
	@Post('datasets') async import(@Body() body: unknown) {
		const data = ImportRequest.parse(body)
		return this.datasets.import(data.uploadId, data.sheet, data.mapping)
	}
	@Get('datasets/:id/rows') async rows(@Param('id') id: string) {
		return (
			await this.storage.load<any[]>(
				join(this.storage.datasetDir(id), 'rows.json')
			)
		).slice(0, 20)
	}
	@Get('samples/:name') async sample(
		@Param('name') name: string,
		@Res() res: Response
	) {
		if (
			![
				'sales-demo.xlsx',
				'sales-demo.csv',
				'sales-issues.xlsx',
				'dashboard.png'
			].includes(name)
		)
			throw new Error('样例不存在')
		res.attachment(name).send(await readFile(join(root, 'samples', name)))
	}
	@Get('sessions') listSessions() {
		return this.analyses.listSessions()
	}
	@Post('sessions') createSession(@Body() body: unknown) {
		return this.analyses.createSession(
			z.object({ datasetId: z.uuid() }).parse(body).datasetId
		)
	}
	@Get('sessions/:id') session(@Param('id') id: string) {
		return this.storage.session(id)
	}

	/** 接收分析请求，并通过流式响应持续返回分析进度和最终报告。 */
	@Post('sessions/:id/analyze')
	async analyze(
		// 获取路径参数中的会话 ID，用于关联对应的数据分析上下文。
		@Param('id') id: string,

		// 获取请求体中的分析参数，初始类型未知，后续通过 Schema 校验。
		@Body() raw: unknown,

		// 获取 Response 对象，用于手动控制流式响应。
		@Res() response: Response
	) {
		// 使用 Zod 校验请求参数：
		// question：用户问题，去除首尾空格，长度限制 1～2000 字符。
		// mode：分析模式，必须符合预定义枚举。
		const { question, mode } = z
			.object({
				question: z.string().trim().min(1).max(2000),
				mode: Mode
			})
			.parse(raw)

		// 建立流式响应连接，将分析过程中的状态实时发送给客户端。
		await streamResponse(response, async (signal, send) => {
			// 调用分析服务执行完整分析流程。
			// signal 用于支持客户端断开后的任务取消。
			// progress 回调会将中间执行状态实时推送给前端。
			const report = await this.analyses.analyze(
				id,
				question,
				mode,
				signal,
				(progress) => send('progress', progress)
			)

			// 分析完成后，将最终报告通过流式通道发送给客户端。
			send('report', report)
		})
	}

	/** 导出指定分析报告；将报告内容生成 HTML 文件并作为附件返回给客户端。 */
	@Get('sessions/:id/reports/:reportId/export')
	async export(
		// 获取分析会话 ID，用于确认报告所属会话。
		@Param('id') id: string,

		// 获取需要导出的报告 ID。
		@Param('reportId') reportId: string,

		// 获取 Response 对象，用于设置响应类型和发送文件。
		@Res() res: Response
	) {
		// 设置响应类型为 HTML。
		// attachment 会让浏览器将响应作为文件下载，而不是直接展示页面。
		res
			.type('html')

			// 设置下载文件名，方便用户保存和识别报告。
			.attachment(`analysis-${reportId}.html`)

			// 调用分析服务生成完整 HTML 报告，并发送给客户端。
			.send(await this.analyses.export(id, reportId))
	}

	@Get('sessions/:id/reports/:reportId/csv')
	async csv(
		@Param('id') id: string,
		@Param('reportId') reportId: string,
		@Res() res: Response
	) {
		const report = await this.analyses.findReport(id, reportId)
		res
			.type('text/csv')
			.attachment(`result-${reportId}.csv`)
			.send(resultCsv(report.table))
	}
	@Post('images')
	@UseInterceptors(
		FileInterceptor('file', { limits: { fileSize: 5 * 1024 * 1024, files: 1 } })
	)
	async image(
		@UploadedFile() file: Express.Multer.File,
		@Body('mode') rawMode: string
	) {
		if (!file) throw new Error('请选择图片')
		return this.multimodal.inspect(
			file.buffer,
			file.mimetype,
			Mode.parse(rawMode),
			AbortSignal.timeout(65000)
		)
	}
	@Post('images/compare')
	async compare(@Body() body: unknown) {
		const data = z
			.object({ datasetId: z.uuid(), facts: z.unknown() })
			.parse(body)
		return this.multimodal.compare(
			data.datasetId,
			data.facts,
			AbortSignal.timeout(30000)
		)
	}
	/**
	 * 将分析报告转换为语音流。
	 * 报告会先按句子切分，再逐句调用 TTS 服务，
	 * 生成音频地址后通过流式响应返回给前端，实现边生成边播放。
	 */
	@Post('sessions/:id/reports/:reportId/speech')
	async speak(
		// 获取分析会话 ID，用于确认报告所属会话。
		@Param('id') id: string,

		// 获取需要转语音的报告 ID。
		@Param('reportId') reportId: string,

		// 获取 Response 对象，用于发送流式响应。
		@Res() response: Response
	) {
		// 根据会话 ID 和报告 ID 查询对应分析报告。
		const report = await this.analyses.findReport(id, reportId)

		// 按中文标点和换行符切分回答内容。
		// 每句话单独合成，可以让前端更快开始播放。
		const sentences = report.answer.match(/[^。！？\n]+[。！？]?/g) || [
			report.answer
		]

		// 建立流式响应连接，逐句返回生成好的音频地址。
		await streamResponse(response, async (signal, send) => {
			// 音频片段序号，用于前端保持播放顺序。
			let seq = 0

			// 限制最多合成前 8 句话，避免一次报告生成过长音频。
			for (const sentence of sentences.slice(0, 8)) {
				// 如果用户取消播放或关闭连接，立即终止任务。
				signal.throwIfAborted()

				// 调用 TTS 服务生成当前句子的音频地址。
				// 限制单句最多 500 个字符，避免单次合成过长。
				const audioUrl = await this.multimodal.speak(
					[...sentence].slice(0, 500).join(''),
					signal
				)

				// 将当前音频片段推送给客户端。
				// 前端收到后即可加入播放队列。
				send('audio', {
					seq: seq++,
					audioUrl
				})
			}

			// 所有句子处理完成，通知客户端结束。
			send('done', {})
		})
	}
}

/**
 * 创建 NDJSON 流式响应。
 * 服务端逐条发送执行进度和结果；
 * 当浏览器主动关闭连接时取消当前任务，并忽略已经产生的迟到输出。
 */
async function streamResponse(
	// Express Response 对象，用于写入流式数据。
	response: Response,

	// 执行实际业务逻辑的函数。
	// signal 用于取消任务；
	// send 用于向客户端推送事件。
	run: (
		signal: AbortSignal,
		send: (event: string, data: unknown) => void
	) => Promise<void>
) {
	// 创建取消控制器，用于统一管理：
	// 用户主动关闭连接、超时取消等场景。
	const controller = new AbortController()

	// 设置最长执行时间 150 秒，防止任务长期占用资源。
	const timer = setTimeout(
		() => controller.abort(new Error('本轮超过 150 秒，请重试或缩小问题范围')),
		150000
	)

	// 浏览器关闭连接时触发。
	const close = () => {
		// 如果响应还没有正常结束，说明用户中途退出。
		if (!response.writableEnded)
			controller.abort(new DOMException('用户停止本轮', 'AbortError'))
	}

	// 监听客户端断开事件。
	response.on('close', close)

	// 设置响应格式为 NDJSON：
	// 每一行都是一个独立 JSON 对象，方便前端实时解析。
	response.type('application/x-ndjson').setHeader('Cache-Control', 'no-cache')

	// 立即发送响应头，让浏览器开始接收流。
	response.flushHeaders()

	// 封装统一发送方法。
	const send = (event: string, data: unknown) => {
		// 只有任务仍有效且连接未断开时才发送。
		// 避免任务取消后产生无效输出。
		if (!controller.signal.aborted && !response.destroyed)
			response.write(
				JSON.stringify({
					event,
					data
				}) + '\n'
			)
	}

	try {
		// 执行业务流程，并传入取消信号和发送函数。
		await run(controller.signal, send)
	} catch (error) {
		// 如果连接仍然存在，将异常转换为错误事件返回前端。
		if (!response.destroyed)
			response.write(
				JSON.stringify({
					event: 'error',
					data: {
						message: (error as Error).message
					}
				}) + '\n'
			)
	} finally {
		// 清理超时定时器。
		clearTimeout(timer)

		// 移除关闭监听，避免内存泄漏。
		response.off('close', close)

		// 结束 HTTP 响应。
		response.end()
	}
}
