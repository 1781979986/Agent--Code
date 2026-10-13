"""读取上一节的订单样本，校验本节需要的输入、参考事实与工具资料。"""

import json
import math
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "02-evaluation-dataset" / "data"
# 对齐 JavaScript trim：包括中文空格和 BOM，不把字符串里的空白一并删除。
TEXT_WHITESPACE = "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff\u000a\u000d\u2028\u2029"


def text(value, path):
    if not isinstance(value, str) or not value.strip(TEXT_WHITESPACE):
        raise ValueError(f"{path}：须为非空文字。")
    return value.strip(TEXT_WHITESPACE)


def strict_object(value, fields, path):
    """对应严格对象 Schema：模型不能借未知字段传入用户身份等参数。"""
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{path}：字段须为 {', '.join(fields)}，不能缺失或增加。")


def array(value, path, *, length=None, minimum=0):
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{path}：须为数组，至少包含 {minimum} 项。")
    if length is not None and len(value) != length:
        raise ValueError(f"{path}：须包含 {length} 项。")
    return value


def nonnegative_number(value, path):
    # Python 的 bool 属于 int；true/false 不能被当作金额 1/0。
    if type(value) not in (int, float):
        raise ValueError(f"{path}：须为非负有限数字。")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0:
        raise ValueError(f"{path}：须为非负有限数字。")
    return value


def parse_json(value):
    # Python 默认接受 NaN/Infinity；JSON.parse 不接受，必须显式拒绝。
    def invalid_constant(constant):
        raise ValueError(f"无效 JSON 常量：{constant}")

    return json.loads(value, parse_constant=invalid_constant)


def validate_order(order):
    strict_object(order, ("tenantId", "userId", "orderId", "product", "paidAmountYuan"), "order")
    result = {key: text(order[key], "order." + key)
              for key in ("tenantId", "userId", "orderId", "product")}
    result["paidAmountYuan"] = nonnegative_number(order["paidAmountYuan"], "order.paidAmountYuan")
    return result


def validate_sample(sample):
    """本节只评 order-amount 的一轮查询，不擅自执行整套数据集。"""
    strict_object(sample, ("id", "name", "fixtureId", "inputs", "referenceOutputs"), "sample")
    if sample["id"] != "order-amount":
        raise ValueError("本节只接受 order-amount 样本。")
    inputs = sample["inputs"]
    strict_object(inputs, ("context", "turns"), "inputs")
    strict_object(inputs["context"], ("tenantId", "userId"), "inputs.context")
    context = {key: text(inputs["context"][key], "inputs.context." + key)
               for key in ("tenantId", "userId")}
    turns = [text(turn, "inputs.turns") for turn in array(inputs["turns"], "inputs.turns", length=1)]
    reference = sample["referenceOutputs"]
    strict_object(reference, ("turns",), "referenceOutputs")
    turn = array(reference["turns"], "referenceOutputs.turns", length=1)[0]
    strict_object(turn, ("behavior", "facts", "criteria"), "referenceOutputs.turns[0]")
    if turn["behavior"] != "answer":
        raise ValueError("本节参考行为须为 answer。")
    strict_object(turn["facts"], ("orderId", "paidAmountYuan"), "facts")
    facts = {"orderId": text(turn["facts"]["orderId"], "facts.orderId"),
             "paidAmountYuan": nonnegative_number(turn["facts"]["paidAmountYuan"], "facts.paidAmountYuan")}
    criteria = [text(item, "criteria") for item in array(turn["criteria"], "criteria", minimum=1)]
    return {"id": "order-amount", "name": text(sample["name"], "name"),
            "fixtureId": text(sample["fixtureId"], "fixtureId"),
            "inputs": {"context": context, "turns": turns},
            "referenceOutputs": {"turns": [{"behavior": "answer", "facts": facts, "criteria": criteria}]}}


def load_case(*, dataset=None, fixtures=None):
    """只复用 Python 02 的 JSON，不依赖上一节的 Service 或运行环境。"""
    if dataset is None:
        dataset = parse_json((DATA_DIR / "dataset.json").read_text(encoding="utf-8"))
    if fixtures is None:
        fixtures = parse_json((DATA_DIR / "fixtures.json").read_text(encoding="utf-8"))
    if not isinstance(dataset, dict):
        raise ValueError("dataset：须为对象。")
    version = text(dataset.get("version"), "version")
    cases = []
    for item in array(dataset.get("cases"), "cases"):
        if not isinstance(item, dict):
            raise ValueError("cases：每条样本须为对象。")
        cases.append({**item, "id": text(item.get("id"), "case.id")})
    matches = [item for item in cases if item["id"] == "order-amount"]
    if len(matches) != 1:
        raise ValueError("02 数据集中应有且只有一条 order-amount 样本。")
    sample = validate_sample(matches[0])

    # 与原例一致：保留 id、orders；本节没有用到的 policy 不进入工具环境。
    parsed_fixtures = []
    for item in array(fixtures, "fixtures"):
        if not isinstance(item, dict):
            raise ValueError("fixtures：每份资料须为对象。")
        parsed_fixtures.append({"id": text(item.get("id"), "fixture.id"),
                                "orders": [validate_order(order) for order in
                                           array(item.get("orders"), "fixture.orders")]})
    selected = [item for item in parsed_fixtures if item["id"] == sample["fixtureId"]]
    if len(selected) != 1:
        raise ValueError("找不到唯一的测试资料，请检查 fixtureId。")
    fixture = selected[0]
    facts = sample["referenceOutputs"]["turns"][0]["facts"]
    context = sample["inputs"]["context"]
    orders = [order for order in fixture["orders"] if order["orderId"] == facts["orderId"]
              and order["tenantId"] == context["tenantId"] and order["userId"] == context["userId"]]
    if len(orders) != 1 or orders[0]["paidAmountYuan"] != facts["paidAmountYuan"]:
        raise ValueError("参考金额与当前用户的订单资料不一致，请先核对测试样本。")
    return {"datasetVersion": version, "sample": sample, "fixture": fixture}
