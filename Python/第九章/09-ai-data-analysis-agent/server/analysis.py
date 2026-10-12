"""标准查询与确定性报告：模型只决定计划，数字、降幅和图表来自真实查询。"""

import calendar
import math
import re

from .common import js_sorted, js_string, new_id, now

METRIC_NAME = {"net": "净销售额", "paid": "实付金额", "refund": "退款金额"}
METRIC_SQL = {"net": "paid_amount - refund_amount", "paid": "paid_amount", "refund": "refund_amount"}


def js_round(value):
    # Math.round 半数向 +∞，不同于 Python round 的偶数舍入，负贡献也要对齐。
    return math.floor(value + 0.5)


def amount(value):
    try:
        number = float(value)
        if value is None or not math.isfinite(number):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("查询金额为空或无效") from None
    return js_round(number * 100) / 100


def money(value):
    return f"{value:,.2f}"


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def build_sql(spec):
    """标准 SQL 供 Replay 与 AI 独立口径复核共用，用户不能指定数据库路径。"""
    where = f"sold_at >= DATE {quote(spec['start'])} AND sold_at <= DATE {quote(spec['end'])}"
    if spec["region"]:
        where += f" AND region = {quote(spec['region'])}"
    summed = f"ROUND(SUM({METRIC_SQL[spec['metric']]}), 2) AS amount"
    if spec["kind"] == "summary":
        return f"SELECT region, {summed} FROM sales WHERE {where} GROUP BY region ORDER BY region"
    if spec["kind"] == "contribution":
        return f"SELECT strftime(sold_at, '%Y-%m') AS month, product_name AS product, {summed} FROM sales WHERE {where} GROUP BY month, product ORDER BY month, product"
    return f"SELECT strftime(sold_at, '%Y-%m') AS month, region, {summed} FROM sales WHERE {where} GROUP BY month, region ORDER BY month, region"


def month_sequence(start, end):
    result = []
    year, month = int(start[:4]), int(start[5:7])
    while f"{year:04d}-{month:02d}" <= end[:7] and len(result) < 120:
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if f"{year:04d}-{month:02d}" <= end[:7]:
        raise ValueError("本项目每次最多分析 120 个月，请缩小时间范围")
    return result


def month_end(year, month):
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def replay_decision(question, dataset, previous):
    """只识别公开示例问题；固定的是查询计划，不是数据库金额。"""
    if not dataset["start"] or not dataset["end"]:
        raise ValueError("数据集缺少日期范围")
    spec = {"kind": "trend", "metric": "net", "start": dataset["start"][:7] + "-01",
            "end": month_end(int(dataset["end"][:4]), int(dataset["end"][5:7])), "region": None, **(previous or {})}
    def no_query(route, message):
        return {"route": route, "spec": None, "sql": None, "message": message}
    if re.search(r"天气|预测|为什么.*不买|广告效果", question):
        return no_query("insufficient", "当前销售表只能支持金额与商品变化的计算，无法证明购买意愿、广告效果或未来销量。请补充相应资料。")
    if not re.search(r"区域|销售|净额|实付|退款|商品|降幅|趋势|华东|华南|华北", question):
        return no_query("clarify", "Replay 支持页面里的示例问题。自由提问请切换 AI 分析，或明确要统计的金额、时间和区域。")
    if re.search(r"实付|不扣退款", question):
        spec["metric"] = "paid"
    elif "退款金额" in question:
        spec["metric"] = "refund"
    elif re.search(r"净销售|扣除退款", question):
        spec["metric"] = "net"
    if re.search(r"全部区域|所有区域", question):
        spec["region"] = None
    else:
        spec["region"] = next((region for region in dataset["regions"] if region in question), None) or spec["region"]
    dates = [(int(year or dataset["end"][:4]), int(month)) for year, month in re.findall(r"(?:(20[0-9]{2})年)?([0-9]{1,2})月", question)]
    if dates:
        if any(month < 1 or month > 12 for _, month in dates):
            raise ValueError("月份无效")
        spec["start"] = f"{dates[0][0]:04d}-{dates[0][1]:02d}-01"
        spec["end"] = month_end(*dates[-1])
    spec["kind"] = "contribution" if re.search(r"商品|贡献", question) else "decline" if re.search(r"连续|下降", question) else "trend" if re.search(r"按月|趋势", question) else "summary"
    if spec["kind"] == "contribution" and not spec["region"]:
        return no_query("clarify", "要分解哪个区域的商品降幅？例如：只看华东，哪些商品贡献了主要降幅。")
    return {"route": "query", "spec": spec, "sql": build_sql(spec), "message": "按问题确定口径后查询销售表"}


