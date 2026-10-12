"""重新生成可手算的销售表与质量问题表；不自动生成或修改看板截图。"""

import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.common import ROOT  # noqa: E402

HEADERS = ["销售日期", "区域", "商品名称", "实付金额（元）", "退款金额（元）"]
VALUES = [
    ["2026-07-15", "华东", "咖啡机", 20000, 2000], ["2026-07-15", "华东", "键盘", 12000, 0],
    ["2026-08-15", "华东", "咖啡机", 16000, 2000], ["2026-08-15", "华东", "键盘", 11000, 0],
    ["2026-09-15", "华东", "咖啡机", 11000, 1000], ["2026-09-15", "华东", "键盘", 10000, 0],
    ["2026-07-15", "华南", "咖啡机", 9000, 0], ["2026-07-15", "华南", "键盘", 6000, 0],
    ["2026-08-15", "华南", "咖啡机", 11000, 0], ["2026-08-15", "华南", "键盘", 6000, 0],
    ["2026-09-15", "华南", "咖啡机", 13000, 0], ["2026-09-15", "华南", "键盘", 5000, 0],
    ["2026-07-15", "华北", "咖啡机", 10000, 0], ["2026-07-15", "华北", "键盘", 10000, 0],
    ["2026-08-15", "华北", "咖啡机", 12000, 0], ["2026-08-15", "华北", "键盘", 10000, 0],
    ["2026-09-15", "华北", "咖啡机", 9000, 0], ["2026-09-15", "华北", "键盘", 12000, 0],
]


def create_samples(directory=None):
    directory = ROOT / "samples" if directory is None else Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(); sales = workbook.active; sales.title = "销售明细"
    sales.append(HEADERS)
    for row in VALUES:
        sales.append(row)
    for key in "ABCDE":
        sales.column_dimensions[key].width = 24
    notes = workbook.create_sheet("指标说明")
    for row in [["指标", "定义"], ["实付金额", "未扣退款，人民币元"], ["净销售额", "实付金额减退款金额，人民币元"]]:
        notes.append(row)
    workbook.save(directory / "sales-demo.xlsx")
    (directory / "sales-demo.csv").write_text("\ufeff" + "\n".join(",".join(map(str, row)) for row in [HEADERS, *VALUES]), encoding="utf-8")
    for row in [VALUES[0], ["2026-09-31", "华东", "咖啡机", "未知", 0], ["2026-09-15", "华南", "键盘", 100, 200]]:
        sales.append(row)
    workbook.save(directory / "sales-issues.xlsx")
    workbook.close()


if __name__ == "__main__":
    create_samples()
    print("样例已生成：samples/sales-demo.xlsx、sales-demo.csv、sales-issues.xlsx；看板截图保持原样。")
