import { execFileSync, spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const image = 'agent-course-analysis:chapter9-09'
const directory = fileURLToPath(new URL('../runtime', import.meta.url))

/** BuildKit 的认证请求由 CLI 发出，为它补充 macOS 已启用的系统代理。 */
export function buildEnvironment(env = process.env, platform = process.platform, readProxy = () => execFileSync('scutil', ['--proxy'], { encoding: 'utf8', timeout: 3000 })) {
  const result = { ...env }
  if (platform !== 'darwin') return result
  if ((env.HTTP_PROXY || env.http_proxy) && (env.HTTPS_PROXY || env.https_proxy)) return result

  let settings
  try { settings = readProxy() } catch { return result }
  const value = name => settings.match(new RegExp(`^\\s*${name}\\s*:\\s*(\\S+)`, 'm'))?.[1]
  for (const protocol of ['HTTP', 'HTTPS']) {
    const key = `${protocol}_PROXY`
    if (env[key] || env[key.toLowerCase()] || value(`${protocol}Enable`) !== '1') continue
    const host = value(`${protocol}Proxy`)
    const port = Number(value(`${protocol}Port`))
    if (!host || !Number.isInteger(port) || port < 1 || port > 65535) continue
    // HTTPSProxy 表示 HTTPS 请求使用的代理，连接代理本身仍使用 HTTP CONNECT。
    const hostname = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
    result[key] = `http://${hostname}:${port}`
  }
  return result
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const env = buildEnvironment()
  if (env.HTTP_PROXY !== process.env.HTTP_PROXY || env.HTTPS_PROXY !== process.env.HTTPS_PROXY) {
    console.log('本次镜像构建使用 macOS 已配置的系统代理。')
  }
  const child = spawn('docker', ['buildx', 'build', '--load', '-t', image, directory], {
    stdio: 'inherit', env
  })
  child.once('error', error => {
    console.error('无法启动 Docker 构建：', error.message)
    process.exitCode = 1
  })
  child.once('close', (code, signal) => {
    process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1)
    if (code === 0) console.log(`查询运行镜像构建完成：${image}`)
  })
}
