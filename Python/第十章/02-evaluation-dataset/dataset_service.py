"""评测前的数据准备：校验固定样本、分离评分参考、创建独立运行副本。"""

from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import uuid4


DATA_DIR = Path(__file__).resolve().parent / "data"
BEHAVIORS = ("answer", "clarify", "insufficient_evidence")
# 与原例 Text Schema 一致，去掉首尾空白和 BOM；事实中的字符串不做此转换。
TEXT_WHITESPACE = "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff\u000a\u000d\u2028\u2029"


def text(value, path):
    if not isinstance(value, str) or not value.strip(TEXT_WHITESPACE):
        raise ValueError(f"{path}：须为非空文字。")
    return value.strip(TEXT_WHITESPACE)


def strict_object(value, fields, path):
    """对应严格对象 Schema：缺少字段或夹带未知字段，都在运行前报错。"""
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{path}：字段须为 {', '.join(fields)}，不能缺失或增加。")


def array(value, path, *, minimum=1):
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{path}：须为数组，至少包含 {minimum} 项。")
    return value


def number(value, path, *, nonnegative=False):
    # Python bool 是 int 的子类；金额不能把 true/false 当作 1/0。
    if type(value) not in (int, float):
        raise ValueError(f"{path}：须为有限数字。")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (nonnegative and value < 0):
        raise ValueError(f"{path}：须为{'非负' if nonnegative else ''}有限数字。")
    return value


def reference_turn(value, path):
    strict_object(value, ("behavior", "facts", "criteria"), path)
    if value["behavior"] not in BEHAVIORS:
        raise ValueError(f"{path}.behavior：处理方式无效。")
    if not isinstance(value["facts"], dict):
        raise ValueError(f"{path}.facts：须为事实对象。")
    facts = {}
    for key, fact in value["facts"].items():
        name = text(key, f"{path}.facts 的字段名")
        # 事实允许文字、数字和布尔值，允许空事实；不接受嵌套对象、数组或 null。
        if not isinstance(fact, (str, bool)):
            number(fact, f"{path}.facts.{name}")
        facts[name] = fact
    criteria = [text(item, f"{path}.criteria[{index}]") for index, item in
                enumerate(array(value["criteria"], f"{path}.criteria"))]
    return {"behavior": value["behavior"], "facts": facts, "criteria": criteria}


def validate_dataset(value):
    """检查字段、重复样本 ID 和轮次对应关系；不自动判断业务参考是否正确。"""
    strict_object(value, ("version", "cases"), "dataset")
    version, cases, ids = text(value["version"], "version"), [], set()
    for index, sample in enumerate(array(value["cases"], "cases")):
        path = f"cases[{index}]"
        strict_object(sample, ("id", "name", "fixtureId", "inputs", "referenceOutputs"), path)
        identity = text(sample["id"], path + ".id")
        name = text(sample["name"], path + ".name")
        fixture_id = text(sample["fixtureId"], path + ".fixtureId")
        strict_object(sample["inputs"], ("context", "turns"), path + ".inputs")
        context = sample["inputs"]["context"]
        strict_object(context, ("tenantId", "userId"), path + ".inputs.context")
        context = {key: text(context[key], path + ".inputs.context." + key) for key in ("tenantId", "userId")}
        turns = [text(turn, f"{path}.inputs.turns[{turn_index}]") for turn_index, turn in
                 enumerate(array(sample["inputs"]["turns"], path + ".inputs.turns"))]
        reference = sample["referenceOutputs"]
        strict_object(reference, ("turns",), path + ".referenceOutputs")
        reference = {"turns": [reference_turn(turn, f"{path}.referenceOutputs.turns[{turn_index}]") for turn_index, turn in
                               enumerate(array(reference["turns"], path + ".referenceOutputs.turns"))]}
        if identity in ids:
            raise ValueError(f"{path}.id：样本 ID 重复。")
        ids.add(identity)
        if len(turns) != len(reference["turns"]):
            raise ValueError(f"{path}：每轮输入都需要对应的检查要求。")
        cases.append({"id": identity, "name": name, "fixtureId": fixture_id,
                      "inputs": {"context": context, "turns": turns}, "referenceOutputs": reference})
    return {"version": version, "cases": cases}


