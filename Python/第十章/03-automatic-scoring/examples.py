"""四份人工构造的评分对照材料，不冒充真实 Agent 的四次输出。"""

from copy import deepcopy

from agent_service import execute_order_tool


def teaching_examples(sample, orders):
    # 答案是人工编写的；工具结果由 Agent 使用的同一个只读函数实际取得。
    context = sample["inputs"]["context"]
    correct = execute_order_tool("get_order", {"orderId": "A1024"}, context, orders)
    wrong = execute_order_tool("get_order", {"orderId": "A1025"}, context, orders)
    items = [
        ("wrong-order", "查错订单，但回答写了正确金额", "订单 A1024 的实付金额是 3500 元。", wrong, None),
        ("unsupported-promise", "金额正确，但承诺退款已批准", "订单 A1024 实付 3500 元，退款已批准，今天一定到账。", correct, 0),
        ("missing-amount", "查到了订单，但没有回答金额", "已查到 A1024 的订单信息。", correct, 1),
        ("correct-paraphrase", "用另一种表达正确回答", "这笔咖啡机订单实际支付了三千五百元。", correct, 2),
    ]
    return [{"id": identity, "name": name, "expectedScore": score,
             "run": {"origin": "teaching_example", "status": "completed", "answer": answer,
                     "toolCalls": [deepcopy(call)]}}
            for identity, name, answer, call, score in items]
