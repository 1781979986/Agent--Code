# 04 - 长期记忆：Store、用户画像与跨会话信息保存

上一小节，咱们解决的是长对话越来越大的问题。

不管是消息裁剪，还是自动摘要，处理的其实都是 **当前 Thread（会话窗口） 中已经存在的历史消息**。

但是，如果咱们要做一个真实的 Agent 系统，那么是绝对不能只支持一个Thread（会话窗口）的。

举个例子，假设用户在售后对话中明确说：

```latex
请记住我的沟通偏好：
以后使用中文回答，先给结论；
如果需要人工联系，请安排在工作日 19:00 以后。
```

几天以后，他新建了一个对话：

```latex
请按照我以前保存的偏好，告诉我订单超过退款阈值后应该怎样处理。
```

新对话使用的是一个全新的 `thread_id`。

按照上一节的处理方式，`Checkpointer` 只会恢复新 Thread 自己的 State，并不会跑到旧 Thread 里寻找用户偏好。

那怎么办？

总不能为了拿到三条偏好，把用户之前的完整聊天记录全部复制过来吧。

这就是这一小节要解决的问题：**把值得长期使用的信息从某一次对话中拿出来，保存到独立于 Thread 的位置，让同一名用户的新会话也可以继续使用。**

## 长期记忆到底是什么
LangChain 官方把能够跨会话、跨 Thread 使用的信息称为 `[Long-term memory](https://docs.langchain.com/oss/javascript/langchain/long-term-memory)`，也就是 **长期记忆**。

![](./img/rbaemchyxx5k9ngr-1.png)![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904110551601.png)

同时，咱们需要注意。

无论是之前学的 “短期记忆” 还是现在看的 “长期记忆”，都和保存时间没任何关系。

真正决定它是不是长期记忆的，是它的 **作用范围**。

> **短期记忆属于某一个 Thread（会话窗口），长期记忆则独立于 Thread，可以被同一用户的多个 Thread 读取。**
>

比如，订单号 `A1024` 当前正在申请退款，这可能只是某次售后会话中的临时状态，保存在当前 Thread 就够了。

而“用户希望使用中文回答”，后面的新会话仍然可能用到，更适合保存成长期记忆。

所以，长期记忆保存的应该是：**经过选择以后，未来仍然值得使用的信息。**

> PS：
>
> 很多人会用 **用户画像** 这个词来描述长期记忆的保存形式，虽然不是特别的准确，但是因为说的人多，所以咱们后面也用这个词
>

大家还记不记得，咱们上一节使用过的 `Checkpointer`。

它会根据 `thread_id` 保存和恢复 Agent State。

现在咱们要保存的是用户画像，用户可以新建很多个 Thread，但是用户身份并没有发生变化。

所以，这份数据不能继续使用 `thread_id` 作为读取范围：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/ChatGPT%20Image%202026%E5%B9%B49%E6%9C%884%E6%97%A5%2011_17_32.png)![](./img/rbaemchyxx5k9ngr-2.png)

为了解决这个问题，LangGraph 提供了 `[Store](https://docs.langchain.com/oss/javascript/langchain/long-term-memory#usage)`。

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904112057213.png)![](./img/rbaemchyxx5k9ngr-3.png)

`Store` 不会按照 `thread_id` 保存整份 Agent State，而是让 Agent 应用使用自己定义的 `Namespace` 和 `Key`，保存一份 JSON 数据。

## 写个代码
这一小节的代码在：

```latex
/Code/Node/第六章/04-long-term-memory
```

安装依赖以后，直接执行：

```bash
npm run store
```

这段代码使用的就是咱们上面说的 Store ，也就是 `InMemoryStore`：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904113227927.png)![](./img/rbaemchyxx5k9ngr-4.png)

在上面的代码中，`InMemoryStore` 会把数据保存在进程中（注意：企业项目肯定要保存到数据库的）

代码保存的就是一份用户画像，也就是下面这段代码：