def build_report(question, mode, evidence):
    spec, rows = evidence["spec"], evidence["rows"]
    notes = ["金额单位为人民币元；净销售额 = 实付金额 - 退款金额。", "数据只能支持变化和贡献计算，不能单独证明经营原因。"]
    table, chart, answer = rows, None, ""
    metric = METRIC_NAME[spec["metric"]]
    if not rows:
        answer = "当前筛选条件没有查询到明细。请核对日期和区域；空结果不代表销售额为零。"
    elif spec["kind"] == "summary":
        total = sum(amount(row["amount"]) for row in rows)
        region = f"，{spec['region']}" if spec["region"] else ""
        answer = f"{spec['start']} 至 {spec['end']}{region}的{metric}合计 {money(total)} 元。下表列出各区域的实际查询结果。"
        chart = {"kind": "bar", "title": f"区域{metric}", "categories": [js_string(row["region"]) for row in rows],
                 "series": [{"name": metric, "values": [amount(row["amount"]) for row in rows]}], "unit": "元"}
    elif spec["kind"] == "contribution":
        months = month_sequence(spec["start"], spec["end"])
        if len(months) < 2:
            answer = "商品降幅需要两个相邻月份的数据。请扩大日期范围。"
        else:
            before, after = months[-2:]
            products = js_sorted(set(js_string(row["product"]) for row in rows))
            def at(product, month):
                return next((row for row in rows if row["product"] == product and row["month"] == month), None)
            if any(at(product, month) is None for product in products for month in (before, after)):
                answer = f"{before} 或 {after} 缺少部分商品记录，无法把缺失值自动当成零。请核对商品明细后再计算降幅贡献。"
                notes.append("缺少记录的商品没有参与贡献计算。")
            else:
                differences = [{"商品": product, "上月金额": amount(at(product, before)["amount"]),
                                "本月金额": amount(at(product, after)["amount"]),
                                "降幅金额": amount(at(product, before)["amount"]) - amount(at(product, after)["amount"])} for product in products]
                drop = amount(sum(item["降幅金额"] for item in differences))
                table = [{**item, "降幅金额": amount(item["降幅金额"]),
                          "净降幅贡献(%)": js_round(item["降幅金额"] / drop * 10000) / 100 if drop > 0 else None} for item in differences]
                if drop > 0:
                    detail = "；".join(f"{row['商品']}贡献 {money(row['降幅金额'])} 元，占净降幅 {js_string(row['净降幅贡献(%)'])}%" for row in table)
                    answer = f"{spec['region']}从 {before} 到 {after}的{metric}减少 {money(drop)} 元。{detail}。"
                else:
                    answer = f"{spec['region']}从 {before} 到 {after}没有净下降，因此不计算降幅占比。"
                notes.append("使用最后两个相邻月份比较；增长商品可能出现负贡献，净降幅为零时不计算百分比。")
                chart = {"kind": "bar", "title": f"{before} → {after} 商品降幅", "categories": products,
                         "series": [{"name": "降幅金额", "values": [row["降幅金额"] for row in table]}], "unit": "元"}
    else:
        months = month_sequence(spec["start"], spec["end"])
        regions = js_sorted(set(js_string(row["region"]) for row in rows))
        def at(region, month):
            row = next((row for row in rows if row["region"] == region and row["month"] == month), None)
            return amount(row["amount"]) if row is not None else None
        series = [{"name": region, "values": [at(region, month) for month in months]} for region in regions]
        chart = {"kind": "line", "title": f"按月{metric}趋势", "categories": months, "series": series, "unit": "元"}
        # 缺失月份保留断点，不能补 0，也不能跨过缺口判断连续下降。
        if any(None in item["values"] for item in series):
            notes.append("有月份缺少记录，图表保留断点；缺失值未替换为零。")
        if spec["kind"] == "decline":
            last = "、".join(months[-3:])
            declining = [] if len(months) < 3 else [item for item in series if
                        all(value is not None for value in item["values"][-3:]) and item["values"][-3] > item["values"][-2] > item["values"][-1]]
            if len(months) < 3:
                answer = "判断连续两个月下降至少需要三个相邻月份，请扩大时间范围。"
            elif declining:
                detail = "；".join(item["name"] + "连续两个月下降（" + " → ".join(money(value) for value in item["values"][-3:]) + " 元）" for item in declining)
                answer = f"在最后三个相邻月份 {last}中，{detail}。"
            else:
                answer = f"在 {last}中，没有数据完整且连续两个月下降的区域。"
            notes.append("连续两个月下降 = 三个相邻月份的金额依次下降；不跨过缺失月份比较。")
        else:
            answer = f"已按月统计{spec['region'] or '各区域'}的{metric}。图表和下方结果表来自同一次查询。"
    return {"id": new_id(), "question": question, "answer": answer, "mode": mode, "status": "completed",
            "evidence": evidence, "table": table, "chart": chart, "notes": notes, "createdAt": now()}
