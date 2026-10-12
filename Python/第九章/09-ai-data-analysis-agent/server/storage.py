"""保存本地数据版本与会话；写完临时文件再原子替换，避免半份 JSON。"""

import json
import re
from pathlib import Path

from .common import ROOT, json_value, new_id


def checked_id(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9-]{36}", identity):
        raise ValueError("记录 ID 无效")
    return identity


class Storage:
    def __init__(self, directory=None):
        self.directory = ROOT / "data" if directory is None else Path(directory)

    def dataset_dir(self, identity):
        return self.directory / "datasets" / checked_id(identity)

    def save(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{new_id()}.tmp")
        try:
            temporary.write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def dataset(self, identity):
        return self.load(self.dataset_dir(identity) / "metadata.json")

    def session(self, identity):
        return self.load(self.directory / "sessions" / f"{checked_id(identity)}.json")

    def save_session(self, session):
        self.save(self.directory / "sessions" / f"{checked_id(session['id'])}.json", session)