```javascript
await saveUserProfile(store, userOne, {
    responseLanguage: 'zh-CN',
    answerStyle: 'conclusion_first',
    contactWindow: '工作日 19:00 以后'
})
```

执行以后，终端会打印三部分内容。

+ 第一次：以 `bluewhale / user-1001` 的身份保存画像
+ 第二次：模拟这名用户在另一个 Thread 中读取，可以得到刚才保存的数据
+ 第三次，换成 `bluewhale / user-1002`，读取结果是 `null`

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904112555766.png)![](./img/rbaemchyxx5k9ngr-5.png)

那么，通过以上代码咱们就可以知道：**只要记忆的存储位置不包含 **`thread_id`**，同一名用户就可以在不同 Thread 中读取它。**

但是，换一名用户以后，读取位置变了，自然也就拿不到前一名用户的画像。

### Namespace、Key 和 Value
打开 `profile-store.js`，真正负责确定保存位置的是 `profileNamespace()`：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904113718692.png)![](./img/rbaemchyxx5k9ngr-6.png)

假设当前登录用户是 `user-1001`，最终得到的 Namespace 就是：

```javascript
[
    'agent-course',
    'tenants',
    'bluewhale',
    'users',
    'user-1001', // 用户编号
    'profiles'
]
```

大家可以先把 `Namespace` 理解成数据库中一条记忆所属的目录。

这个目录里还可以保存多条数据，所以咱们还得给当前画像设置一个 `Key`：

```javascript
const PROFILE_KEY = 'current'
```

最后，用户画像本身就是 `Value`：

```javascript
{
    responseLanguage: 'zh-CN',
    answerStyle: 'conclusion_first',
    contactWindow: '工作日 19:00 以后'
}
```

把它们放在一起，Store 中的一条记录大致就是：

> + Namespace：这是谁的哪一类记忆
> + Key：这条记忆叫什么
> + Value：实际保存的 JSON 数据
>

保存时调用 `put()`：

```javascript
await store.put(namespace, 'current', profile)
```

读取时调用 `get()` ：

```javascript
const item = await store.get(namespace, 'current')
```

你看，整个 `store` 实例的用法就很简单

```javascript
// 创建 store
const store = new InMemoryStore()
// 保存数据
await store.put(namespace, 'current', profile)
// 读取数据
const item = await store.get(namespace, 'current')
```

## 长期记忆的分类
LangChain 的 [记忆概念文档](https://docs.langchain.com/oss/javascript/concepts/memory) 把长期记忆大致分成了三种类型。

![](./img/rbaemchyxx5k9ngr-7.png)![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904114010738.png)

+ 第一种 [语义记忆（ Semantic Memory）](https://docs.langchain.com/oss/javascript/concepts/memory#semantic-memory) ：比如 用户叫什么、偏好什么回答方式，也就是一些事实和偏好信息  
![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904114540367.png)![](./img/rbaemchyxx5k9ngr-8.png)
+ 第二种 [发生过的经历（](https://docs.langchain.com/oss/javascript/concepts/memory#episodic-memory)`Episodic Memory`[）](https://docs.langchain.com/oss/javascript/concepts/memory#episodic-memory)：比如 上一次退款时，用户补交照片后审核才通过了，保存的是发生过的经历。  
![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904114605744.png)![](./img/rbaemchyxx5k9ngr-9.png)
+ 第三种 [程序记忆（](https://docs.langchain.com/oss/javascript/concepts/memory#procedural-memory)`Procedural Memory`[）](https://docs.langchain.com/oss/javascript/concepts/memory#procedural-memory)：比如 处理退款以前必须先核对订单归属，保存的是事情的固定规则。  
![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904114634912.png)![](./img/rbaemchyxx5k9ngr-10.png)

咱们这里实现了第一种：**把用户当前有效的事实和偏好整理成一份用户画像。**

这份画像的身份和 Key 都是对应的，可以直接读取。

## 使用 PostgreSQL 保存真正的长期记忆
在前面咱们也说了，企业级的项目数据一定得保存在数据库里面。

因此，接下来咱们就得使用 PostgreSQL 来尝试数据保存，完成长期记忆了

PostgreSQL 咱们在前面用过。咱们得 PostgreSQL 是配合 docker 使用的。

咱们这里就不重新创建新的 `docker-compose.yml` 文件了，直接用咱们 第二小节的（执行下面的命令之前，确保 docker desktop 已经打开了）。

```bash
docker compose -f ../02-short-term-memory/docker-compose.yml up -d --wait
```

当前指令在 ` 04-long-term-memory` 的路径下执行，否则得修改 `../02-short-term-memory/docker-compose.yml` 的路径：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140026645.png)![](./img/rbaemchyxx5k9ngr-11.png)

然后，在当前项目的 `.env` 中配置：

```latex
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-v4-flash
POSTGRES_URI=postgresql://agent_course:agent_course@localhost:5432/agent_memory
```

第一次运行本案例时，还需要执行：

```bash
npm run setup
```

这条命令完成表的初始化，执行下面的代码：

```javascript
await checkpointer.setup()
await store.setup()
```

前者准备保存 Checkpoint 所需的数据表，后者准备 Store 所需的数据表。

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140147554.png)![](./img/rbaemchyxx5k9ngr-12.png)

接下来打开 `storage.js` ，咱们来看这一段代码：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140227266.png)![](./img/rbaemchyxx5k9ngr-13.png)

这里依然保留 `PostgresSaver`，因为 Agent 自己的 Thread State 还得正常保存。

新增加的 `PostgresStore`，会负责把用户画像保存成跨 Thread 数据。

在创建 Agent 时，两个都会被传进去：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140334496.png)![](./img/rbaemchyxx5k9ngr-14.png)

