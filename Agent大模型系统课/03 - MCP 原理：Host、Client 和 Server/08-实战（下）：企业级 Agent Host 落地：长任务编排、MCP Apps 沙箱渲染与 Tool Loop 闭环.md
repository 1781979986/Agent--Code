# 08-实战（下）：企业级 Agent Host 落地：长任务编排、MCP Apps 沙箱渲染与 Tool Loop 闭环

上一小节咱们从 `npm run host` 开始，顺着 `agent-host.js`  来讲。

到现在大家应该已经理解 Host 是怎么携带身份连接 Server、怎么发现当前用户可以使用的能力，以及查询订单、退款预审、MRTR 确认和幂等写入到底是怎么执行的。

但是，上一小节咱们看的都是命令行操作。

除了命令行之外，咱们还得看 `web-host` 的可视化操作部分。

这就是咱们这一小节要做的

## 从 `npm run web-host` 进入项目
保持 MCP Server 运行。

执行：

```bash
npm run web-host
```

终端会打印两个地址：

```latex
MCP Apps Web Host：http://127.0.0.1:3200
MCP Apps Sandbox：http://127.0.0.1:3201
```

先打开第一个：

```latex
http://127.0.0.1:3200
```

这个就是项目的浏览器版 Agent Host。

![](./img/ll6g8afz5cv4e681-1.png)



> 至于第二个 `Sandbox` 先不用管。它并不是给用户直接访问的页面，等后面真正加载 MCP App 时，咱们再看它是干什么的。
>

先回到代码。

`package.json` 中的 `web-host` 指令是：

```json
{
    "scripts": {
        "web-host": "node --env-file-if-exists=.env src/web-host-server.js"
    }
}
```

所以，Node 服务的入口是：`src/web-host-server.js` 

![](./img/ll6g8afz5cv4e681-2.png)



这个文件会启动两个 HTTP Server。

其中，`3200`** 端口负责浏览器里的 Agent Host，**`3201`** 端口负责后面加载 MCP App 的隔离环境**。

浏览器打开页面以后，还会执行 `src/web-host/main.js` 中的 `main()`：

![](./img/ll6g8afz5cv4e681-3.png)



它会读取页面配置，然后按照当前选择的身份连接 MCP Server，获取这个身份能够发现的 Tools。

做到这里，浏览器版 Host 就启动完成了。

接下来，点击左侧的：`一键演示 MCP App`

![](./img/ll6g8afz5cv4e681-4.png)



页面会切换成蓝鲸科技财务身份，并提示是否确认启动三笔订单的批量退款审核。

先别急着点确认。

![](./img/ll6g8afz5cv4e681-5.png)



咱们现在就从这个按钮开始追代码。

## 点击按钮以后，程序从哪里开始执行
按钮的点击事件在 `src/web-host/main.js`  里面，点击之后会触发 `runDeterministicAppDemo` 方法：

![](./img/ll6g8afz5cv4e681-6.png)



这个方法就是后续执行逻辑的起始方法

![](./img/ll6g8afz5cv4e681-7.png)



这个方法其实做的事情挺多的，咱们一个个来看

### 启动批量审核任务
进入 `runDeterministicAppDemo()`，首先会看到下面这段代码：

![](./img/ll6g8afz5cv4e681-8.png)



批量退款审核属于财务能力。

所以，如果当前页面选择的是客服身份，程序会先切换成蓝鲸科技财务，再重新连接 MCP Server。

连接完成以后，调用第一个 Tool：

![](./img/ll6g8afz5cv4e681-9.png)



这里会触发一个叫做  `callMcpTool()` 的方法，在这个方法中有一个 `requestMcpTool(name, args)` 调用

![](./img/ll6g8afz5cv4e681-10.png)



而 `requestMcpTool` 方法之所以可以调用 `MCP Tool` 本质上就是通过一个 **接口调用** 来完成的

![](./img/ll6g8afz5cv4e681-11.png)



而  `/api/mcp/call` 这个接口，他就在 `web-host-server.js` 的 `createServer` 方法里面：

![](./img/ll6g8afz5cv4e681-12.png)



