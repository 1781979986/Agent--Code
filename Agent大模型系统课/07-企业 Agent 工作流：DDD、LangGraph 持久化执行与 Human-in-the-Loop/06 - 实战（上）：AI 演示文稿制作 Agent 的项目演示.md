# 06 - 实战（上）：AI 演示文稿制作 Agent 的项目演示

第七章写到这里，咱们已经解决了三个问题。

+ 服务中断以后，工作流可以从 Checkpoint 继续执行
+ 大纲生成以后，可以暂停等待用户审核
+ 部分页面生成失败以后，也可以保留已经成功的结果，只补做失败页面

不过，前面写的都是针对单个问题的最小案例。

从这一小节开始，咱们把这些能力组装起来，实现一个完整的 **AI 演示文稿制作 Agent**。

整个项目的业务流程大概是这样的：

> 用户提供一份参考资料，再填写演示主题、目标观众和页面数量，Agent 就可以生成内容大纲。
>
> 用户审核大纲以后，系统继续制作每一页。中途出现失败，可以接着完成，同时支持固定某一个的修改。
>
> 最后，系统会检查所有页面的版本，导出一份可以继续编辑的 `.pptx` 文件。
>

一张图表示：

![](./img/zywgwwgccg0mag2u-1.png)![](06-%E5%AE%9E%E6%88%98%EF%BC%88%E4%B8%8A%EF%BC%89%EF%BC%9AAI%20%E6%BC%94%E7%A4%BA%E6%96%87%E7%A8%BF%E5%88%B6%E4%BD%9C%20Agent%20%E7%9A%84%E9%A1%B9%E7%9B%AE%E6%BC%94%E7%A4%BA%E3%80%81%E8%BF%90%E8%A1%8C%E4%B8%8E%E9%9C%80%E6%B1%82%E5%88%86%E6%9E%90.assets/ChatGPT%20Image%202026%E5%B9%B49%E6%9C%8820%E6%97%A5%2010_16_21.png)

## 项目如何运行
> 课程源码：`https://github.com/lgd8981289/Agent--Code`
>
> 本项目代码位于：`Code/Node/第七章/06-ai-presentation-agent`
>

首先安装依赖，启动 PostgreSQL，并初始化数据库：

```bash
npm install
docker compose up -d --wait
npm run setup
```

不要忘记 `.env` 文件：

```latex
MODEL_MODE=ai
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-v4-flash
POSTGRES_URI=postgresql://presentation_course:presentation_course@localhost:5436/presentation_agent
```

最后启动项目：

```bash
npm run dev
```

这条命令会同时启动 NestJS 服务端和 前端页面。

浏览器访问：

```latex
http://localhost:5182
```

服务端 API 地址是：

```latex
http://localhost:4310/api
```

启动之后，就长这个样子：

![](06-%E5%AE%9E%E6%88%98%EF%BC%88%E4%B8%8A%EF%BC%89%EF%BC%9AAI%20%E6%BC%94%E7%A4%BA%E6%96%87%E7%A8%BF%E5%88%B6%E4%BD%9C%20Agent%20%E7%9A%84%E9%A1%B9%E7%9B%AE%E6%BC%94%E7%A4%BA%E3%80%81%E8%BF%90%E8%A1%8C%E4%B8%8E%E9%9C%80%E6%B1%82%E5%88%86%E6%9E%90.assets/image-20260920154612232.png)![](./img/zywgwwgccg0mag2u-2.png)

模式的话，咱们还是提供了两个：`Replay` 和 `AI 模式`。

+ Replay  模式主要是为了快速标准流程
+ AI 模式，就是正了八经的 AI 方案了。

## 完整演示一次项目
[此处为语雀卡片，点击链接查看](https://www.yuque.com/lgdsunday/ab1dy3/zywgwwgccg0mag2u#nZrWd)

到这里，项目的核心功能就全部跑通了。

下一小节，咱们就从“开始制作”按钮正式进入项目代码。

> 更新：2026-09-20 16:07 · 原文：<https://www.yuque.com/lgdsunday/ab1dy3/zywgwwgccg0mag2u>
