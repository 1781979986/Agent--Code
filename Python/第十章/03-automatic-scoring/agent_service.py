"""最小 Tool Calling Agent：查询订单、回传工具结果并保存真实调用记录。"""

from copy import deepcopy
import json

from sample import parse_json, strict_object, text


TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_order",
        "description": "根据订单号查询当前登录用户的订单与实付金额。",
        "parameters": {"type": "object", "properties": {"orderId": {"type": "string"}},
                       "required": ["orderId"], "additionalProperties": False},
    },
}]
AGENT_PROMPT = (
    "你是售后查询助手。订单事实必须先通过 get_order 查询。按用户问题回答，金额说明币种。"
    "资料没有提供的信息要说明不知道。不要承诺退款已批准、已提交或到账时间。"
)


def parse_order_args(args):
    strict_object(args, ("orderId",), "args")
    return {"orderId": text(args["orderId"], "args.orderId")}


def execute_order_tool(name, args, context, orders):
    """只读工具：身份由应用控制，模型只能提供订单号，不能覆盖租户或用户。"""
    try:
        parsed = parse_order_args(args)
    except ValueError:
        parsed = None
    if name != "get_order" or parsed is None:
        return {"name": name, "args": args,
                "result": {"found": False, "error": "工具名称或参数不合法。"}}
    order = next((item for item in orders if item["orderId"] == parsed["orderId"]
                  and item["tenantId"] == context["tenantId"] and item["userId"] == context["userId"]), None)
    # 记录原始参数，但返回独立订单副本；改动工具结果不会污染固定资料。
    return {"name": name, "args": args,
            "result": {"found": True, "order": deepcopy(order)} if order is not None else {"found": False}}


class AgentService:
    def __init__(self, model):
        # 原例由 Nest 容器注入 Service；Python CLI 直接传入模型服务即可。
        self.model = model

    def run(self, inputs, orders):
        """返回最终回答、实际工具记录和执行异常；不读取评分参考。"""
        tool_calls = []
        messages = [{"role": "system", "content": AGENT_PROMPT},
                    {"role": "user", "content": inputs["turns"][0]}]
        try:
            # 本节只运行一个用户问题；模型可通过多轮 Tool Calling 完成查询。
            for _round in range(4):
                message = self.model.complete("agent", {"messages": messages, "tools": TOOLS})
                if not message.get("tool_calls"):
                    return {"origin": "live", "status": "completed",
                            "answer": message.get("content") or "", "toolCalls": tool_calls}
                # 先保存 assistant 提出的调用，再逐条追加对应的 Tool Message。
                messages.append({"role": "assistant", "content": message.get("content"),
                                 "tool_calls": message["tool_calls"]})
                for call in message["tool_calls"]:
                    if len(tool_calls) >= 6:
                        raise ValueError("工具调用次数已达到本例上限。")
                    if call["type"] != "function":
                        raise ValueError("本例只支持 Function Tool。")
                    raw_args = call["function"]["arguments"]
                    try:
                        args = parse_json(raw_args)
                    except (ValueError, TypeError):
                        # 非法 JSON 原样交给工具层校验，错误也作为工具结果回传模型。
                        args = raw_args
                    record = execute_order_tool(call["function"]["name"], args, inputs["context"], orders)
                    tool_calls.append(record)
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(record["result"], ensure_ascii=False,
                                                           separators=(",", ":"), allow_nan=False)})
            raise ValueError("模型调用轮次已达到本例上限。")
        except Exception as error:
            # 出错时保留已经执行过的调用，供评分器区分执行失败与回答不合格。
            return {"origin": "live", "status": "error", "answer": "",
                    "toolCalls": tool_calls, "error": str(error)}
