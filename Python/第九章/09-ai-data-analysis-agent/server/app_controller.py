"""HTTP 入口只接收业务参数，文件路径和权限由服务端构造；进度使用 NDJSON。"""

import asyncio
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.datastructures import UploadFile

from .common import ROOT, json_text, trim_text, utf16_length
from .report import result_csv
from .signal import CancelSignal


def identity(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("记录 ID 无效")
    UUID(value)
    return value


def mode(value):
    if value not in ("replay", "ai"):
        raise ValueError("mode: 请选择 replay 或 ai")
    return value


async def upload_form(request):
    form = await request.form(max_files=1, max_fields=4, max_part_size=5 * 1024 * 1024)
    file = form.get("file")
    if not isinstance(file, UploadFile):
        await form.close()
        raise ValueError("请选择文件")
    try:
        data = await file.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(413, "单个文件大小不能超过 5 MiB")
        return data, file.filename or "", file.content_type or "", form.get("mode")
    finally:
        await form.close()


def stream_response(run, timeout=150):
    async def events():
        signal, queue = CancelSignal(), asyncio.Queue()
        def send(event, data):
            if not signal.aborted:
                queue.put_nowait({"event": event, "data": data})
        timer = asyncio.get_running_loop().call_later(timeout, signal.abort, "本轮超过 150 秒，请重试或缩小问题范围")
        async def produce():
            try:
                await run(signal, send)
            except Exception as error:
                queue.put_nowait({"event": "error", "data": {"message": str(error)}})
            finally:
                queue.put_nowait(None)
        producer = asyncio.create_task(produce())
        try:
            while (packet := await queue.get()) is not None:
                yield json_text(packet) + "\n"
        finally:
            # StreamingResponse 在客户端关闭时结束生成器；先取消供应商/容器，再等待清理。
            timer.cancel()
            signal.abort("用户停止本轮")
            await asyncio.gather(producer, return_exceptions=True)
    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


def create_router(storage, datasets, analyses, multimodal, env):
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health():
        return {"ok": True}

    @router.get("/meta")
    def meta():
        speech = bool(env.get("DASHSCOPE_API_KEY") and env.get("DASHSCOPE_BASE_URL"))
        return {"ai": bool(env.get("DEEPSEEK_API_KEY")), "speech": speech, "vision": speech,
                "model": env.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"}

    @router.get("/datasets")
    def list_datasets():
        return datasets.list()

    @router.post("/uploads", status_code=201)
    async def upload(request: Request):
        data, filename, _, _ = await upload_form(request)
        return await asyncio.to_thread(datasets.upload, data, filename)

    @router.post("/datasets", status_code=201)
    async def import_data(request: Request):
        data = await request.json()
        mapping = data.get("mapping")
        # 依照原请求 Schema 固定字段顺序，保证 checksum 跨语言一致。
        if not isinstance(mapping, dict) or any(not isinstance(mapping.get(key), str) for key in ("date", "region", "product", "paid", "refund")):
            raise ValueError("mapping: 请提供五个字段映射")
        mapping = {key: mapping[key] for key in ("date", "region", "product", "paid", "refund")}
        if not isinstance(data.get("sheet"), str) or not data["sheet"]:
            raise ValueError("sheet: 请选择工作表")
        return await asyncio.to_thread(datasets.import_data, identity(data.get("uploadId")), data["sheet"], mapping)

    @router.get("/datasets/{dataset_id}/rows")
    def rows(dataset_id: str):
        return storage.load(storage.dataset_dir(dataset_id) / "rows.json")[:20]

    @router.get("/samples/{name}")
    def sample(name: str):
        if name not in ("sales-demo.xlsx", "sales-demo.csv", "sales-issues.xlsx", "dashboard.png"):
            raise ValueError("样例不存在")
        return Response((ROOT / "samples" / name).read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @router.get("/sessions")
    def list_sessions():
        return analyses.list_sessions()

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request):
        data = await request.json()
        return analyses.create_session(identity(data.get("datasetId")))

    @router.get("/sessions/{session_id}")
    def session(session_id: str):
        return storage.session(session_id)

    @router.post("/sessions/{session_id}/analyze")
    async def analyze(session_id: str, request: Request):
        data = await request.json()
        question = data.get("question")
        if not isinstance(question, str) or not 1 <= utf16_length(trim_text(question)) <= 2000:
            raise ValueError("question: 问题须为 1～2000 字符")
        selected_mode, question = mode(data.get("mode")), trim_text(question)
        async def run(signal, send):
            report = await analyses.analyze(session_id, question, selected_mode, signal, lambda progress: send("progress", progress))
            send("report", report)
        return stream_response(run)

    @router.get("/sessions/{session_id}/reports/{report_id}/export")
    def export(session_id: str, report_id: str):
        return Response(analyses.export(session_id, report_id), media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="analysis-{report_id}.html"'})

    @router.get("/sessions/{session_id}/reports/{report_id}/csv")
    def csv(session_id: str, report_id: str):
        report = analyses.find_report(session_id, report_id)
        return Response(result_csv(report["table"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="result-{report_id}.csv"'})

    async def timed(run, seconds):
        signal = CancelSignal()
        timer = asyncio.get_running_loop().call_later(seconds, signal.abort, "请求超时，请重试")
        try:
            return await run(signal)
        finally:
            timer.cancel()

    @router.post("/images", status_code=201)
    async def image(request: Request):
        data, _, mime, raw_mode = await upload_form(request)
        selected_mode = mode(raw_mode)
        return await timed(lambda signal: multimodal.inspect(data, mime, selected_mode, signal), 65)

    @router.post("/images/compare", status_code=201)
    async def compare(request: Request):
        data = await request.json()
        return await timed(lambda signal: multimodal.compare(identity(data.get("datasetId")), data.get("facts"), signal), 30)

    @router.post("/sessions/{session_id}/reports/{report_id}/speech")
    def speak(session_id: str, report_id: str):
        report = analyses.find_report(session_id, report_id)
        sentences = re.findall(r"[^。！？\n]+[。！？]?", report["answer"]) or [report["answer"]]
        async def run(signal, send):
            for seq, sentence in enumerate(sentences[:8]):
                signal.throw_if_aborted()
                # 保留本项目的上限：8 句、每句 500 Unicode 字符，非 08 的回答分段上限。
                audio_url = await multimodal.speak(sentence[:500], signal)
                send("audio", {"seq": seq, "audioUrl": audio_url})
            send("done", {})
        return stream_response(run)

    return router
