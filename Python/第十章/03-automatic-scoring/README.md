# 03 自动评分 · Python 版

让 Agent 查询订单并答题，再由代码检查工具记录、由独立的评分请求评阅回答。保留原例的评分标准、四份人工对照材料及结果状态；读取相邻 `02-evaluation-dataset/data` 中的 Python 数据，不依赖另一语言目录。

## 离线验证

Python 3.11+，在本目录执行：

```bash
uv sync --locked
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest -v test_scoring
```

测试使用 Fake Model 和本地 HTTP 服务，验证完整的 Tool Calling、规则检查、Judge 请求与报告保存；无需 API Key，不调用收费接口。Mock 的分数是测试设定的，不代表真实模型的评分能力。

## 调用真实模型

程序只读取进程环境变量，不自动读取环境文件。先向当前进程加载 `DEEPSEEK_API_KEY`，可选变量如下：

| 变量 | 默认值或作用 |
| --- | --- |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`，程序追加 `/chat/completions` |
| `DEEPSEEK_MODEL` | `deepseek-flash`，用于答题 |
| `JUDGE_MODEL` | 未设置时沿用答题模型，用于独立评分 |

如果你已经有本节自己的、符合 Shell 语法的 `.env`，可自行加载后运行：

```bash
set -a
source .env
set +a
uv run --locked python main.py
```

没有环境文件时，可以直接在终端向进程注入变量，再执行 `uv run --locked python main.py`。不需要创建或复制任何环境模板。`source` 仅适合合法的 Shell 赋值文件；出现 `command not found` 时，先检查用户自己的配置语法。

此命令会产生模型费用。正常一次运行包含 Agent 查询、回答、评分，以及三次人工材料的 Judge 评分；查错订单的材料会被规则拦截，不调用 Judge。每次请求超时为 45 秒，不自动重试；Agent 最多 4 轮模型调用、6 次工具调用。

## 文件职责与观察点

- `sample.py`：读取上一节 JSON，校验唯一样本、订单身份与参考金额。
- `model_service.py`：标准库 HTTP 请求，分开选择答题模型与评分模型。
- `agent_service.py`：只读订单工具与 Tool Calling 循环，保存真实参数、返回结果。
- `evaluation_service.py`：本节重点；先检查查询目标、订单数据和非空回答，再按 0/1/2/`null` 评分，校验理由及引用原文。
- `examples.py`：四份人工对照材料，核对评分器是否符合人工预期。
- `main.py`：串起流程并保存 `outputs/latest.json`；再次运行会覆盖。
- `test_scoring.py`：离线行为、错误边界、本地 HTTP 联调与报告验证。

关键预期是：查错订单即使答对金额也应 `failed`，并跳过模型评分；查询记录正确的同义改写交给 Judge 判断，2 分为 `passed`、0/1 分为 `failed`、`null` 为 `needs_review`。Agent 执行异常记录为 `agent_error`，评分异常记录为 `evaluation_error`，不会伪装成 0 分。

规则只核对过程和数据，不能证明回答内容正确；引用校验也不能保证 Judge 判断正确。四份人工材料不计入真实 Agent 成功率，本节不计算整套数据集通过率，也不接入 LangSmith。

只使用 Python 标准库，不需要 FastAPI、数据库或额外 SDK。请求字段遵循 [DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。环境文件和 Git 忽略配置不属于本节复制与测试范围。
