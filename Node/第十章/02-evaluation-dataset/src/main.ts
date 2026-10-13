import 'reflect-metadata'
import { Module } from '@nestjs/common'
import { NestFactory } from '@nestjs/core'
import { DatasetService } from './dataset.service.js'

@Module({ providers: [DatasetService] })
class AppModule {}

/** 启动 NestJS 命令行案例，观察样本、参考答案以及每次运行是否拥有独立的测试环境。 */
async function main() {
	// 创建 NestJS 应用上下文。
	// 与启动完整 HTTP 服务不同，这里只初始化依赖注入容器，方便在命令行中直接调用 Service。
	const app = await NestFactory.createApplicationContext(AppModule, {
		logger: false // 关闭 Nest 默认日志，避免干扰案例输出。
	})

	try {
		// 从 Nest 容器中获取数据集服务。
		// DatasetService 负责管理测试样本、参考答案以及运行环境的初始化。
		const service = app.get(DatasetService)

		// 准备一个名为 order-amount 的测试任务。
		// prepare 会创建本次 Agent 运行所需的输入数据和独立环境副本。
		const run = service.prepare('order-amount')

		// 输出当前使用的数据集版本，方便确认测试数据是否发生变化。
		console.log(`数据集：${run.datasetVersion}`)

		// 检查当前注册的样本数量。
		// 这里用于确认数据集结构是否正确加载。
		console.log(`结构检查通过：${service.list().length} 条样本`)

		// 打印当前所有可用测试样本。
		console.table(service.list())

		console.log('\n样本 order-amount：交给 Agent 的输入')

		// 输出 Agent 实际接收到的输入内容。
		// 注意：这里展示的是给 Agent 的任务信息，而不是评分时使用的参考答案。
		console.log(JSON.stringify(run.inputs, null, 2))

		// 说明订单数据存放位置。
		// 后续 Agent 调用查询工具时，会从当前隔离环境中读取这些数据。
		console.log('订单数据：保存在独立 environment 中，后续供查询工具读取。')

		console.log('\n留给评分程序的参考结果')

		// 输出标准答案。
		// 评分程序会将 Agent 最终输出与 reference 进行比较，判断 Agent 是否完成任务。
		console.log(JSON.stringify(service.getReference('order-amount'), null, 2))

		// 检查运行参数中是否显式携带参考答案。
		// 真实 Agent 测试通常不会把 referenceOutputs 暴露给 Agent，
		// 避免模型直接读取答案导致测试失真。
		console.log(
			'运行参数是否包含 referenceOutputs：',
			Object.hasOwn(run, 'referenceOutputs')
		)

		console.log('\n环境隔离实验：程序直接修改第一份副本，不调用 Agent。')

		// 查看当前运行环境中第一条订单的数据。
		// 这里模拟一次测试过程中对环境状态的修改。
		console.log(
			'第一份副本的 A1024 实付金额：',
			run.environment.orders[0].paidAmountYuan
		)

		// 修改当前运行环境中的订单金额。
		// 这个修改只影响本次 prepare 创建出来的环境副本。
		run.environment.orders[0].paidAmountYuan = 1

		// 模拟 Agent 运行过程中产生的上下文消息。
		run.environment.messages.push({
			role: 'user',
			content: '上一轮留下的测试消息'
		})

		// 模拟 Agent 运行过程中产生的长期记忆。
		run.environment.memories.note = '上一轮留下的测试记忆'

		// 输出修改后的订单金额。
		console.log(
			'修改第一份副本以后：',
			run.environment.orders[0].paidAmountYuan
		)

		// 再次准备同一个任务。
		// 如果环境隔离设计正确，这里应该重新创建一份干净副本，
		// 不应该受到上一次修改的影响。
		const next = service.prepare('order-amount')

		// 验证新的运行环境是否恢复初始数据。
		console.log(
			'重新准备后，A1024 实付金额：',
			next.environment.orders[0].paidAmountYuan
		)

		// 验证历史消息是否被清空。
		console.log('重新准备后的消息数量：', next.environment.messages.length)

		// 验证历史记忆是否被清空。
		console.log(
			'重新准备后的记忆数量：',
			Object.keys(next.environment.memories).length
		)

		// 当前案例只验证测试基础设施：
		// 1. 数据集是否正确加载；
		// 2. Agent 输入是否正确生成；
		// 3. reference 是否独立保存；
		// 4. 每次运行环境是否隔离。
		//
		// 此处没有真正调用模型，也没有执行 Agent 任务，因此不会产生 Agent 评分结果。
		console.log(
			'\n本次只检查样本与环境准备，没有调用模型，也没有计算 Agent 得分。'
		)
	} finally {
		// 无论测试成功还是失败，都关闭 Nest 应用上下文。
		// 防止数据库连接、文件句柄等资源没有释放。
		await app.close()
	}
}

main().catch((error: unknown) => {
	console.error(error instanceof Error ? error.message : error)
	process.exitCode = 1
})
