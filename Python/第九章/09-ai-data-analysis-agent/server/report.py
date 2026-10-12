"""同一份结果、图表与 SQL 导出离线 HTML；标准库生成 SVG，无 Node 后端依赖。"""

import html
import math
import re

from .common import js_string

COLORS = ["#087f72", "#4384c5", "#b4782e"]


def escape_html(value):
    return html.escape("" if value is None else js_string(value), quote=True).replace("&#x27;", "&#39;")


def chart_option(chart):
    return {"animation": False, "color": COLORS, "grid": {"left": 65, "right": 24, "top": 45, "bottom": 48},
            "tooltip": {"trigger": "axis"}, "legend": {"top": 0},
            "xAxis": {"type": "category", "data": chart["categories"], "axisLine": {"lineStyle": {"color": "#b7c5cf"}}},
            "yAxis": {"type": "value", "name": chart["unit"], "splitLine": {"lineStyle": {"color": "#edf0f3"}}},
            "series": [{"name": item["name"], "type": chart["kind"], "data": item["values"], "connectNulls": False,
                        "smooth": False, "barMaxWidth": 45, "symbolSize": 7, "lineStyle": {"width": 3}} for item in chart["series"]]}


def chart_svg(chart):
    """保留负数、零基线和缺失断点；离线图不依赖 CDN、JS 或在线渲染服务。"""
    width, height, left, right, top, bottom = 900, 330, 65, 24, 45, 48
    plot_w, plot_h = width - left - right, height - top - bottom
    all_values = [float(value) for item in chart["series"] for value in item["values"] if value is not None]
    if any(not math.isfinite(value) for value in all_values):
        raise ValueError("图表金额无效")
    low, high = min([0, *all_values]), max([0, *all_values])
    if low == high:
        high = low + 1
    if high > 0:
        high *= 1.08
    if low < 0:
        low *= 1.08
    def y(value):
        return top + (high - value) / (high - low) * plot_h
    count = max(1, len(chart["categories"]))
    band = plot_w / count
    def x(index):
        return left + band * (index + 0.5)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="330" viewBox="0 0 900 330" role="img"><title>{escape_html(chart["title"])}</title>',
           '<rect width="900" height="330" fill="white"/>', f'<text x="{left}" y="28" font-size="14">{escape_html(chart["unit"])}</text>']
    for step in range(5):
        value = low + (high - low) * step / 4
        line_y = y(value)
        svg.append(f'<line x1="{left}" x2="{width-right}" y1="{line_y:.2f}" y2="{line_y:.2f}" stroke="#edf0f3"/><text x="{left-8}" y="{line_y+4:.2f}" text-anchor="end" font-size="11">{value:,.0f}</text>')
    svg.append(f'<line x1="{left}" x2="{width-right}" y1="{y(0):.2f}" y2="{y(0):.2f}" stroke="#b7c5cf"/>')
    for index, label in enumerate(chart["categories"]):
        svg.append(f'<text x="{x(index):.2f}" y="{height-bottom+25}" text-anchor="middle" font-size="12">{escape_html(label)}</text>')
    for series_index, item in enumerate(chart["series"]):
        color = COLORS[series_index % len(COLORS)]
        svg.append(f'<text x="{170+series_index*180}" y="25" fill="{color}" font-size="13">{escape_html(item["name"])}</text>')
        if chart["kind"] == "bar":
            bar_w = min(45, band * 0.75 / max(1, len(chart["series"])))
            for index, value in enumerate(item["values"]):
                if value is None:
                    continue
                base_x = x(index) - len(chart["series"]) * bar_w / 2 + series_index * bar_w
                svg.append(f'<rect x="{base_x:.2f}" y="{min(y(value), y(0)):.2f}" width="{bar_w:.2f}" height="{abs(y(value)-y(0)):.2f}" fill="{color}"><title>{escape_html(chart["categories"][index])}: {escape_html(value)}</title></rect>')
        else:
            points = []
            def flush():
                if points:
                    svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3"/>')
                    points.clear()
            for index, value in enumerate(item["values"]):
                if value is None:
                    flush()
                    continue
                points.append(f"{x(index):.2f},{y(value):.2f}")
                svg.append(f'<circle cx="{x(index):.2f}" cy="{y(value):.2f}" r="3.5" fill="{color}"><title>{escape_html(value)}</title></circle>')
            flush()
    return "".join(svg) + "</svg>"


def table_html(rows):
    keys = list(rows[0]) if rows else []
    headers = "".join(f"<th>{escape_html(key)}</th>" for key in keys)
    body = "".join("<tr>" + "".join(f"<td>{escape_html(row.get(key))}</td>" for key in keys) + "</tr>" for row in rows)
    return f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"


def report_html(report):
    evidence = report["evidence"]
    if evidence:
        dataset, spec = evidence["dataset"], evidence["spec"]
        basis = f"<p>数据：{escape_html(dataset['name'])} · {escape_html(dataset['sheet'])} · v{dataset['version']}<br>数据集 ID：{escape_html(dataset['id'])}<br>Checksum：{escape_html(dataset['checksum'])}<br>时间：{escape_html(spec['start'])} 至 {escape_html(spec['end'])} · 区域：{escape_html(spec['region'] or '全部')} · 指标：{escape_html(spec['metric'])}</p><pre>{escape_html(evidence['sql'])}</pre>{table_html(evidence['rows'])}"
    else:
        basis = "<p>未执行有效查询。</p>"
    chart = chart_svg(report["chart"]) if report["chart"] else ""
    notes = "".join(f"<li>{escape_html(note)}</li>" for note in report["notes"])
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>销售分析报告</title><style>body{{margin:40px auto;padding:0 24px;max-width:960px;font:15px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#24333d}}h1{{font-size:26px}}h2{{font-size:18px;margin-top:32px}}.meta{{color:#6b7c88}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #dce3e8;text-align:left}}pre{{background:#f4f7f8;padding:18px;white-space:pre-wrap;overflow-wrap:anywhere}}svg{{max-width:100%;height:auto}}li{{margin:5px 0}}</style><h1>销售数据分析报告</h1><p class="meta">{escape_html(report["createdAt"])} · {"AI 分析" if report["mode"] == "ai" else "Replay 演示"}</p><h2>问题</h2><p>{escape_html(report["question"])}</p><h2>计算结果</h2><p>{escape_html(report["answer"])}</p>{chart}{table_html(report["table"])}<h2>分析依据</h2>{basis}<h2>口径与边界</h2><ul>{notes}</ul></html>'


def result_csv(rows):
    keys = list(rows[0]) if rows else []
    def cell(value):
        text = "" if value is None else js_string(value)
        if isinstance(value, str) and re.match(r"[=+\-@\t\r]", text):
            text = "'" + text
        return '"' + text.replace('"', '""') + '"'
    return "\ufeff" + "\r\n".join(",".join(cell(value) for value in row) for row in [keys] + [[row.get(key) for key in keys] for row in rows])