def validate_fixtures(value):
    """订单、规则和身份均为合成资料；空订单列表合法，可以表达没有订单的环境。"""
    fixtures = []
    for index, fixture in enumerate(array(value, "fixtures")):
        path = f"fixtures[{index}]"
        strict_object(fixture, ("id", "orders", "policy"), path)
        identity, orders = text(fixture["id"], path + ".id"), []
        for order_index, order in enumerate(array(fixture["orders"], path + ".orders", minimum=0)):
            order_path = f"{path}.orders[{order_index}]"
            strict_object(order, ("tenantId", "userId", "orderId", "product", "paidAmountYuan"), order_path)
            clean = {key: text(order[key], order_path + "." + key) for key in ("tenantId", "userId", "orderId", "product")}
            clean["paidAmountYuan"] = number(order["paidAmountYuan"], order_path + ".paidAmountYuan", nonnegative=True)
            orders.append(clean)
        policy = fixture["policy"]
        strict_object(policy, ("id", "manualReviewAboveYuan", "content"), path + ".policy")
        policy = {"id": text(policy["id"], path + ".policy.id"),
                  "manualReviewAboveYuan": number(policy["manualReviewAboveYuan"], path + ".policy.manualReviewAboveYuan", nonnegative=True),
                  "content": text(policy["content"], path + ".policy.content")}
        fixtures.append({"id": identity, "orders": orders, "policy": policy})
    return fixtures


def read_data(name):
    """按源码位置读取本节两个固定文件，不依赖 cwd，也不读取环境配置。"""
    if name not in ("dataset.json", "fixtures.json"):
        raise ValueError("只读取本节的 dataset.json 和 fixtures.json。")
    # JSON.parse 不接受 NaN/Infinity，标准库加载时也明确拒绝这些非 JSON 常量。
    def invalid_constant(value):
        raise ValueError(f"无效 JSON 常量：{value}")
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"), parse_constant=invalid_constant)


class DatasetService:
    def __init__(self, *, dataset=None, fixtures=None):
        # 可注入合成资料做离线边界测试；默认只读取本目录的固定数据。
        self._dataset = validate_dataset(read_data("dataset.json") if dataset is None else dataset)
        self._fixtures = validate_fixtures(read_data("fixtures.json") if fixtures is None else fixtures)
        ids = [fixture["id"] for fixture in self._fixtures]
        if len(set(ids)) != len(ids):
            raise ValueError("测试资料 ID 重复。")
        for sample in self._dataset["cases"]:
            if sample["fixtureId"] not in ids:
                raise ValueError(f"找不到测试资料：{sample['fixtureId']}")

    def list(self):
        """返回样本目录，运行前核对测试范围；维持原数据集的样本顺序。"""
        return [{"id": sample["id"], "name": sample["name"], "turns": len(sample["inputs"]["turns"])}
                for sample in self._dataset["cases"]]

    def prepare(self, case_id):
        """为一次运行创建输入与工具环境；评分参考不出现在返回值里。"""
        sample = self._find(case_id)
        fixture = next(item for item in self._fixtures if item["id"] == sample["fixtureId"])
        # deepcopy 对齐 structuredClone：订单、规则、上下文、消息和记忆都不能共用可变对象。
        environment = {**deepcopy(fixture), "messages": [], "memories": {}}
        return {"runId": str(uuid4()), "datasetVersion": self._dataset["version"], "caseId": sample["id"],
                "inputs": deepcopy(sample["inputs"]), "environment": environment}

    def get_reference(self, case_id):
        """评分时单独读取检查要求，修改评分副本不会污染下次评测。"""
        return deepcopy(self._find(case_id)["referenceOutputs"])

    def _find(self, case_id):
        sample = next((sample for sample in self._dataset["cases"] if sample["id"] == case_id), None)
        if sample is None:
            raise ValueError(f"找不到样本：{case_id}")
        return sample
