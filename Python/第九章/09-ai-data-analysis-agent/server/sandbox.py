"""每次只挂载授权数据库副本；取消/超时先删除本次容器，再回收 CLI 和输入目录。"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from .common import ROOT, json_text, new_id, trim_text, utf16_length

IMAGE = "agent-course-analysis-python:chapter9-09"


async def docker(args, timeout=15):
    child = await asyncio.create_subprocess_exec("docker", *args, stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout)
        if len(stdout) > 1024 * 1024 or len(stderr) > 1024 * 1024:
            raise RuntimeError("Docker 管理命令输出过大")
        if child.returncode:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "Docker 管理命令失败")
        return stdout.decode("utf-8", errors="replace").strip()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


def container_args(name, directory):
    return ["create", "--name", name, "--label", "agent-course=chapter9-09", "--network", "none",
            "--read-only", "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--cpus", "1", "--memory", "256m", "--memory-swap", "256m", "--pids-limit", "64",
            "--init", "--log-driver", "none", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
            "--mount", f"type=bind,source={directory},target=/input,readonly", IMAGE]


async def start_and_collect(name, signal, timeout_ms, remove):
    child = await asyncio.create_subprocess_exec("docker", "start", "--attach", name,
                                                  stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    size = 0
    async def read(stream):
        nonlocal size
        data = bytearray()
        while chunk := await stream.read(8192):
            size += len(chunk)
            if size > 256 * 1024:
                raise RuntimeError("查询输出过大")
            data.extend(chunk)
        return data.decode("utf-8", errors="replace")
    readers = [asyncio.create_task(read(stream)) for stream in (child.stdout, child.stderr)]
    async def collect():
        stdout, stderr = await asyncio.gather(*readers)
        await child.wait()
        if not stdout:
            raise RuntimeError(stderr or "查询没有返回结果")
        return stdout
    output, stopped = asyncio.create_task(collect()), asyncio.create_task(signal._event.wait())
    try:
        done, _ = await asyncio.wait((output, stopped), timeout=timeout_ms / 1000, return_when=asyncio.FIRST_COMPLETED)
        signal.throw_if_aborted()
        if output not in done:
            raise RuntimeError("查询超过 10 秒，已停止")
        return await output
    except BaseException:
        # 只杀 docker 客户端不会停止容器，必须先回收已知的本次任务容器。
        await remove()
        raise
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
        for task in (output, stopped, *readers):
            task.cancel()
        await asyncio.gather(output, stopped, *readers, return_exceptions=True)


async def execute_query(database_path, sql, signal, timeout_ms=10000, *, docker_impl=docker,
                        start_impl=start_and_collect, work_root=None):
    signal.throw_if_aborted()
    if not isinstance(sql, str) or not trim_text(sql) or utf16_length(sql) > 12000:
        raise ValueError("查询为空或过长")
    work = ROOT / ".work" if work_root is None else Path(work_root)
    if "," in str(work.resolve()):
        raise ValueError("项目路径不能包含英文逗号")
    work.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="query-", dir=work))
    directory.chmod(0o755)
    name, attempted = "analysis-" + new_id(), False
    async def remove():
        try:
            await docker_impl(["rm", "--force", name])
        except RuntimeError as error:
            if "No such container" not in str(error):
                raise
    try:
        shutil.copyfile(database_path, directory / "data.duckdb")
        (directory / "data.duckdb").chmod(0o444)
        (directory / "request.json").write_text(json_text({"sql": sql}), encoding="utf-8")
        (directory / "request.json").chmod(0o444)
        signal.throw_if_aborted()
        # create 超时/取消也尝试清理本次名字，避免 daemon 完成后留下一只迟到容器。
        attempted = True
        await signal.run(docker_impl(container_args(name, directory), timeout=20))
        signal.throw_if_aborted()
        raw = await start_impl(name, signal, timeout_ms, remove)
        signal.throw_if_aborted()
        result = json.loads(raw)
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "查询未完成")
        if result.get("truncated"):
            raise ValueError("结果超过 100 行，请缩小范围或先汇总")
        if not isinstance(result.get("rows"), list) or len(result["rows"]) > 100:
            raise ValueError("查询返回结构异常")
        return result["rows"]
    except RuntimeError as error:
        if "No such image" in str(error):
            raise RuntimeError("缺少运行镜像，请先执行 uv run python scripts/build_sandbox.py") from error
        raise
    finally:
        try:
            if attempted:
                await remove()
        finally:
            shutil.rmtree(directory)
