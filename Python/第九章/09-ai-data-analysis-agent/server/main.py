"""启动本机 FastAPI 与实时识别；只从进程环境取得配置，不自动加载环境文件。"""

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .analysis_service import AnalysisService
from .app_controller import create_router
from .dataset_service import DatasetService
from .model import AnalysisProvider
from .multimodal_service import MultimodalService
from .storage import Storage
from .voice.voice_gateway import VoiceGateway


def create_app(*, storage=None, env=None, provider=None, execute=None, multimodal=None, gateway=None):
    env = os.environ if env is None else env
    storage = Storage() if storage is None else storage
    datasets = DatasetService(storage)
    analyses = AnalysisService(storage, provider=AnalysisProvider(env) if provider is None else provider, execute=execute)
    multimodal = MultimodalService(storage, env, execute=execute) if multimodal is None else multimodal
    gateway = VoiceGateway() if gateway is None else gateway
    web_port = int(env.get("WEB_PORT") or 5187)
    origins = {f"http://localhost:{web_port}", f"http://127.0.0.1:{web_port}"}

    @asynccontextmanager
    async def lifespan(app):
        yield
        for signal in list(analyses.busy.values()):
            signal.abort("服务已停止")
        for socket in list(gateway.active):
            gateway.disconnect(socket)
            await socket.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.analyses, app.state.datasets, app.state.storage = analyses, datasets, storage

    async def errors(request: Request, error):
        if isinstance(error, FileNotFoundError):
            message = "记录或文件不存在，请检查数据集与样例文件"
        elif isinstance(error, HTTPException):
            message = str(error.detail)
        elif isinstance(error, (ValidationError, RequestValidationError)):
            message = "；".join(".".join(map(str, item["loc"])) + ": " + item["msg"] for item in error.errors())
        else:
            message = str(error) or "请求失败"
        return JSONResponse({"message": message}, status_code=error.status_code if isinstance(error, HTTPException) else 400)
    for kind in (Exception, HTTPException, RequestValidationError):
        app.add_exception_handler(kind, errors)
    app.include_router(create_router(storage, datasets, analyses, multimodal, env))

    @app.websocket("/voice")
    async def voice(socket: WebSocket):
        if socket.headers.get("origin") not in origins:
            await socket.close(code=1008)
            return
        await gateway.handle(socket)
    return app


def main():
    print(f"启动数据分析服务；页面：http://localhost:{os.environ.get('WEB_PORT') or 5187}")
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT") or 4317),
                ws="websockets-sansio", ws_max_size=128 * 1024, server_header=False)


if __name__ == "__main__":
    main()
