"""识别连接只负责转写；匹配 turnId 后处理音频，关闭连接时关闭上游。"""

import asyncio
import json
import re

from fastapi import WebSocketDisconnect

from ..common import json_text
from ..signal import CancelSignal
from .asr import open_recognition


class VoiceGateway:
    def __init__(self, recognition_factory=open_recognition):
        self.recognition_factory, self.active = recognition_factory, {}

    def disconnect(self, socket):
        active = self.active.pop(socket, None)
        if active:
            active["signal"].abort("录音已停止")

    async def handle(self, socket):
        await socket.accept()
        outgoing, tasks = asyncio.Queue(), set()
        async def sender():
            while True:
                await socket.send_text(await outgoing.get())
        sending = asyncio.create_task(sender())
        async def listen(data):
            self.disconnect(socket)
            identity = data.get("turnId")
            if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", identity):
                return
            signal = CancelSignal()
            def emit(event, details=None):
                if not signal.aborted:
                    outgoing.put_nowait(json_text({"event": event, "data": {**(details or {}), "turnId": identity}}))
            try:
                recognition = self.recognition_factory(signal, emit)
                self.active[socket] = {"id": identity, "signal": signal, "recognition": recognition}
                await recognition.done
            except Exception as error:
                emit("error", {"message": str(error)})
            finally:
                if (self.active.get(socket) or {}).get("signal") is signal:
                    self.active.pop(socket, None)
        try:
            while True:
                raw = await socket.receive_text()
                if len(raw.encode("utf-8")) > 128 * 1024:
                    await socket.close(code=1009)
                    break
                try:
                    packet = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(packet, dict) or not isinstance(packet.get("data"), dict):
                    continue
                event, data = packet.get("event"), packet["data"]
                if event == "listen":
                    task = asyncio.create_task(listen(data))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    continue
                active = self.active.get(socket)
                if not active or active["id"] != data.get("turnId"):
                    continue
                try:
                    if event == "audio":
                        active["recognition"].append(data.get("audio"))
                    elif event == "finish":
                        active["recognition"].finish()
                    elif event == "cancel":
                        self.disconnect(socket)
                except Exception as error:
                    outgoing.put_nowait(json_text({"event": "error", "data": {"turnId": active["id"], "message": str(error)}}))
                    self.disconnect(socket)
        except WebSocketDisconnect:
            pass
        finally:
            self.disconnect(socket)
            await asyncio.gather(*tasks, return_exceptions=True)
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
