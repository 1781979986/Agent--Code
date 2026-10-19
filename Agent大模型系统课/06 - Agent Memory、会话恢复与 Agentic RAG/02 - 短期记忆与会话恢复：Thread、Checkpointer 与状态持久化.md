# 02 - 短期记忆与会话恢复：Thread、Checkpointer 与状态持久化

上一小节，咱们留下了一个问题：“用户第一次告诉 Agent 的信息，下一次提问时怎样才能继续使用？”

很多同学会觉得，只要使用同一个 `agent` 对象连续调用两次就可以了。

比如：

```javascript
await agent.invoke({
  messages: [{ role: 'user', content: '订单 A1024 的退款金额是 3000 元。' }]
})

await agent.invoke({
    messages: [{ role: 'user', content: '刚才说的是哪个订单？' }]
})
```

但是，这两次 `invoke()` 默认是两次独立调用。第二次只提交了“刚才说的是哪个订单”，第一轮的内容并没有再次传入。

也就是说：两次调用使用的虽然是同一个 `agent` ，模型也并不会因此自动拥有上一轮记忆。

那么怎么办呢？

最简单的办法就是，由 Agent 应用直接读取历史消息，再把整段历史消息重新拼到 `messages `里面。

但是，第五章已经学过，LangChain 的 Agent 内部其实是一张 LangGraph。除了 `messages`，Graph State 中还可能包含任务状态、摘要、上传文件和其他运行数据。

如果每个项目都手动加载、拼接和保存这些 State，代码就会很乱了。

那么，如果想要解决这个问题，就得用到一个东西了，那就是 **短期记忆**

## 短期记忆到底是什么
LangChain 把同一个会话中的记忆称为 [Short-term Memory](https://docs.langchain.com/oss/javascript/langchain/short-term-memory)，也就是短期记忆。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901191423536.png)![](./img/ph47qniwki7lanv4-1.png)

咱们先用一句话来理解：

短期记忆就是 **Agent 在同一个会话中保存并继续使用之前 State 的能力。**

比如，用户在售后会话 A 中告诉 Agent：“订单 A1024 的退款金额是 3000 元。”

随后继续追问：“这个金额需要人工审核吗？”

第二句话中没有再次出现订单号和退款金额。

Agent 想要理解“这个金额”指的是什么，就必须继续使用会话 A 前面保存的 State。

但是，如果同一个用户新建了会话 B，再问一遍“这个金额需要人工审核吗”，Agent 默认不应该把会话 A 的订单信息带进来。

所以，所谓的 “短期” 并不是说 “保存的时间短”，而是说 **这份记忆的使用范围只在当前会话中**

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/ChatGPT%20Image%202026%E5%B9%B49%E6%9C%881%E6%97%A5%2019_33_40.png)![](./img/ph47qniwki7lanv4-2.png)

当前会话的状态完全可以在 PostgreSQL（一个数据库） 中保存几个月。只要这份状态仍然只属于当前会话，没有自动共享给其他会话，它依然属于短期记忆。

那么，短期记忆中保存的到底是什么？

最常见的是历史消息，比如：

+ HumanMessage
+ AIMessage
+ ToolMessage

但是，严格来说，短期记忆保存的是当前会话的 **Graph State**，不只是聊天文字。

假设咱们给 Agent State 增加了下面这些字段：

```javascript
{
    messages: [],
    summary: '',
    currentOrderId: '',
    retrievedDocuments: []
}
```

那么，对话摘要、当前订单号和已经检索到的资料，也可以跟着当前会话一起保存。

所以，**保存聊天记录只是短期记忆中最常见的一部分，并不等于短期记忆的全部。**

## 短期记忆是怎样实现的
概念说清楚以后，接下来就可以看实现了。

如果想要实现短期记忆，那么会涉及到一些概念，比如：`Thread`、`State`、`Checkpoint` 和 `Checkpointer`。

不过这些概念大家不需要专门去记它，知道他们是干嘛的就可以了。

我画了个图，大家可以看下对应的流程

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/ChatGPT%20Image%202026%E5%B9%B49%E6%9C%881%E6%97%A5%2019_38_40.png)![](./img/ph47qniwki7lanv4-3.png)

其中：

