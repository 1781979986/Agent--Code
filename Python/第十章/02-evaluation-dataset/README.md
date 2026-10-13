# 02 评测数据集 · Python 版

演示评测前的数据准备：读取 7 条样本，将 Agent 输入与评分参考分开，每次运行创建独立的业务资料、消息与记忆副本。不启动 HTTP 服务，不调用模型，也不计算 Agent 得分。

## 运行

Python 3.11+，在本目录执行：

```bash
uv sync --locked
uv run --locked python main.py
```

只使用标准库，也可直接执行 `python3 main.py`。不需要 API Key、环境文件、Docker 或数据库。

## 文件与观察点

- `data/dataset.json`：原课程的 7 条样本，保留身份、单轮/多轮输入、参考事实及检查要求。
- `data/fixtures.json`：合成订单和退款规则，每次准备运行时复制，原件不修改。
- `dataset_service.py`：严格校验字段、重复 ID、轮次与资料引用；`prepare()` 返回运行副本，`get_reference()` 单独返回评分参考。
- `main.py`：列出样本、打印输入与参考，再修改第一份副本，观察第二次是否重新从相同数据开始。
- `test_dataset.py`：校验结构、业务边界、参考分离及副本隔离。

预期显示 `结构检查通过：7 条样本`。第一份订单金额从 **3500** 改成 **1** 后，重新准备仍为 **3500**，消息和记忆数量都为 **0**；运行参数不含 `referenceOutputs`。

`inputs.turns` 是按顺序发送的用户消息。后续执行多轮样本时，应保留同一次运行中实际产生的模型回答与工具结果，再发送下一轮；不能用参考答案填充对话。

`environment` 由应用保存，订单与规则供工具读取，不是全量发给模型的 Prompt。`referenceOutputs` 只由评分程序读取。结构检查不代替人工核对业务参考；本节的内存副本也不等于数据库或持久化记忆的完整隔离方案。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest -v test_dataset
```

测试完全离线；不读取、创建或修改环境文件，也不改动 Git 忽略配置。
