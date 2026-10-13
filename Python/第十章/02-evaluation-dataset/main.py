"""观察样本、评分参考与独立副本；不调用模型，不启动 HTTP 服务。"""

import json
import sys

from dataset_service import DatasetService


def show_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def main():
    # 本节只需要创建服务对象；Python 不需要 NestJS 的依赖注入容器或 Web 框架。
    service = DatasetService()
    run = service.prepare("order-amount")
    print(f"数据集：{run['datasetVersion']}")
    print(f"结构检查通过：{len(service.list())} 条样本")
    # 控制台表格换成逐行目录，保留每条样本的 ID、名称、轮次与原顺序。
    for sample in service.list():
        print(f"{sample['id']} | {sample['name']} | {sample['turns']} 轮")

    print("\n样本 order-amount：交给 Agent 的输入")
    show_json(run["inputs"])
    print("订单数据：保存在独立 environment 中，后续供查询工具读取。")
    print("\n留给评分程序的参考结果")
    # 参考结果给评分程序读取，不能提前发给答题 Agent。
    show_json(service.get_reference("order-amount"))
    print("运行参数是否包含 referenceOutputs：", str("referenceOutputs" in run).lower())

    print("\n环境隔离实验：程序直接修改第一份副本，不调用 Agent。")
    print("第一份副本的 A1024 实付金额：", run["environment"]["orders"][0]["paidAmountYuan"])
    # 故意修改订单，再放入旧消息与旧记忆，观察下一次 prepare 是否仍然干净。
    run["environment"]["orders"][0]["paidAmountYuan"] = 1
    run["environment"]["messages"].append({"role": "user", "content": "上一轮留下的测试消息"})
    run["environment"]["memories"]["note"] = "上一轮留下的测试记忆"
    print("修改第一份副本以后：", run["environment"]["orders"][0]["paidAmountYuan"])

    next_run = service.prepare("order-amount")
    print("重新准备后，A1024 实付金额：", next_run["environment"]["orders"][0]["paidAmountYuan"])
    print("重新准备后的消息数量：", len(next_run["environment"]["messages"]))
    print("重新准备后的记忆数量：", len(next_run["environment"]["memories"]))
    # 此处只验证样本读取、输入/参考分离与副本隔离，不是 Agent 的实际答题成绩。
    print("\n本次只检查样本与环境准备，没有调用模型，也没有计算 Agent 得分。")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
