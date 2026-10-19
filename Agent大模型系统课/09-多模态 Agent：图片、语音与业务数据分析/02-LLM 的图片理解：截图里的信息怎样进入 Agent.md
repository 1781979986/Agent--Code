# 02-LLM 的图片理解：截图里的信息怎样进入 Agent

上一小节，咱们提到，用户可以上传一张销售看板截图，让 Agent 帮忙查看里面的信息。

比如下面这张图。这是我为课程准备的模拟看板，大家可以先瞅一眼。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/dashboard-clear.png)![](./img/iiuggfcqunxuy43z-1.png)

那么现在，假如用户问：

> 截图中的销售额统计了哪个时间范围？金额和单位是什么？是否扣除了退款？
>

那么大家想一下，如果要回答这个问题，是不是需要把图片里几处不同的信息联系起来，比如：上面的统计周期、中间的销售额，以及右下角的指标说明。

那么想要做到这一点，就需要 **读取这张截图，从而让 LLM 理解这张图片中的数据**

## 图片理解到底在做什么
很多同学看到“读取截图”，可能会想到 `OCR`。

[OCR，也就是光学字符识别](https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/concept-ocr)，主要解决把图片里的文字识别出来的问题。

要回答刚才的问题，还需要判断这些文字之间的关系。

比如：

> “128.60”对应的是销售额
>
> “3,216”对应的是订单数
>
> 右下角的“未扣除退款”解释的是销售额的统计口径
>
> 模型需要结合标签、位置和用户问题，找到相互对应的信息。
>

这就是本节要使用的 **图片理解能力：结合图片内容与用户问题，识别信息之间的关系，并给出相应结果。**

那么，哪些模型具备这种能力呢？

国内可以选择的视觉模型有不少，比如：

+ **阿里的千问视觉模型**： [Qwen3-VL](https://help.aliyun.com/zh/model-studio/vision)，支持图片问答、文字识别和图表理解，可以通过阿里云百炼调用。  
![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007110231554.png)![](./img/iiuggfcqunxuy43z-2.png)
+ **字节的豆包多模态模型**： [Doubao-Seed-2.1](https://docs.volcengine.com/docs/ark/image-understanding)，可以结合图片和文字问题进行分析，通过火山方舟调用。  
![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/PixPin_2026-10-07_11-03-00.png)![](./img/iiuggfcqunxuy43z-3.png)
+ **智谱的 GLM 多模态模型**： [GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)，支持图片与文字输入，可以通过智谱开放平台调用，完成截图理解等任务。  
![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007110658217.png)![](./img/iiuggfcqunxuy43z-4.png)

这些模型都能接收图片和文字问题，再用文字或结构化数据返回分析结果。

智谱咱们之前用过，这里换个。这次咱们用 **千问的 **`qwen3-vl-flash`**。** 国内的公司用的多一些。

接下来，咱们就用它读取刚才那张销售看板，看看图片怎样传进去，识别结果又怎样返回。

## 先运行一次图片读取
本节代码在 `Code/Node/第九章/02-image-understanding`。

核心代码只有一个文件：`image-understanding.js`。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007110902705.png)![](./img/iiuggfcqunxuy43z-5.png)

`samples` 目录里准备了两张图片，分别是刚才的清晰看板

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007110934012.png)![](./img/iiuggfcqunxuy43z-6.png)

以及一张部分信息被遮挡的看板。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007110956843.png)![](./img/iiuggfcqunxuy43z-7.png)

资源就用这些，然后咱们来看如何使用百炼的模型

### 准备模型配置
首先在 [阿里云](https://help.aliyun.com/zh/model-studio/vision?accounttraceid=14d810b367b441238c9eb5bd480bc1aajqgd) 选择百炼服务

![](./img/iiuggfcqunxuy43z-8.png)![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007111233621.png)

然后就可以找到 [百炼的控制台](https://bailian.console.aliyun.com/?utm_content=se_1021228191&gclid=Cj0KCQjwuJLWBhD_ARIsAIBcRUw6kfZIYD7f0j1ybC5LkXJW50YiPCAqIcV7KmJsFNghyRBiyHOyw-YaAtGQEALw_wcB)

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007111507234.png)![](./img/iiuggfcqunxuy43z-9.png)

控制台就长这样

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007111917385.png)![](./img/iiuggfcqunxuy43z-10.png)