+ `Thread` 表示一段独立会话，也就是短期记忆的使用范围。应用程序会为每个会话分配一个 `thread_id`。后续请求只要继续使用相同的 `thread_id`，就会读写同一份会话状态。
+ `State` 是 Agent 当前正在使用的数据。比如说： `messages`。当然，它里面也可以放入一些数据摘要或者其他会话数据。
+ `Checkpoint` 是 Agent 执行到某个时刻时保存下来的 State 快照。一次 Agent 执行中可能经历模型调用、Tool 执行和再次模型调用，LangGraph 会在执行步骤之间保存多个 Checkpoint。
+ `Checkpointer` 则是负责保存和读取这些 Checkpoint 的组件。

> Thread、Checkpoint 和 Checkpointer 的完整定义，可以参考 LangGraph 官方的 [Persistence 文档](https://docs.langchain.com/oss/javascript/langgraph/persistence)。
>

概念一共就这么多，不算难吧，那么接下来咱们就通过代码来实现一下。

## 使用 MemorySaver 验证短期记忆
为了先把短期记忆的流程跑通，这里咱们使用最简单的 [MemorySaver](https://reference.langchain.com/javascript/langchain-langgraph-checkpoint/memory/MemorySaver)。

`MemorySaver` 会把 Checkpoint 保存在当前 Node.js 进程的内存中，不需要额外准备数据库，比较适合本地实验。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901194944758.png)![](./img/ph47qniwki7lanv4-4.png)

本节代码在：

```latex
/Code/Node/第六章/02-short-term-memory
```

进入目录安装依赖，创建 `.env`：

```latex
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-v4-flash
POSTGRES_URI=postgresql://agent_course:agent_course@localhost:5432/agent_memory
```

大家注意看上面的第三条 `POSTGRES_URI` 。

如果是只做短期记忆，那么数据库不是必须的，但是咱们在后面还得验证会话回复，所以在这里先安装上。

咱们这里使用的数据库，叫做 `PostgreSQL`，这个东西可能很多同学不了解是啥，这里跟大家简单说一下。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901195907280.png)![](./img/ph47qniwki7lanv4-5.png)

`PostgreSQL` 是一个关系型数据库，和很多同学听过的 MySQL 属于同一类数据库。

咱们就简单把它理解成一个专门保存数据的服务就可以了。

> PS：这里大家注意。
>
> 严格来说，只验证短期记忆不一定非得使用数据库。咱们马上要使用的 `MemorySaver`，就可以把 State 暂时保存在当前变量里面。
>

但是，因为咱们后面还得验证 **进程退出以后能不能重新恢复会话**。所以，这里提前配置 PostgreSQL，要不后面还得重新弄一遍。

那么，下面这个地址是从哪里来的呢？

```latex
postgresql://agent_course:agent_course@localhost:5432/agent_memory
```

它是根据本节 `docker-compose.yml` 中的 PostgreSQL 配置拼出来的。

打开项目里面的 `docker-compose.yml`  文件，可以看到：

```yaml
environment:
  POSTGRES_DB: agent_memory
  POSTGRES_USER: agent_course
  POSTGRES_PASSWORD: agent_course
ports:
  - "5432:5432"
```

这些配置会在 Docker 中创建一个 PostgreSQL 数据库（**这也意味着咱们需要启动 Docker**）。连接地址的通用格式是：

```latex
postgresql://用户名:密码@数据库地址:端口/数据库名称
```

把本节的配置放进去，就是：

```latex
用户名：agent_course
密码：agent_course
数据库地址：localhost
端口：5432
数据库名称：agent_memory

最终连接地址：
postgresql://agent_course:agent_course@localhost:5432/agent_memory
```

其中，`localhost` 表示 PostgreSQL 运行在当前电脑上，`5432` 是 PostgreSQL 默认使用的端口。

所以，`POSTGRES_URI` 本质上就是连接 PostgreSQL 时使用的数据库地址。如果后面换成云数据库，这个地址通常会由云数据库控制台提供，不需要继续使用这里的本地配置。

同时因为咱们使用的是 **Docker 版 PostgreSQL**。

所以，咱们也不需要单独安装 `PostgreSQL` ，只需要启动  `Docker` 就可以了。

确定 `Docker` 启动之后，CD 到当前源码的目录下 `Node/第六章/02-short-term-memory`，然后执行

```plain
docker compose up -d --wait
docker compose ps
```