咱们来看下这个 `if` 里面的代码，去除边缘逻辑之后，其实他做的事情很简单，就一个事，那就是：**创建 MCP Client 获取响应数据**

![](./img/ll6g8afz5cv4e681-13.png)



而  `withMcpClient` 会调用 `createAfterSalesClient` 方法来创建对应的 MCP Client：

![](./img/ll6g8afz5cv4e681-14.png)



代码就来到了 `mcp-client` 的 `createAfterSalesClient` 方法里面：

![](./img/ll6g8afz5cv4e681-15.png)



这个方法大家看起来是不是就很熟悉了？

这不就是咱们在上一小节中讲到的 **“第一件事吗”**

![](./img/ll6g8afz5cv4e681-16.png)



看到这里，很多同学可能会觉得：这不就是把上一节的命令行确认，换成了浏览器弹窗吗？

从 MCP 的确认机制来看，确实是同一件事。

但是，从代码执行过程来看，Web Host 中间还多了一次 **浏览器和 Node 代理之间的通信过程**。

第一次调用 `start_batch_refund_review` 时，浏览器传入的 `decision` 是：`decision: 'prompt'`

![](./img/ll6g8afz5cv4e681-17.png)



他指的是：**当前还没有用户的决定，如果 Server 要求确认，就先把问题返回给浏览器。**

所以，第一次调用会停在 「显示确认弹窗」那里，大致流程就是下面这样

![](./img/ll6g8afz5cv4e681-18.jpeg)



此时，批量审核任务还没有创建，`startBatchReview()` 也没有执行。

用户点击“确认执行”以后，会执行到 `finishConfirmation` 方法（这个流程不给大家演示了，从确认按钮的点击事件中可以直接找到）：

![](./img/ll6g8afz5cv4e681-19.png)



![](./img/ll6g8afz5cv4e681-20.png)



Server 再次进入 `start_batch_refund_review` 的 Handler 时，就可以同时拿到 `requestState` 和 `inputResponses` 了：

![](./img/ll6g8afz5cv4e681-21.png)



Server 会比较确认前后的操作是否一致，避免页面确认的是 `A1024`，最终执行时却被换成了其他订单。

只有 `requestState`、`orderIds` 和确认结果全部通过校验，才会真正执行：

![](./img/ll6g8afz5cv4e681-22.png)



所以，弹窗本身不会创建审核任务。

它只负责收集用户的选择，真正的批量审核仍然由 MCP Server 中的业务代码执行。

然后，页面才会拿到这次任务对应的 `jobId`。

问题也就来了。

为什么 Server 不直接把三笔订单的最终审核结果返回，而是先返回一个任务编号？

### 为什么长任务要先返回 `jobId`
先来看 `startBatchReview()` 里面真正创建了什么：

![](./img/ll6g8afz5cv4e681-23.png)



这个方法中有这么一段代码：

![](./img/ll6g8afz5cv4e681-24.png)



这里创建了一条任务记录，把状态设置成 `working`，然后立刻把 `jobId` 返回给 Host。

这样设计是因为：真实的批量审核耗时通常会比较长。

如果 `start_batch_refund_review` 一直占着当前 Tool 请求，等所有工作做完才返回，调用时间一长，就容易遇到 HTTP 超时、网络中断 的问题

所以，耗时任务通常会被拆开进行处理。

而这里的 `jobId` 就是这次后台任务的唯一编号。

到这里，长任务只完成了一半。

Server 已经把任务创建出来了，但是 Host 怎么知道它什么时候处理完呢？

### Host 根据 `jobId` 查询进度
回到 `runDeterministicAppDemo()`。

程序拿到 `jobId` 以后，会创建一条页面进度提示：

![](./img/ll6g8afz5cv4e681-25.png)



接下来进入循环：

![](./img/ll6g8afz5cv4e681-26.png)



这段代码每隔 `350ms` 调用一次 `get_batch_review_status`

并且把刚才得到的 `jobId` 传给 Server。

Server 会根据任务已经运行的时间，依次返回：

```latex
25%  正在读取订单
70%  正在执行退款规则
100% 批量审核完成
```

![](./img/ll6g8afz5cv4e681-27.png)



页面每拿到一次结果，就更新同一条进度信息。

直到状态变成：`completed`

