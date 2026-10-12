"""对齐课程 JSON、时间与字符串边界，路径始终从源码位置确定。"""

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent


def trusted_base_url(value):
    url = urlsplit(value)
    if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
            or url.query or url.fragment or url.path.removesuffix("/") != "/compatible-mode/v1"
            or not re.fullmatch(r"(?:dashscope(?:-intl)?\.aliyuncs\.com|[A-Za-z0-9_-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com)", url.hostname or "")):
        raise ValueError("请填写百炼北京或新加坡地域的真实兼容接口地址")
    return urlunsplit(("https", url.hostname, "/compatible-mode/v1", "", ""))


def new_id():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def trim_text(text):
    # ECMAScript trim 包括 BOM，Python strip 的默认字符集合略有不同。
    return text.strip("\u0009\u000b\u000c\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff\u000a\u000d\u2028\u2029")


def js_sorted(values):
    # JS sort 默认按 UTF-16 单元排序；非常用字符也不能改变图表和表格顺序。
    return sorted(values, key=lambda text: text.encode("utf-16-be", errors="surrogatepass"))


def utf16_length(text):
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def utf16_slice(text, length):
    return text.encode("utf-16-le", errors="surrogatepass")[:length * 2].decode("utf-16-le", errors="surrogatepass")


def js_string(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def json_value(value):
    if isinstance(value, (datetime, date)):
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        return value.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def json_text(value):
    return json.dumps(json_value(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