等待出现 `healthy` 就可以了

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901200705756.png)![](./img/ph47qniwki7lanv4-6.png)

然后执行：

```bash
npm run memory
```

这次代码就会完成两组对话。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901200736155.png)![](./img/ph47qniwki7lanv4-7.png)

第一组对话使用 `support-user-1001`：

```latex
第一轮：订单 A1024 的退款金额是 3000 元，请先记住。
第二轮：刚才说的是哪个订单，退款金额是多少？
```

第二组对话使用 `support-user-1002`，只发送：

```latex
刚才说的是哪个订单，退款金额是多少？
```

这个结果正好验证了前面讲过的短期记忆范围：**相同 Thread（会话窗口） 可以继续使用之前的 State，换一个 Thread（会话窗口） 就从新的 State 开始。**

接下来，咱们从 `memory-session.js` 来看一下这个功能是怎么实现的。

代码先创建 `MemorySaver`，再把它交给 Agent：

```javascript
/**
 * 在同一个 Node.js 进程里验证：
 * 1. 相同 Thread 能够接续已有 State；
 * 2. 不同 Thread 相互隔离；
 * 3. thread_id 不能替代用户权限校验。
 */
async function main() {
    const checkpointer = new MemorySaver()
    const agent = createMemoryAgent(checkpointer)
...
}
```

进入 `createMemoryAgent()`，这个 Checkpointer 会被传给 `createAgent()`：

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901200938862.png)![](./img/ph47qniwki7lanv4-8.png)

现在 Agent 已经知道 State 应该交给谁保存了。

但是，他还不知道应该读取哪一份 State。

所以，调用以前还要准备当前会话的 `thread_id`：

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901201038458.png)![](./img/ph47qniwki7lanv4-9.png)

最终生成的 Config 中包含：

```javascript
{
    configurable: {
        thread_id: 'support-user-1001'
    }
}
```

两轮 `invoke()` 都会使用同一个 Config。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901201131739.png)![](./img/ph47qniwki7lanv4-10.png)

大家注意：第二次调用只传入了本轮新增的问题，并没有重新提交第一轮消息。

![](./img/ph47qniwki7lanv4-11.png)![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901201215869.png)

## 短期记忆和会话恢复不是一回事
运行到这里，Agent 已经拥有短期记忆了。

但是，如果关闭 Node.js 进程，再重新执行 `npm run memory`，刚才的两个会话又会从空 State 开始。

这是不是说明短期记忆失效了？

不是。

这是因为本节使用的是：

```javascript
const checkpointer = new MemorySaver()
```

`MemorySaver` 把 Checkpoint 保存在当前系统进程的内存里。进程退出以后，这个对象和里面的数据都会消失。

他已经实现了同一 Thread 中的短期记忆，只是没有提供跨进程的持久化能力。

这就引出了本节的第二个概念：**会话恢复**。

> 会话恢复，指的是：**应用程序重新启动或者用户稍后回来时，重新加载指定 Thread State，并从上一次结果继续交互的能力。**
>

想要实现会话恢复，Checkpoint 就不能只保存在当前 Node.js 进程里面了。而是必须要写入进程外部的持久化存储。

为此，LangGraph 提供了一个专门对接 PostgreSQL 的 Checkpointer，叫作 `[PostgresSaver](https://reference.langchain.com/javascript/langchain-langgraph-checkpoint-postgres)`。

![](./img/ph47qniwki7lanv4-12.png)![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901201938209.png)

`PostgresSaver` 是一个把 Agent 产生的 Checkpoint 写入 PostgreSQL，并在下一次调用时，根据 `thread_id` 读取并恢复对应的 State。

那么接下来，咱们把 `MemorySaver` 换成 `PostgresSaver` 来看一下

## 使用 PostgreSQL 实现会话恢复
先打开 `postgres-session.js`。

咱们之前用的代码是是：

```javascript
const checkpointer = new MemorySaver()
```

现在，代码改成了：

```javascript
const checkpointer = PostgresSaver.fromConnString(getPostgresUri())
```

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901202733454.png)![](./img/ph47qniwki7lanv4-13.png)

`getPostgresUri()` 会读取 `.env` 中的 `POSTGRES_URI`，`fromConnString()` 再使用这个地址创建一个连接 PostgreSQL 的 `PostgresSaver`。

