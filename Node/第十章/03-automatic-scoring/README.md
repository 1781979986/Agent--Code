# 自动评分：规则检查与 LLM-as-a-Judge

本例使用 NestJS 命令行应用，完成一次真实订单查询及评分，再用四份人工构造的材料核对评分器。复用相邻 `02-evaluation-dataset/data` 下的 JSON，不需要安装或启动 02。

## 运行

Node.js 22.20 或以上版本。在本目录执行 `npm install`，然后自行在本目录的 `.env` 中配置：

```dotenv
DEEPSEEK_API_KEY=你的_API_Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
JUDGE_MODEL=deepseek-flash
```

执行：

```bash
npm run demo
```

会调用真实 API 并计费。正常的一次 Tool Call 需要两次答题模型调用，之后进行一次回答评分；四份教学对照材料中，查错订单的一份被规则拦下，其余三份调用评分模型。异常模型行为可能改变调用次数，Agent 最多调用模型 4 轮、工具 6 次，每次 API 请求超时 45 秒，不自动重试。

终端依次展示真实回答、规则检查、回答评分、理由、引用原文、最终状态，以及教学材料与人工标准是否一致。`outputs/latest.json` 保存本次结果，再运行会覆盖该文件。教学材料不计入真实 Agent 成功率；本例也不计算全数据集成功率。

`JUDGE_MODEL` 使用同一 DeepSeek 兼容接口，可与答题模型不同；默认回退到 `DEEPSEEK_MODEL`。本例用相同模型演示不同职责，不把自评结果视为客观保证。正式使用前应拿人工标注材料校准。

截至 2026-10-13，[DeepSeek 官方模型说明](https://api-docs.deepseek.com/quick_start/pricing)推荐 Flash 请求名 `deepseek-flash`；旧 `deepseek-v4-flash` 名称的请求已转到新的 Flash 模型。本例使用当前名称，旧章节不随本例改动。

## 文件与入口

- `src/main.ts`：加载样本、执行 Agent、评分、运行对照、保存结果。
- `src/sample.ts`：读取 02 的订单金额样本并核对参考金额与业务资料。
- `src/agent.service.ts`：只读订单工具与最小 Tool Calling 循环，记录真实工具结果。
- `src/evaluation.service.ts`：规则检查、评分 Prompt、JSON 校验与最终结果合并。
- `src/model.service.ts`：独立的答题与评分模型请求。
- `src/examples.ts`：明确标注的人工对照材料。其人工分数不会发送给 Judge。

## 评分约定

这里只针对 `order-amount` 样本：查询目标、订单数据和回答非空为必需条件。规则通过后，Judge 根据问题、参考、工具结果与回答给出 0/1/2 分或 `null`。2 分通过，0/1 分未通过，`null` 需要人工核对。这是本例策略，不是框架标准。

本例要求仅查询指定订单，允许多次查询同一个订单，不固定完整工具顺序。工具轨迹来自应用记录；确定性检查只证明配置的条件成立，不证明答案正确。Judge 引用必须出现在回答中，但引用存在也不证明其解释正确。

状态：

| 状态 | 含义 |
| --- | --- |
| `passed` | 规则与本例回答标准通过 |
| `failed` | 有规则未通过，或回答评分为 0/1 |
| `needs_review` | Judge 明确表示依据不足，无法判断 |
| `evaluation_error` | 评分请求、JSON、字段或引用校验异常 |
| `agent_error` | 受测 Agent 没有正常结束 |

评分侧出错时保留错误原因，不填充 0 分。回答中的指令只作为待评数据；Prompt 约束不等于完全防止评分模型被诱导，仍需人工抽查。安全攻击测试在后续小节展开。

## 验证

```bash
npm run check
npm test
```

测试使用固定模型桩与本地 HTTP 模拟接口，不读取 `.env`，不需要 Key、不访问付费模型。它们验证评分程序、工具边界和 SDK 请求链路，不能据此确认真实 Judge 的评分质量。

常见问题：缺少 Key 时按提示配置；401/429 检查账号与限额；出现 `evaluation_error` 查看评分理由、字段或网络错误。评分与人工标准不一致时保留结果并核对原文，不反复重试到“满意分数”。

## 官方依据

- [LangSmith 评测方式](https://docs.langchain.com/langsmith/evaluation-concepts#evaluation-techniques)
- [LLM-as-a-Judge 配置与标准](https://docs.langchain.com/langsmith/llm-as-judge)
- [NestJS 独立应用](https://docs.nestjs.com/standalone-applications)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)

本例直接实现本地评分函数，无需 LangSmith 账号。