到这里，存储组件已经准备好了。

但是！把 `store` 传给 Agent，并不代表模型会自动知道里面保存了什么。

模型没有直接读取数据库的能力。所以咱们仍然需要在合适的时候写入记忆，并在回答之前把需要的记忆取出来。

因此，当前 Agent 还定义了两个 Tool。

+ 一个叫做 `save_user_profile`：它负责 把当前用户画像写入 Store  
![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140451680.png)![](./img/rbaemchyxx5k9ngr-15.png)
+ 一个叫做 `get_user_profile`：它负责 从 Store 读取当前用户画像  
![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140510603.png)![](./img/rbaemchyxx5k9ngr-16.png)

接下来咱们就顺着这两个操作，把跨会话记忆完整跑一遍。

### Thread A：明确保存用户画像
先执行：

```bash
# 清理旧的用户画像
npm run reset
#创建新的用户画像
npm run remember
```

`remember` 会进入 `profile-agent.js` 中的 `remember()` 里面：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140643700.png)![](./img/rbaemchyxx5k9ngr-17.png)

核心代码就下面这句

```javascript
const state = await agent.invoke(
    {
        messages: [
            {
                role: 'user',
                content:
                    '请记住我的沟通偏好：以后使用中文回答，先给结论；如果需要人工联系，请安排在工作日 19:00 以后。'
            }
        ]
    },
    createRunConfig(
        BLUEWHALE_USER,
        'profile-write-thread-1001'
    )
)
```

这是用户明确提出的“请记住”。模型会选择 `save_user_profile`，Tool 再执行：

```javascript
await saveUserProfile(
    runtime.store,
    runtime.context,
    profile
)
```

这里的 `profile` 来自模型整理后的 Tool 参数。

但是，画像保存到哪一名用户下面，不能继续让模型决定。

`tenantId` 和 `userId` 来自 `runtime.context`：

```javascript
{
    tenantId: 'bluewhale',
    userId: 'user-1001'
}
```

在真实项目中，这两个值应该由登录鉴权结果生成，再由应用程序放进 Runtime Context。

用户在 Prompt 中说“我是另一个用户”，不能改变当前真实身份。

执行完成以后，终端会打印本轮 Message 和 Store 中实际保存的画像。

这里只要确认：模型确实调用了 `save_user_profile`，并且 PostgreSQL 中能够重新读取到 `responseLanguage`、`answerStyle` 和 `contactWindow`。 就可以了

