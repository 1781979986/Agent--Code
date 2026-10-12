# 09 AI 数据分析 Agent · Python 版

独立的销售分析工作台：上传 Excel / CSV、确认字段，文字或语音连续提问，查看趋势、商品贡献、图表和真实查询依据；支持截图核对、历史恢复、HTML / CSV 导出与逐句朗读。

后端使用 **FastAPI + LangGraph + DuckDB**，前端保留 **Vue 3 + ECharts**。不读取其他语言目录，也不依赖相邻 Python 小节。原件、数据集、会话、报告均保存在本项目自己的 `data/`，查询镜像使用独立标签。

## 文件职责

- `server/app_controller.py`：上传、确认导入、分析、截图、导出与朗读接口；分析进度使用 NDJSON。
- `server/dataset_service.py`、`table_parser.py`：工作表预览、五字段映射、质量检查、Checksum 与不可变数据版本。
- `server/workflow.py`、`model.py`：LangGraph 决策 → 受限查询 → 一次修正 / 报告；AI SQL 用标准查询独立复核口径。
- `server/analysis.py`：根据真实结果计算连续下降、降幅贡献与图表数据，不让模型填写最终数字。
- `server/analysis_service.py`、`storage.py`：固定会话的数据版本、继承追问条件、保存已完成报告与历史。
- `server/report.py`：HTML 与 CSV 导出；标准库绘制离线 SVG，不需要运行 Node 后端。
- `server/sandbox.py`、`runtime/`：只读临时容器、SQL AST 白名单、取消、超时和资源清理。
- `server/multimodal_service.py`、`voice/`：截图提取与同口径复算、实时 ASR、逐句 TTS。
- `web/`：独立前端及依赖；`samples/`：原课程的表格与看板；`scripts/` / `test/`：构建、样本生成和验证。

## 先做离线验证

需要 Python 3.11+、uv；前端需要 Node.js 22.20+。在本节目录执行：

```bash
uv sync --locked
PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false uv run --locked python -m unittest discover -s test -p 'test_*.py' -v

cd web
npm ci
npm test
npm run build
cd ..
```

测试不需要 Key、不调用云端，也不需要 Docker。真实表格导入、DuckDB 查询与会话保存只在临时目录进行；Docker 参数及清理由 Fake 验证，不冒充容器隔离已验证。HTTP / ASR 通信测试只启停自己创建的随机本机端口。

## 启动项目

先自行打开 Docker Desktop，然后在本节目录构建 **Python 版**查询镜像：

```bash
uv run --locked python scripts/build_sandbox.py
```

镜像标签为 `agent-course-analysis-python:chapter9-09`，不覆盖其他语言或小节的镜像。BuildKit 构建只发送固定运行器与带 Hash 的 DuckDB 依赖锁，不包含课程其他文件、数据或环境配置。首次构建需要访问 Docker Hub / PyPI；失败应先检查网络与 Docker 代理，不关闭查询运行时的隔离限制。

Replay 分析与内置看板截图不需要 API Key，但页面分析仍会进行**真实 Docker 查询**，并非硬编码金额。先启动后端：

```bash
uv run --locked python -m server.main
```

另开终端，进入本节 `web/`：

```bash
npm ci
npm run dev
```