然后咱们点击第一个 **API Key** ，直接创建

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007112127258.png)![](./img/iiuggfcqunxuy43z-11.png)

这里大家注意：在这个弹出框中我们可以得到两个关键内容：

+ 第一个是：API key 。大家直接复制就行
+ 第二个是：**业务空间 ID**。这个 ID 在咱们配置中会有用到

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007113153064.png)![](./img/iiuggfcqunxuy43z-12.png)

然后，在项目目录配置 `.env`：

```plain
DASHSCOPE_API_KEY=你的百炼_API_Key
DASHSCOPE_BASE_URL=https://你的业务空间ID/compatible-mode/v1
VISION_MODEL=qwen3-vl-flash
```

### 看看清晰截图能读出什么
执行：

```bash
npm run clear
```

这个命令会读取 `samples/dashboard-clear.png`，发送刚才的问题，最终的打印结果如下：

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007113313381.png)![](./img/iiuggfcqunxuy43z-13.png)

重点检查这些内容：

| 要核对的内容 | 图片中明确显示的信息 |
| --- | --- |
| 统计时间 | 2026-09-01 至 2026-09-30 |
| 指标 | 销售额 |
| 数值与单位 | 128.60 万元 |
| 统计口径 | 全部区域，已支付订单实付金额，未扣除退款 |


读取完成以后，终端还会打印本次结果的文件位置，例如 `outputs/clear-<运行时间>.json`。可以打开它，查看实际返回的数据。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007113433459.png)![](./img/iiuggfcqunxuy43z-14.png)

接下来，咱们从代码入口看，这些信息是怎样得到的。

## 从 main() 看图片怎样进入模型
打开 `package.json`，`clear` 对应的是：

```json
"clear": "node --env-file=.env image-understanding.js clear"
```

Node.js 先加载 `.env`，再执行 `image-understanding.js`。文件最后调用 `main()`，`main()` 根据命令选择图片。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007113604310.png)![](./img/iiuggfcqunxuy43z-15.png)

### 读取图片，把文件内容放进请求
`main()` 确定图片路径以后，调用：

```javascript
// 读取图片，并准备 MIME 类型、Base64、文件大小、哈希等后续请求所需信息。
const image = await loadImage(imagePath)
```

`imagePath` 指向当前项目里的 `samples/dashboard-clear.png`。

`loadImage()` 会检查文件是否存在、大小是否符合要求，以及文件头对应哪种图片格式。

通过检查以后，函数使用 `readFile()` 读取图片字节，再生成一个 Data URL。核心写法是：

```javascript
dataUrl: `data:${mimeType};base64,${bytes.toString('base64')}`
```

对于这张 PNG，得到的字符串大致长这样：

```latex
data:image/png;base64,iVBORw0KGgo...
```

`image/png` 说明图片格式，后面的 Base64 保存图片内容。

Base64 是图片的传输表示。这一步还没有识别图片里的文字。

### 把问题和图片放到同一条消息里
`loadImage()` 返回以后，执行回到 `main()`，接着创建模型客户端：

```javascript
// 使用 OpenAI SDK 调用 DashScope 的 OpenAI 兼容接口。
// 示例中关闭自动重试，方便直接观察真实请求失败结果。
const client = new OpenAI({
  apiKey,
  baseURL,
  timeout: 60_000,
  maxRetries: 0
})
```

`apiKey` 和 `baseURL` 来自刚才的 `.env`。客户端准备好以后，`main()` 继续调用：

```javascript
// 将图片和问题交给视觉模型，并取得结构化识别结果。
const result = await inspectDashboard(client, image, model)
```

然后咱们来看下  `inspectDashboard()` 这个方法

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007114251094.png)![](./img/iiuggfcqunxuy43z-16.png)

这个方法中最关键的代码其实是：

```javascript
// 构建请求并调用视觉模型，获取 JSON 读取结果。
const completion = await client.chat.completions.create(
  buildRequest(image, model)
)
```

