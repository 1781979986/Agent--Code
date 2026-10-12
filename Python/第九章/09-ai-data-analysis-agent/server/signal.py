"""一轮任务的取消信号与事件出口；旧轮次不能继续向页面发送结果。"""

import asyncio
import inspect


class TurnCancelled(Exception):
    pass


class CancelSignal:
    def __init__(self):
        self._event = asyncio.Event()
        self.reason = TurnCancelled("本轮已停止")

    @property
    def aborted(self):
        return self._event.is_set()

    def abort(self, reason=None):
        if not self.aborted:
            self.reason = reason if isinstance(reason, Exception) else TurnCancelled(reason or "本轮已停止")
            self._event.set()

    def throw_if_aborted(self):
        if self.aborted:
            raise self.reason

    async def run(self, awaitable):
        """把取消传给可取消的网络协程；不用于已经开始的同步 Docker 查询。"""
        if self.aborted:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            self.throw_if_aborted()
        task = asyncio.ensure_future(awaitable)
        stopped = asyncio.create_task(self._event.wait())
        try:
            await asyncio.wait((task, stopped), return_when=asyncio.FIRST_COMPLETED)
            self.throw_if_aborted()
            return await task
        finally:
            # 即使外层任务被取消，也等待网络协程的 finally 关闭连接，不遗留后台请求。
            if not task.done():
                task.cancel()
            stopped.cancel()
            await asyncio.gather(task, stopped, return_exceptions=True)