访问 [http://localhost:5187](http://localhost:5187)，后端监听 `127.0.0.1:4317`。端口占用时，在**两个终端**导出相同的 `PORT` / `WEB_PORT` 后分别启动，例如 `export PORT=4318 WEB_PORT=5188`。前端代理与后端语音 Origin 白名单必须一致；只启用一个后端进程，不使用多 worker。

`samples/` 已包含 XLSX、CSV、质量问题文件和 `dashboard.png`，无需启动其他小节或安装数据库服务。可选 `uv run --locked python scripts/create_samples.py` 重建三个表格样本；不会自动更新看板截图，重建后文件 Checksum 可能变化。

## AI、图片和语音配置

服务只从进程环境读取 `DEEPSEEK_API_KEY`、可选 `DEEPSEEK_MODEL`（沿用原例默认 `deepseek-v4-flash`）与 `DEEPSEEK_BASE_URL`；百炼使用 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL` 和可选 `VISION_MODEL`（默认 `qwen3-vl-flash`）。Key、业务空间和地域须匹配，使用控制台提供的真实兼容接口地址。

Python 不自动读取 `.env`，前端 Vite 也已关闭自动环境文件加载。若你已有符合 shell 语法的 `.env`，在**后端终端**加载后启动；已经导出变量则只执行最后一行：

```bash
set -a
source .env
set +a
uv run --locked python -m server.main
```

自由提问和非内置截图使用 AI 模式。录音和朗读在 Replay / AI 两种模式中都调用真实百炼服务，需要 `qwen3-asr-flash-realtime` 与 `qwen3-tts-flash` 权限。密钥只留在后端；修改进程配置后需重启后端。此复制任务不创建、复制、检查或修改任何环境文件。

## 演示与标准结果

1. 上传 `sales-demo.xlsx`，选择“销售明细”，确认五字段映射；预览四条，实际导入 **18 行**。
2. 问“哪些区域连续两个月净销售额下降？”：华东 7 / 8 / 9 月为 **30000 → 25000 → 20000** 元。
3. 同一会话追问“只看华东，哪些商品贡献了主要降幅？”：咖啡机下降 **4000 元 / 80%**，键盘 **1000 元 / 20%**。
4. 问“9月全部区域净销售额是多少？”：**59000 元**；实付为 **60000 元**、退款 **1000 元**，口径不能混用。
5. 上传内置 `dashboard.png`，核对“未扣退款”再确认比较；原表同口径 **60000 元**，差值为零。
6. 查看图表、结果表、SQL、版本与原始结果，下载离线 HTML / CSV。刷新或重启后，在“分析记录”恢复会话。
7. 配置百炼后录音，说完结束、核对转写后确认分析；“朗读结果”逐句合成播放，“停止播放”取消后续请求并清空队列。播放被拦截时点击“继续播放”。
8. 上传 `sales-issues.xlsx`：问题行保留并标记，禁止分析；修正原表后重新上传，不自动删行或补零。

## 真实 Docker 验证与边界

Docker 已运行并成功构建镜像后执行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/verify.py
```

它验证真实导入、容器查询、追问、恢复、导出、截图口径和越权 SQL 拒绝；只使用临时数据与 Replay，不调用收费 API。缺镜像或 Docker 不可用时明确失败，不回退到宿主执行。离线测试也不能替代真实麦克风与云端联调。

每次查询仅挂载当前数据库的只读副本，禁网、非 root、只读根文件系统，限制 1 CPU、256 MiB 内存、64 个进程、10 秒与 256 KiB 输出。只开放 `sales` 的受限 SELECT，结果最多 100 行；取消会回收本次容器，清理失败会报告错误。

这是单用户本地销售分析项目，支持金额汇总、趋势、连续下降与商品贡献，不做预测或因果解释。空退款和缺失月份不补零。会话固定数据版本，保存完成报告与 `lastSpec`，没有 Checkpointer，不恢复服务重启打断的计算。分析/朗读流最长 150 秒；ASR 最长等待 45 秒，录音最多 30 秒。朗读沿用原例最多 8 句，每句最多 500 Unicode 字符；已播放声音不可撤回，取消不保证供应商立即停止计费。

依赖版本由 `uv.lock`、`web/package-lock.json` 和 `runtime/requirements.lock` 固定。真实模式会把必要问题、元信息、历史摘要、截图或音频发送到相应供应商，请先确认业务数据授权与供应商政策。

官方参考：[LangGraph](https://docs.langchain.com/oss/python/langgraph/graph-api)、[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[实时 ASR](https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-client-events)、[百炼 TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。