这里的 `buildRequest()` 负责准备请求。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007114351750.png)![](./img/iiuggfcqunxuy43z-17.png)

先看其中的消息部分：

```javascript
messages: [{
  role: 'user',
  content: [
    { type: 'text', text: extractionPrompt },
    { type: 'image_url', image_url: { url: image.dataUrl } }
  ]
}]
```

前面的课程里提到过，用户消息的 `content` 经常是一段字符串。现在这一条消息包含两个内容块：

+ `text` 放用户问题和读取要求。
+ `image_url` 放刚才生成的图片 Data URL。

这样，模型本次收到的就是“请回答这个问题”和“问题对应的这张图片”。

`extractionPrompt` 定义在文件上方。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007114407471.png)![](./img/iiuggfcqunxuy43z-18.png)

它先提出本节的问题，再明确要求：

```latex
只提取截图中明确可见、与销售额有关的信息，不推算被遮挡的数字，不补全年月或单位。
金额保留图中字符串，不做单位换算。
```

后面还规定返回哪些 JSON 字段，看不清的字段用 `null`，并通过 `uncertainties` 说明不确定的地方。

请求里另外设置了：

```javascript
enable_thinking: false,
response_format: { type: 'json_object' }
```

### 接住模型返回的 JSON
模型请求结束以后，`inspectDashboard()` 得到了 `completion`。

其中 `completion.choices[0].message.content` 保存模型返回的正文。由于请求约定了 JSON 输出，这份正文是一段 JSON 字符串。

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/image-20261007114504938.png)![](./img/iiuggfcqunxuy43z-19.png)

接下来的代码把它交给：

```javascript
const reading = parseReading(completion)
```

`parseReading()` 先检查响应有没有完整结束，再用 `JSON.parse()` 将正文变成对象，最后用文件顶部的 `DashboardSchema` 校验字段。

拿到 `reading` 以后，`inspectDashboard()` 继续调用：

```javascript
prepareAgentInput(reading)
```

这个函数会检查必需字段有没有 `null`，并把缺失字段和 `uncertainties` 整理成 `confirmationQuestions`，也就是待确认项。

然后，根据待确认项决定状态：

```javascript
status: confirmationQuestions.length
  ? 'needs_confirmation'
  : 'extracted'
```

函数处理完以后，结果回到 `main()`，由 `printResult()` 打印，再写入 `outputs`。保存时还会记录来源文件名、图片 Hash、模型名和 API 返回的用量，方便知道这份读取结果来自哪张图片。

给大家画了个图，大家可以看下这个代码的关键逻辑：

![](02-%E5%9B%BE%E7%89%87%E7%90%86%E8%A7%A3%EF%BC%9A%E6%88%AA%E5%9B%BE%E9%87%8C%E7%9A%84%E4%BF%A1%E6%81%AF%E6%80%8E%E6%A0%B7%E8%BF%9B%E5%85%A5%20Agent.assets/%E5%9B%BE%E7%89%87%E8%AF%86%E5%88%AB%E4%BB%A3%E7%A0%81%E6%89%A7%E8%A1%8C%E6%B5%81%E7%A8%8B%E4%BF%A1%E6%81%AF%E5%9B%BE.png)![](./img/iiuggfcqunxuy43z-20.png)

然后大家也可以执行

```bash
npm run incomplete
```

这个就是咱们前面说过的被遮挡的图片，可以通过这个看到大模型发现了被遮挡的数据了。

这个就不给大家演示了

## 总结
这一节，咱们从一张销售看板截图出发，完成了图片读取、模型请求、结构化解析和缺失信息提示。

图片通过 Data URL 放进 User Message，与问题一起交给视觉模型。模型返回的 JSON 再由程序检查，整理成后续 Agent 可以使用的参考数据。

另外，当前图片只展示了汇总指标。要继续回答“哪些商品造成了销售额下降”，还需要读取商品与订单明细。

下一小节，咱们就从原始 Excel 和 CSV 开始，让 Agent 获得真正能够查询和计算的数据。

> 更新：2026-10-07 11:54 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/iiuggfcqunxuy43z>