循环才会停止。

这就是当前项目的长任务执行方式：

> **第一次 Tool 调用只负责创建任务，后面的 Tool 调用再根据 **`jobId`** 读取任务状态。**
>

这里还有一个容易忽略的地方。

每次浏览器调用 `/api/mcp/call` 时，Node 代理都会重新创建 MCP Client，并重新连接 Server。`get_batch_review_status` 不一定和启动任务使用的是同一个 MCP Server 实例。

所以，任务状态不能放在某一个 Server 实例里面。

当前代码把 `reviewJobs` 放在 Server 工厂之外的业务层

这样，新的 Server 实例仍然可以读取前面创建的任务。

### 任务完成以后读取审核报告
任务状态变成 `completed` 以后，程序继续执行最后一次 Tool 调用：`await callMcpTool('get_batch_review_report', { jobId })`

![](./img/ll6g8afz5cv4e681-28.png)



`get_batch_review_report` 返回的是这次批量审核的完整结果，其中包括：订单总数、自动通过数量、需要人工审核的数量，以及每一笔订单的审核结论。

从数据上看，到这里其实已经完成了。

Host 完全可以把这些内容直接打印成 JSON。

但是，财务真正查看结果时，显然更适合使用统计信息和表格，而不是盯着一大段 JSON。

**这就是项目需要 MCP App 的地方。**

咱们进入 `mcp-server.js`，看看 `get_batch_review_report` 和普通 Tool 有什么区别：

![](./img/ll6g8afz5cv4e681-29.png)



最关键的地方是：

```javascript
_meta: {
    ui: {
    // APP_URI 的值是：'ui://after-sales/batch-review-report.html'
        resourceUri: APP_URI
    }
}
```

这段数据是在告诉 Host：

> `get_batch_review_report` 除了会返回普通 Tool Result，还关联了一份专门展示结果的 UI Resource。
>

这个 UI Resource 的地址就是：

```latex
ui://after-sales/batch-review-report.html
```

Server 还要注册对应的 Resource：

![](./img/ll6g8afz5cv4e681-30.png)



其中：

+ `get_batch_review_report` 返回的是 **审核数据**。
+ `ui://after-sales/batch-review-report.html` 返回的是 **用来展示这些数据的页面资源**。

那么很多同学看到这里可能就疑惑了：**Host 怎么发现并加载 MCP App 的呢？**

继续回到 `src/web-host/main.js`。

页面调用任何 Tool 时，都会经过 `callMcpTool()`：

![](./img/ll6g8afz5cv4e681-31.png)

Tool 执行完成以后，Host 会读取这个 Tool 的定义。

如果能从 `_meta.ui.resourceUri` 中拿到 UI 地址，就继续执行：`renderMcpApp({ tool, args, result })`

`renderMcpApp()` 先调用 `readAppResource()`：

![](./img/ll6g8afz5cv4e681-32.png)



这一次 `/api/mcp/resource` 请求，会让 Node 侧 MCP Client 调用 `resources/read`，读取刚才的 `ui://` Resource。

![](./img/ll6g8afz5cv4e681-33.png)



Host 最终拿到三类信息：

+ html：MCP App 构建后的页面代码
+ csp：MCP App 允许访问哪些外部资源
+ permissions：MCP App 申请使用哪些浏览器能力

但是，Host 不能直接执行这段 HTML。

因为这段页面代码来自 MCP Server。

## 为什么 MCP App 必须放进 Sandbox
站在 Web Host 的角度，MCP App 属于外部代码。

如果把它直接插进 Agent Host 页面，它里面的 JavaScript 就可能读取父页面 DOM、Cookie、Local Storage，甚至修改当前对话内容。

这显然不行。

所以，MCP Apps 使用隔离的 iframe 来运行页面。Host 和 App 不能互相读取 DOM，只能通过受控消息通信。

这也解释了 `npm run web-host` 为什么会同时启动两个地址：

```latex
Web Host：http://127.0.0.1:3200
Sandbox：http://127.0.0.1:3201
```

虽然它们都在本机，但是端口不同，因此属于不同的 Origin。

项目中的关系是：

![](./img/ll6g8afz5cv4e681-34.png)



对应的代码如下：

