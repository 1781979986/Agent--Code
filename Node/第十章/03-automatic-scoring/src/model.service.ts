import { Injectable } from '@nestjs/common'
import OpenAI from 'openai'
import type { ChatCompletionCreateParamsNonStreaming } from 'openai/resources/chat/completions'

type Request = Pick<ChatCompletionCreateParamsNonStreaming, 'messages' | 'tools' | 'response_format'>

@Injectable()
export class ModelService {
  /** 分别记录答题模型和评分模型；两次调用使用独立消息，不共享对话。 */
  settings() {
    const agent = process.env.DEEPSEEK_MODEL || 'deepseek-flash'
    return { agent, judge: process.env.JUDGE_MODEL || agent }
  }

  /** 仅检查配置是否存在，不打印密钥。 */
  assertConfigured() {
    if (!process.env.DEEPSEEK_API_KEY?.trim()) {
      throw new Error('请先在本节 .env 中配置 DEEPSEEK_API_KEY，格式见 README.md。')
    }
  }

  /** 调用兼容接口；超时或截断直接报告异常，不把缺失结果当成答案。 */
  async complete(role: 'agent' | 'judge', request: Request) {
    this.assertConfigured()
    const client = new OpenAI({
      apiKey: process.env.DEEPSEEK_API_KEY,
      baseURL: process.env.DEEPSEEK_BASE_URL || 'https://api.deepseek.com',
      timeout: 45_000,
      maxRetries: 0
    })
    const params = {
      ...request,
      model: this.settings()[role],
      stream: false as const,
      max_tokens: 1600,
      temperature: 0,
      thinking: { type: 'disabled' }
    }
    const completion = await client.chat.completions.create(params)
    const choice = completion.choices[0]
    if (!choice || !['stop', 'tool_calls'].includes(choice.finish_reason)) {
      throw new Error(`模型没有完整返回：${choice?.finish_reason || 'empty_choices'}`)
    }
    return choice.message
  }
}