最终的打印如下：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140759931.png)![](./img/rbaemchyxx5k9ngr-18.png)

### Thread B：在新会话中读取旧偏好
`remember` 执行完以后，当前 Node.js 进程已经结束了。

现在单独执行：

```bash
npm run recall
```

这条命令会启动一个新的进程，并使用一个全新的 Thread：

```javascript
createRunConfig(
    BLUEWHALE_USER,
    'profile-read-thread-1001'
)
```

注意，保存时使用的是：

```latex
profile-write-thread-1001
```

读取时使用的是：

```latex
profile-read-thread-1001
```

两个 `thread_id` 完全不同，所以 `PostgresSaver` 不会把 Thread A 的历史消息恢复到 Thread B。

Thread B 中只有一个问题，那就是：

```latex
这是一个新会话。请按照我以前保存的偏好，告诉我订单超过退款阈值后应该怎样处理。
```

模型会先调用 `get_user_profile`。

Tool 使用 Runtime Context 中的用户身份生成 Namespace，再通过固定 Key 读取画像：

```javascript
const profile = await loadUserProfile(
    runtime.store,
    runtime.context
)
```

读取结果会作为 `ToolMessage` 回到 Agent State。模型看到这份画像以后，才能按照“中文回答、先给结论”的要求生成最终回答。

最终的打印如下：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904140906675.png)![](./img/rbaemchyxx5k9ngr-19.png)

最后梳理一遍整体的流程：

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/ChatGPT%20Image%202026%E5%B9%B49%E6%9C%884%E6%97%A5%2014_11_30.png)![](./img/rbaemchyxx5k9ngr-20.png)

## 换一个用户以后还能读到吗
最后执行：

```bash
npm run isolation
```

代码会分别使用三组身份读取同一类画像：

```latex
bluewhale / user-1001
bluewhale / user-1002
star-retail / user-1001
```

最终只有第一组能够得到数据。

![](04-%E9%95%BF%E6%9C%9F%E8%AE%B0%E5%BF%86%EF%BC%9AStore%E3%80%81%E7%94%A8%E6%88%B7%E7%94%BB%E5%83%8F%E4%B8%8E%E8%B7%A8%E4%BC%9A%E8%AF%9D%E4%BF%A1%E6%81%AF%E4%BF%9D%E5%AD%98.assets/image-20260904141007615.png)![](./img/rbaemchyxx5k9ngr-21.png)

`user-1002` 和原用户属于同一个租户，但是用户不同；第三组虽然也叫 `user-1001`，但是所属租户已经变成了 `star-retail`。

它们生成的 Namespace 都不一样，自然不会读取到 `user-1001` 的画像了。

不过，这里必须说清楚一个边界：**Namespace 是数据组织和查询范围，不是完整的权限系统。**

真正的权限边界仍然来自应用程序的登录鉴权。服务端确认当前用户以后，把可信身份交给 Runtime Context，Tool 再使用这份身份读写固定 Namespace。

不能让模型从用户问题中提取 `tenantId` 和 `userId`，再决定读取谁的记忆。

## 总结
到这里，短期记忆和长期记忆的区别就已经讲完了

+ 短期记忆由 `Checkpointer` 保存，核心范围是 `Thread`。它记录当前会话中的 Messages、摘要和任务状态，让同一个 Thread 可以继续执行。
+ 长期记忆由 `Store` 保存，范围由应用程序定义。本节使用 `tenantId + userId` 组成 Namespace，因此同一名用户可以在多个 Thread 中读取同一份画像。

Store 中的一条记忆由 `Namespace`、`Key` 和 `Value` 组成。当前用户画像使用固定的 `current` 作为 Key，Value 则是一份包含回答语言、回答风格和联系时间的 JSON。

但是，保存进 Store 不等于模型会自动读取。咱们还得通过 Tool 或 Middleware，在需要的时候读取记忆，再把结果交给模型，才可以。

> 更新：2026-09-04 15:37 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/rbaemchyxx5k9ngr>
