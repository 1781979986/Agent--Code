"""可取消的异步 JSON 请求；仅 DeepSeek 决策允许原例的一次自动重试。"""

import asyncio
import random

import httpx2


async def post_json(url, key, body, signal, *, timeout=60, retries=0, client_factory=httpx2.AsyncClient):
    signal.throw_if_aborted()
    async def request():
        async with client_factory(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            for attempt in range(retries + 1):
                response = None
                try:
                    async with asyncio.timeout(timeout):
                        response = await client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
                    retry_header = response.headers.get("x-should-retry")
                    retryable = retry_header == "true" or (retry_header != "false" and (response.status_code in (408, 409, 429) or response.status_code >= 500))
                    if response.is_error and retryable and attempt < retries:
                        response.raise_for_status()
                    # 供应商业务错误保留原 JSON，由各业务接口判断；HTTP 错误在最后一次也返回。
                    return response.status_code, response.json()
                except (httpx2.TransportError, httpx2.HTTPStatusError, TimeoutError):
                    if attempt >= retries:
                        raise
                    delay = 0.5 * (1 - random.random() * 0.25)
                    if response is not None:
                        try:
                            delay = float(response.headers["retry-after-ms"]) / 1000 if "retry-after-ms" in response.headers else float(response.headers["retry-after"])
                        except (KeyError, ValueError):
                            pass
                    await asyncio.sleep(delay if 0 < delay <= 60 else 0.5)
    # 取消会等待 HTTP 客户端 finally 完成，避免请求继续在后台运行。
    return await signal.run(request())
