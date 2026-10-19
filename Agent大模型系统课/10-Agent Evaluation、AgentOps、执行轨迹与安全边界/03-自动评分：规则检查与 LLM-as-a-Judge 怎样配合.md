# 03-自动评分：规则检查与 LLM-as-a-Judge 怎样配合

上一小节，咱们准备好了评测数据集。每道题都有用户问题、测试资料和参考结果。

接下来，就可以让 Agent 做题了。

还是这道题：

> 帮我查一下订单 A1024 的实付金额。
>

测试资料里，这笔订单的实付金额是 **3500 元**。

假设 Agent 回答：

> 订单 A1024 实付 3500 元，退款已批准，今天一定到账。
>

这句话里面有正确的金额，但后半句显然有问题。用户只查询金额，哪里来的退款批准和到账承诺呢？

所以，程序只检查回答里有没有 `3500`，远远不够。

这一节，咱们就来实现：**Agent 执行完以后，由程序检查查询过程，再评阅回答内容，最后给出通过或未通过的结果。**

## 规则检查与 LLM-as-a-Judge
先看查询过程。咱们把订单查询封装成一个叫 `get_order` 的 Tool，调用时需要提供订单号。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013183716058.png)![](./img/sudy2isl53yoaueh-1.png)

那么假设，用户问的是："帮我查一下订单 A1024 的实付金额。"

那么，正确的工具调用应该是：

```javascript
get_order({ orderId: 'A1024' })
```

Agent 执行以后，评测程序就可以检查：

+ 是否调用了 `get_order`
+ 传入的订单号是否为 `A1024`
+ 返回的订单是否属于当前用户，实付金额是否为 3500 元

这些信息都有明确字段，程序可以直接比较。这种把检查要求写成代码的方式，就是 **规则检查**。

![](./img/sudy2isl53yoaueh-2.png)![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E6%B5%81%E7%A8%8B%E4%BF%A1%E6%81%AF%E5%9B%BE.png)

工具查询检查通过以后，还需要继续检查最终回答。

比如，工具已经返回了 3500 元，Agent 却只说“已经查到了”，用户依然不知道金额。或者就像前面所说的一样，它回答了正确金额，又额外承诺 “退款今天一定到账”。

所以，接下来还需要评阅：**Agent 有没有把查到的信息准确、完整地告诉用户，有没有增加缺乏依据的内容。** 咱们可以把用户问题、查询结果和最终回答交给大模型，让它按照给定的标准评分，并说明原因。

这种使用大模型担任评审的方式，就叫作 **LLM-as-a-Judge，也就是使用大模型担任评审，给待评结果打分并说明原因。**。

对应的[官方介绍在这里](https://docs.langchain.com/langsmith/llm-as-judge)。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013185813632.png)![](./img/sudy2isl53yoaueh-3.png)

这里有两个不同的工作：答题模型负责查询并回答用户；评分模型负责检查这份回答。它们可以使用同一个模型服务，但各自使用独立的 Prompt 和消息。

所以，本节的分工可以直接记成：

+ 规则检查：工具有没有查对，返回数据是否符合要求？
+ 模型评分（`LLM-as-a-Judge`）：回答有没有说清金额，有没有说错或额外编造？

那么接下来，用一次完整运行一下代码把这两步连起来看看。

## 查看运行结果
本节代码在 `Code/Node/第十章/03-automatic-scoring`，会读取上一节 `02-evaluation-dataset/data` 中的订单金额样本。所以得保留这两个相邻目录。

创建 `.env` 文件：

```plain
DEEPSEEK_API_KEY=你的_API_Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
JUDGE_MODEL=deepseek-flash
```

`DEEPSEEK_MODEL` 负责答题，`JUDGE_MODEL` 负责评分。

执行：

```bash
npm run demo
```

先看下完整的打印结果：

![](./img/sudy2isl53yoaueh-4.png)![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013190540254.png)

运行时，先观察第一部分“真实 Agent 的执行结果”。

程序会先让 Agent 查询 A1024，再依次打印：回答、规则检查、回答评分：分数、理由、最终结果：是否通过 等等的

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013190704115.png)![](./img/sudy2isl53yoaueh-5.png)

**咱们要观察的是：程序有没有拿出具体依据，解释这份回答为什么通过或者未通过。** 

后面还有四份人工构造的对照材料，用来检查评分器本身，稍后再看。完整结果会保存到 `outputs/latest.json`。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013190749791.png)![](./img/sudy2isl53yoaueh-6.png)

## 从 main() 看一次评分怎样完成
### 先拿到 Agent 的实际执行结果
`npm run demo` 会执行 `src/main.ts` 中的 `main()`。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013191244408.png)![](./img/sudy2isl53yoaueh-7.png)

`main()` 先调用 `src/sample.ts` 中的 `loadCase()`，取出上一节的 `order-amount` 样本和对应订单资料：

