"""BuildKit 构建独立 Python 镜像；构建上下文只包含固定文件，不打包数据或配置。"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.common import ROOT  # noqa: E402
from server.sandbox import IMAGE  # noqa: E402


def build_environment(env=None, platform=None, read_proxy=None):
    env = os.environ if env is None else env
    result = dict(env)
    if (sys.platform if platform is None else platform) != "darwin":
        return result
    if (env.get("HTTP_PROXY") or env.get("http_proxy")) and (env.get("HTTPS_PROXY") or env.get("https_proxy")):
        return result
    if read_proxy is None:
        read_proxy = lambda: subprocess.check_output(["scutil", "--proxy"], text=True, timeout=3)
    try:
        settings = read_proxy()
    except Exception:
        return result
    def value(name):
        match = re.search(r"^\s*" + name + r"\s*:\s*(\S+)", settings, re.MULTILINE)
        return match[1] if match else ""
    for protocol in ("HTTP", "HTTPS"):
        key = protocol + "_PROXY"
        if env.get(key) or env.get(key.lower()) or value(protocol + "Enable") != "1":
            continue
        host, port = value(protocol + "Proxy"), value(protocol + "Port")
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            continue
        hostname = f"[{host}]" if ":" in host and not host.startswith("[") else host
        result[key] = f"http://{hostname}:{int(port)}"
    return result


def main():
    work = ROOT / ".work"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=work) as context:
        for name in ("Dockerfile", "requirements.lock", "entry.py", "query_worker.py", "common.py"):
            shutil.copyfile(ROOT / "runtime" / name, Path(context) / name)
        result = subprocess.run(["docker", "buildx", "build", "--load", "-t", IMAGE, context], env=build_environment())
        if result.returncode:
            raise RuntimeError(f"镜像构建失败，退出码 {result.returncode}")
    print(f"查询运行镜像构建完成：{IMAGE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
