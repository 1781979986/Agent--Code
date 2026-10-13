import 'reflect-metadata'
import { Module } from '@nestjs/common'
import { NestFactory } from '@nestjs/core'
import { mkdir, writeFile } from 'node:fs/promises'
import { AgentService } from './agent.service.js'
import {
	EvaluationService,
	RUBRIC_VERSION,
	type Evaluation
} from './evaluation.service.js'
import { ModelService } from './model.service.js'
import { loadCase, type AgentResult } from './sample.js'
import { teachingExamples } from './examples.js'

@Module({ providers: [ModelService, AgentService, EvaluationService] })
class AppModule {}

/** 展示规则、模型评分和最终结论，避免只留下一个总分。 */
function printResult(label: string, run: AgentResult, evaluation: Evaluation) {
	console.log(`\n${label}`)
	console.log(`回答：${run.answer || '未产生回答'}`)
	console.table(evaluation.rules)
	if (evaluation.judge.status === 'completed') {
		const { score, reason, quotes } = evaluation.judge.result
		console.log(`回答评分：${score === null ? '暂不能判断' : `${score}/2`}`)
		console.log(`评分理由：${reason}`)
		console.log(`引用原文：${quotes.join('；')}`)
	} else console.log(`模型评分：${evaluation.judge.reason}`)
	console.log(`最终结果：${evaluation.status}`)
}

/**
 * 先执行一次真实 Agent 流程：
 * 1. 加载测试案例和模拟数据；
 * 2. 调用 Agent 完成用户任务；
 * 3. 使用评分器对 Agent 输出进行自动评估。
 *
 * 随后使用人工提前准备好的教学样本，
 * 验证评分器的判断结果是否符合预期标准。
 */
async function main() {
	// 创建 Nest 应用上下文，不启动 HTTP 服务，只用于获取依赖注入的 Service。
	const app = await NestFactory.createApplicationContext(AppModule, {
		logger: false
	})

	try {
		// 从 Nest 容器中获取模型服务，用于调用答题模型和评分模型。
		const model = app.get(ModelService)

		// 检查模型相关配置是否完整，例如 API Key 是否存在。
		model.assertConfigured()

		// 加载本次评测案例：
		// datasetVersion：当前测试数据集版本；
		// sample：用户问题、期望行为等测试样本；
		// fixture：测试所需要的模拟业务数据。
		const { datasetVersion, sample, fixture } = loadCase()

		// 获取 Agent 执行服务，负责模拟真实用户请求流程。
		const agent = app.get(AgentService)

		// 获取评测服务，负责判断 Agent 输出是否符合要求。
		const evaluator = app.get(EvaluationService)

		console.log('本次会调用答题模型和评分模型，产生 API 费用。')
		console.log('使用模型：', model.settings())
		console.log(`用户问题：${sample.inputs.turns[0]}`)

		// 执行一次真实 Agent 请求。
		// 使用 structuredClone 创建独立副本，避免 Agent 执行过程中修改测试原始数据。
		const run = await agent.run(
			structuredClone(sample.inputs),
			structuredClone(fixture.orders)
		)

		// 使用评分器检查 Agent 的执行过程和最终结果。
		const evaluation = await evaluator.evaluate(sample, run)

		// 输出真实 Agent 的执行结果和评分结果。
		printResult('一、真实 Agent 的执行结果', run, evaluation)

		// 保存教学对照结果。
		// 这些数据不是 Agent 实际生成，而是人为设计的测试样本，
		// 用于验证评分器是否符合人工判断标准。
		const comparisons = []

		console.log(
			'\n二、下面四份是人为构造的评分对照材料，不是真实 Agent 的四次输出。'
		)

		// 遍历预先设计好的教学案例。
		for (const example of teachingExamples(sample, fixture.orders)) {
			// 使用同一个评分器评估人工构造的 Agent 输出。
			const result = await evaluator.evaluate(sample, example.run)

			// 打印该案例的执行结果和评分结果。
			printResult(example.name, example.run, result)

			// 检查评分器结果是否符合人工预期。
			//
			// 如果 expectedScore 为 null：
			// 表示该案例应该在规则检查阶段直接失败，
			// 不应该继续调用评分模型。
			//
			// 如果 expectedScore 有值：
			// 表示评分模型应该完成评分，并得到对应分数。
			const agreement =
				example.expectedScore === null
					? result.status === 'failed' && result.judge.status === 'skipped'
					: result.judge.status === 'completed' &&
						result.judge.result.score === example.expectedScore

			console.log(
				`人工预期：${example.expectedScore === null ? '规则不通过，不调用评分模型' : `${example.expectedScore}/2`}`
			)

			console.log(
				`人工核对：${agreement ? '与预期一致' : '存在分歧或评分未完成，请查看理由与原文'}`
			)

			// 保存当前教学案例、评分结果以及是否符合人工预期。
			comparisons.push({ ...example, evaluation: result, agreement })
		}

		// 创建输出目录，用于保存本次完整评测结果。
		const directory = new URL('../outputs/', import.meta.url)
		await mkdir(directory, { recursive: true })

		// 保存最终评测报告。
		// 内容包括：
		// - 测试时间；
		// - 数据集版本；
		// - 测试案例；
		// - 使用的模型信息；
		// - 真实 Agent 结果；
		// - 人工设计的评分对照结果。
		await writeFile(
			new URL('latest.json', directory),
			JSON.stringify(
				{
					createdAt: new Date().toISOString(),
					datasetVersion,
					fixtureId: fixture.id,
					caseId: sample.id,
					rubricVersion: RUBRIC_VERSION,
					models: model.settings(),

					// 真实 Agent 执行结果。
					actual: { run, evaluation },

					// 人工构造样本和评分器验证结果。
					comparisons
				},
				null,
				2
			)
		)

		console.log(
			'\n完整结果已保存到 outputs/latest.json。教学对照材料不计入 Agent 成功率。'
		)

		// 如果真实执行或者教学样本评测过程中出现错误，
		// 设置进程退出码，让 CI/CD 或自动化测试系统识别失败。
		if (
			[evaluation, ...comparisons.map((item) => item.evaluation)].some((item) =>
				['evaluation_error', 'agent_error'].includes(item.status)
			)
		)
			process.exitCode = 1
	} finally {
		// 无论成功还是失败，都关闭 Nest 应用上下文，释放资源。
		await app.close()
	}
}

main().catch((error: unknown) => {
	console.error(error instanceof Error ? error.message : String(error))
	process.exitCode = 1
})
