"""原件 → 确认工作表/映射 → 不可变数据版本；质量检查通过才建立查询库。"""

import hashlib
from pathlib import Path

import duckdb

from .common import json_text, new_id, now
from .storage import checked_id
from .table_parser import normalize, parse_file, preview_sheet


class DatasetService:
    def __init__(self, storage):
        self.storage = storage

    def list(self):
        directory = self.storage.directory / "datasets"
        datasets = []
        for path in sorted(directory.iterdir()) if directory.exists() else []:
            try:
                datasets.append(self.storage.dataset(path.name))
            except FileNotFoundError:
                continue
        return sorted(datasets, key=lambda item: item["createdAt"], reverse=True)

    def upload(self, data, filename):
        sheets = parse_file(data, filename)
        identity = new_id()
        directory = self.storage.directory / "uploads" / identity
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "source").write_bytes(data)
        result = {"id": identity, "filename": Path(filename).name, "sheets": [preview_sheet(sheet) for sheet in sheets]}
        self.storage.save(directory / "preview.json", result)
        return result

    def import_data(self, identity, sheet_name, mapping):
        directory = self.storage.directory / "uploads" / checked_id(identity)
        preview = self.storage.load(directory / "preview.json")
        data = (directory / "source").read_bytes()
        sheet = next((item for item in parse_file(data, preview["filename"]) if item["name"] == sheet_name), None)
        if sheet is None:
            raise ValueError("所选工作表不存在")
        normalized = normalize(sheet, mapping)
        rows, issues = normalized["rows"], normalized["issues"]
        # 与原例相同：文件原始字节 + 无空格的 [工作表, 映射] JSON；映射顺序是协议的一部分。
        checksum = hashlib.sha256(data + json_text([sheet_name, mapping]).encode("utf-8")).hexdigest()
        existing = self.list()
        repeated = next((item for item in existing if item["checksum"] == checksum), None)
        if repeated:
            return {"dataset": repeated, "repeated": True}
        dates = sorted(row["sold_at"] for row in rows if isinstance(row.get("sold_at"), str))
        dataset = {"id": new_id(), "name": preview["filename"], "sheet": sheet_name, "checksum": checksum,
                   "version": 1 + max([0] + [item["version"] for item in existing
                                            if item["name"] == preview["filename"] and item["sheet"] == sheet_name]),
                   "createdAt": now(), "rowCount": len(rows), "status": "needs_review" if issues else "ready",
                   "issues": issues, "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
                   "regions": list(dict.fromkeys(row["region"] for row in rows if row.get("region"))),
                   "products": list(dict.fromkeys(row["product_name"] for row in rows if row.get("product_name"))),
                   "mapping": mapping}
        target = self.storage.dataset_dir(dataset["id"])
        target.mkdir(parents=True, exist_ok=True)
        (target / ("source.csv" if preview["filename"].lower().endswith(".csv") else "source.xlsx")).write_bytes(data)
        self.storage.save(target / "rows.json", rows)
        if not issues:
            connection = duckdb.connect(str(target / "data.duckdb"))
            try:
                connection.execute("CREATE TABLE sales (source_row INTEGER, sold_at DATE, region VARCHAR, product_name VARCHAR, paid_amount DECIMAL(18,2), refund_amount DECIMAL(18,2))")
                connection.execute("BEGIN TRANSACTION")
                for row in rows:
                    connection.execute("INSERT INTO sales VALUES (?,?,?,?,?,?)", [row[key] for key in
                                       ("source_row", "sold_at", "region", "product_name", "paid_amount", "refund_amount")])
                connection.execute("COMMIT")
            finally:
                connection.close()
        # 最后发布元信息，未完成的导入不会被误列为可查询版本。
        self.storage.save(target / "metadata.json", dataset)
        return {"dataset": dataset, "repeated": False}
