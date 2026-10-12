"""容器只读取固定挂载位置，返回一份有界 JSON。"""

import json
import sys
from pathlib import Path

from query_worker import run_read_only_query


try:
    request = json.loads(Path("/input/request.json").read_text(encoding="utf-8"))
    result = run_read_only_query("/input/data.duckdb", request["sql"])
    output = {"ok": True, **result}
except Exception as error:
    output = {"ok": False, "message": str(error).encode("utf-16-le", errors="surrogatepass")[:3200].decode("utf-16-le", errors="surrogatepass")}
sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
sys.exit(0 if output["ok"] else 1)