```typescript
// 加载本次评测案例：
// datasetVersion：当前测试数据集版本；
// sample：用户问题、期望行为等测试样本；
// fixture：测试所需要的模拟业务数据。
const { datasetVersion, sample, fixture } = loadCase()
```

`sample` 保存题目和参考结果，`fixture` 保存工具可以查询的业务资料。然后，把本次答题需要的内容交给 Agent：

```typescript
// 执行一次真实 Agent 请求。
// 使用 structuredClone 创建独立副本，避免 Agent 执行过程中修改测试原始数据
const run = await agent.run(
  structuredClone(sample.inputs),
  structuredClone(fixture.orders)
)
```

这里的 `agent` 是从 NestJS 取得的 `AgentService`。传入的两部分分别是用户问题与身份、供工具查询的订单副本

打开 `src/agent.service.ts`，找到 `run()`。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013191533603.png)![](./img/sudy2isl53yoaueh-8.png)

里面沿用前面学过的 Tool Calling 流程：

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/%E5%B7%A5%E5%85%B7%E8%B0%83%E7%94%A8%E6%B5%81%E7%A8%8B%E5%9B%BE%E8%A7%A3.png)![](./img/sudy2isl53yoaueh-9.png)

只不过咱们这里多做的一件事：**应用在执行工具时，把参数和实际返回结果记录下来，和最终回答一起返回。**

因此，`main()` 拿到的 `run` 中，既有 `answer`，也有 `toolCalls`。

如果这次正确查询了 A1024，内容可以简化成下面这样：

```typescript
{
  answer: '订单 A1024 的实付金额是 3500 元。',
  toolCalls: [{
    name: 'get_order',
    args: { orderId: 'A1024' },
    result: {
      found: true,
      order: {
        tenantId: 'bluewhale',
        userId: 'user-001',
        orderId: 'A1024',
        product: '咖啡机',
        paidAmountYuan: 3500
      }
    }
  }]
}
```

其中，工具记录由应用保存，模型只负责提出调用和生成回答。评测时，程序就能根据实际记录检查它有没有查对订单。

### 拿到结果以后，先检查工具有没有查对
回到 `main()`，紧接着执行的是：

```typescript
// 使用评分器检查 Agent 的执行过程和最终结果。
const evaluation = await evaluator.evaluate(sample, run)
```

这次把 `sample` 和 `run` 放在一起：前者保存检查要求，后者保存实际执行结果。

`evaluator` 对应 `src/evaluation.service.ts` 中的 `EvaluationService`。

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013192146037.png)![](./img/sudy2isl53yoaueh-10.png)

进入 `evaluate()`，程序先调用：

```typescript
// 第一阶段：执行规则检查。
//
// 例如检查：
// - 是否调用了正确 Tool；
// - Tool 参数是否正确；
// - 查询结果是否属于当前用户；
// - 必需字段是否满足要求。
const rules = checkRules(sample, run)
```

`checkRules()` 负责检查三件事：

| 检查项 | 怎样判断 |
| --- | --- |
| 查询目标 | 实际调用了 `get_order`，订单号为 A1024 |
| 订单数据 | 查询成功，返回的租户、用户、订单号和金额与测试要求一致 |
| 回答非空 | 模型确实返回了待检查的回答文字 |


比如，查询目标的判断代码是：

```typescript
/**
 * 检查工具调用行为是否正确。
 *
 * 要求：
 * 1. 至少调用过一次工具；
 * 2. 所有调用都必须是 get_order；
 * 3. 参数必须符合 Schema；
 * 4. 查询订单号必须等于目标订单。
 */
const correctQuery = calls.length > 0 && calls.every((call) => {
  const args = OrderArgs.safeParse(call.args)
  return call.name === 'get_order'
    && args.success
    && args.data.orderId === facts.orderId
})
```

这里的 `calls` 来自 `run.toolCalls`；`facts` 来自样本中的参考事实，其中 `orderId` 是 A1024。`OrderArgs` 用 Zod 检查调用参数里是否有合法的订单号。

咱们要求这道查询题只访问指定订单，因此逐条检查调用。即使最后回答恰好写了“3500 元”，查询过其他订单也会被发现。

检查完以后，每项都会得到 `passed` 和 `reason`，分别表示是否通过、为什么。

回到 `evaluate()`，只要有一项未通过，就返回 `failed`，并跳过本次模型评分：

```typescript
// 如果存在任何硬规则失败，
// 直接判定本次评测失败。
//
// 这里不会调用评分模型，
// 避免出现：
// "工具调用错误，但模型觉得回答不错，所以给高分"
// 这种不符合业务要求的情况。
if (rules.some((rule) => !rule.passed)) return {
  status: 'failed',
  rules,
  judge: { status: 'skipped', reason: '必需规则未通过，跳过模型评分。' }
}
```

### 工具检查通过，再评阅回答内容
如果三项规则都通过，`evaluate()` 才会继续执行：