接下来，代码会执行：

```javascript
await checkpointer.setup()
```

`setup()` 会准备保存 Checkpoint 所需的数据库表。

不过这里有一点小点，大家需要注意下，那就是

> 在真实项目里面大家最好把  `await checkpointer.setup()` 放到应用启动或者部署阶段，尽量不要在每次请求时都执行一次。
>

最后，`PostgresSaver` 仍然会被传给同一个 `createMemoryAgent()`：

```javascript
const agent = createMemoryAgent(checkpointer)
```

也就是说，Agent 的创建方式和 `thread_id` 都没有变化。真正替换掉的，只是底层保存 Checkpoint 的位置：

为了避免之前的实验数据影响结果，先清理本案例使用的 Thread：

```bash
npm run postgres:reset
```

这个命令只会删除 `support-postgres-1001` 对应的 Checkpoint。

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901203013932.png)![](./img/ph47qniwki7lanv4-14.png)

然后执行：

```bash
npm run postgres:write
```

![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901203049286.png)![](./img/ph47qniwki7lanv4-15.png)

这条命令会启动第一个 Node.js 进程，并向 `support-postgres-1001` 发送：

```latex
请记住：订单 A2048 当前缺少商品损坏照片，需要用户补充以后再审核。
```

Agent 返回回答以后，`PostgresSaver` 会把当前 Thread 的 State 保存到 PostgreSQL。

终端打印完两条消息以后，这个 Node 进程会结束，之前在进程中创建的 Agent 和 `PostgresSaver` 对象也会被销毁。

如果下一步仍然能够找回订单信息，就说明数据确实保存在 PostgreSQL 中，而不是留在刚才的 JavaScript 对象里。

接下来执行：

```bash
npm run postgres:continue
```

这条命令会启动一个全新的 Node 进程，重新创建 `PostgresSaver` 和 Agent。

代码首先使用相同的 `thread_id` 获取 State：

```javascript
const config = createThreadConfig(
    'user-1001',
    'support-postgres-1001'
)

const stateBeforeInvoke = await agent.getState(config)
```

`getState()` 会让 `PostgresSaver` 根据 `thread_id`，从 PostgreSQL 读取这个 Thread 最新的 Checkpoint。

恢复成功以后，终端会先打印第一个进程保存的两条消息。

随后，第二个进程才会继续提问：“这个订单还缺少什么材料？”

最终 State 中会出现四条消息，模型也应该回答“商品损坏照片”。

![](./img/ph47qniwki7lanv4-16.png)![](02-%E7%9F%AD%E6%9C%9F%E8%AE%B0%E5%BF%86%E4%B8%8E%E4%BC%9A%E8%AF%9D%E6%81%A2%E5%A4%8D%EF%BC%9AThread%E3%80%81Checkpointer%20%E4%B8%8E%E7%8A%B6%E6%80%81%E6%8C%81%E4%B9%85%E5%8C%96.assets/image-20260901203208452.png)

这就是咱们前面所说的会话恢复。

## 总结
这一小节最重要的，是区分短期记忆和会话恢复。

短期记忆表示 Agent 能够在同一个 Thread 中继续使用之前的 State。“短期”描述的是会话范围，不是数据保存时间。

`Thread` 决定 State 属于哪个会话，`Checkpoint` 是执行过程中保存的 State 快照，`Checkpointer` 负责保存和恢复这些快照。

`MemorySaver` 可以验证相同 Thread 的状态延续，但进程退出以后数据会消失。`PostgresSaver` 把 Checkpoint 保存到 PostgreSQL，让新的进程也能恢复原来的 Thread。

而 Checkpointer 解决的是 Agent State 的保存和恢复。

他并没有实现跨会话的长期记忆。

用户在 Thread A 中说“以后回答先给结论”，新建 Thread B 以后，这项偏好默认不会自动出现。

那么，怎样把信息保存到用户级别，并在多个 Thread 中重新召回，则是后面 `Store` 和长期记忆要解决的问题。

但是，随着 Thread 越来越长，`messages` 肯定也会消耗更多 Token，一些无关历史数据还可能会干扰 AI 的回答。

那么怎么办呢？

下一小节再说～

> 更新：2026-09-01 20:47 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/ph47qniwki7lanv4>