![](./img/ll6g8afz5cv4e681-35.png)



`renderMcpApp()` 会先创建 iframe，并让它加载 `3201` 端口的 Sandbox：

```javascript
const iframe = document.createElement('iframe')

iframe.setAttribute(
    'sandbox',
    'allow-scripts allow-same-origin allow-forms'
)

const sandboxUrl = new URL(config.sandboxUrl)
await waitForSandbox(iframe, sandboxUrl.href)
```

Sandbox 启动以后，会先检查自己是不是被 Host 嵌入：

```javascript
if (window.self === window.top) {
    throw new Error('Sandbox 只能在 Host iframe 中运行')
}

if (!document.referrer) {
    throw new Error('Sandbox 无法确认 Host 来源')
}
```

所以说：Sandbox 本身不是业务页面，它是夹在 Host 和 MCP App 之间的一层安全代理。

接下来，Web Host 创建 `AppBridge`，并通过 `PostMessageTransport` 和 Sandbox 建立通信：

![](./img/ll6g8afz5cv4e681-36.png)



这里可以把 `AppBridge` 理解成 Host 侧的 MCP App 通信控制器。

它把页面初始化、Tool Input、Tool Result 和尺寸变化等消息，转换成基于 `postMessage` 传递的 JSON-RPC 消息。

通信建立以后，Host 会按照顺序发送三份内容：

![](./img/ll6g8afz5cv4e681-37.png)



第一份是 MCP App 的页面资源，第二份是这次 Tool 的输入参数，第三份才是 Tool 的执行结果。

Sandbox 收到页面资源以后，会把 HTML 写进内层 iframe。后续消息则会在 Host 和 App 之间转发。

这样一来，MCP App 可以拿到需要展示的数据，但是不能直接读取 Host 的页面和登录信息。

这就是 Sandbox 在当前项目中的作用：

> **让 Host 能够展示 MCP Server 提供的交互页面，同时把这段外部页面代码限制在隔离环境中。**
>

MCP Apps 官方同样采用 sandboxed iframe 和 `postMessage` 的通信方式。

官方文档：`https://modelcontextprotocol.io/extensions/apps/overview`

![](./img/ll6g8afz5cv4e681-38.png)



## MCP App 拿到 Tool Result 以后怎么渲染
现在，Host 已经把 Tool Result 发出去了。

最后再看 MCP App 这一侧。

报告 App 的路径是：

```latex
src/app/main.js
```

核心代码非常少：

```javascript
// 创建 MCP App，并监听 Host 发送过来的 Tool Result。
const app = new App(
    {
        name: 'after-sales-review-report',
        version: '1.0.0'
    },
    {}
)

app.ontoolresult = (params) => {
    render(params.structuredContent)
}

// 连接 Host，开始接收 Tool Input 和 Tool Result。
await app.connect()
```

`app.connect()` 会让 App 接入刚才建立的消息通道。

当 Host 发送 Tool Result 时，`ontoolresult` 就会收到这次批量审核的 `structuredContent`。

然后，调用 `render()` 把结果中的统计信息和订单明细写进页面，render 的代码也不复杂，就是基础的 `web api` 调用

![](./img/ll6g8afz5cv4e681-39.png)



到这里，页面中才真正出现批量审核报告。

## 总结
这一篇咱们从：`npm run web-host`

进入浏览器 Host，再顺着“一键演示 MCP App”的点击事件，把整条执行链路追了一遍。

批量审核不会一直阻塞第一次 Tool 调用。Server 先创建任务并返回 `jobId`，Host 再根据这个编号查询进度。任务完成以后，报告 Tool 返回真实审核数据，同时通过 `_meta.ui.resourceUri` 告诉 Host 对应的 MCP App 在哪里。

Host 读取 `ui://` Resource，把页面放进独立 Sandbox，再通过 AppBridge 把 Tool Input 和 Tool Result 发送给 App。App 最后根据 `structuredContent` 渲染出审核报告。

那么到这里整个项目就讲的差不多了，大家在学习中可能还会遇到一些问题或者疑惑，随时联系我就可以  +V：`sunday9189`

> 更新：2026-07-24 18:21 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/ll6g8afz5cv4e681>