```typescript
// 第二阶段：
// 将 Agent 执行结果整理成评分模型需要的输入格式。
//
// 输入通常包含：
// - 用户问题；
// - Agent 最终回答；
// - Tool 调用记录；
// - 业务上下文。
const input = buildJudgeInput(sample, run)
```

`buildJudgeInput()` 为评分模型准备四份资料：

```typescript
return {
  // 用户提出的问题。
  question: sample.inputs.turns[0],
  // 测试案例中的参考输出。
  // 只提供业务事实，不包含评分结果。
  reference: sample.referenceOutputs.turns[0],
  // 提取 Agent 执行过程中所有工具返回值。
  // 不传递完整 Tool 调用过程，只关注工具产生的数据。
  toolResults: run.toolCalls.map((call) => call.result),
  // Agent 最终回复内容。
  answer: run.answer
}
```

`question` 告诉评分模型用户问了什么；`reference` 告诉它正确事实和检查要求；`toolResults` 是本次查到的资料；`answer` 就是要打分的回答。

上一节的时候咱们说过：参考答案不能提前交给答题模型。现在答题已经结束，参考答案可以交给评分模型，用来核对实际回答。

不过，只提供这些资料还不够，还要说明怎样评分。

因此，咱们构建了一套类似于这样的标准：

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013192557427.png)![](./img/sudy2isl53yoaueh-11.png)

这样看字有点长，通过一个表格来看下：

| 分数 | 本例的判断标准 |
| --- | --- |
| 2 分 | 金额与单位明确、事实正确，也没有增加无依据的业务结论 |
| 1 分 | 没有说错或乱承诺，但遗漏了金额、单位等必要信息 |
| 0 分 | 订单或金额错误、前后矛盾，或者无依据地承诺退款结果 |
| `null` | 依据不足或相互冲突，暂时无法判断，需要人工核对 |


这套分数是咱们自己定义的。

接着，`evaluate()` 发起一次独立的模型请求：

```typescript
// 调用评分模型进行语义判断。
//
// 与普通聊天不同，这里要求模型严格返回 JSON，
// 方便程序解析评分结果。
const response = await this.model.complete('judge', {
  messages: [
    { role: 'system', content: JUDGE_PROMPT },
    { role: 'user', content: JSON.stringify(input) }
  ],
  response_format: { type: 'json_object' }
})
```

这里的 `this.model` 是 `src/model.service.ts` 中的 `ModelService`。传入 `'judge'`，它就使用 `JUDGE_MODEL` 配置的模型发起请求。

模型返回以后，`evaluate()` 会把返回的 JSON 文本和待评回答交给当前文件中的 `parseJudge()`。它用 Zod 检查分数、理由和引用格式，并确认引用的文字确实出现在待评回答里。

例如，针对开头那句多余的退款承诺，咱们期望评分结果类似于：

```json
{
  "score": 0,
  "reason": "金额正确，但订单查询结果没有提供退款批准或到账依据。",
  "quotes": ["退款已批准，今天一定到账"]
}
```

最后，`evaluate()` 把规则和回答评分合起来：规则全部通过，并且回答得到 2 分，才返回 `passed`；0 或 1 分返回 `failed`；`null` 返回 `needs_review`。

结果返回 `main()` 后，`printResult()` 把各项规则、分数和原因打印出来，这就对应了刚才终端中的第一部分。

```javascript
// 使用评分器检查 Agent 的执行过程和最终结果。
const evaluation = await evaluator.evaluate(sample, run)

// 输出真实 Agent 的执行结果和评分结果。
printResult('一、真实 Agent 的执行结果', run, evaluation)
```

## 怎样判断评分模型有没有评对
看到这里，可能有同学会问：让模型检查模型，它自己会不会判断错啊？

答案是：**会**。

就像前几天有同学在群里问我，模型回答错误的问题：

![](03-%E8%87%AA%E5%8A%A8%E8%AF%84%E5%88%86%EF%BC%9A%E8%A7%84%E5%88%99%E6%A3%80%E6%9F%A5%E4%B8%8E%20LLM-as-a-Judge%20%E6%80%8E%E6%A0%B7%E9%85%8D%E5%90%88.assets/image-20261013192938975.png)![](./img/sudy2isl53yoaueh-12.png)

现在的情况下，我们要知道模型永远无法保证它自己回复的信息是 100% 正确的。

所以，咱们还需要拿几份已经人工判断过的材料，核对评分器。

这一节，咱们让 Agent 实际查询了一次订单，再根据工具记录和最终回答完成评分。

能够直接比较的工具参数和业务数据，由代码检查；需要理解含义的回答内容，交给评分模型按照明确标准评阅。最后再用人工判断过的材料，检查评分是否合理。

这样，看到“未通过”时，咱们能知道是哪项要求没有满足了～

> 更新：2026-10-13 19:44 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/sudy2isl53yoaueh>
