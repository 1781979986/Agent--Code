"""入口：真实 Agent 答题、自动评分、人工材料核对，保存本次完整报告。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from agent_service import AgentService
from evaluation_service import EvaluationService, RUBRIC_VERSION
from examples import teaching_examples
from model_service import ModelService
from sample import load_case


OUTPUT_PATH = Path(__file__).resolve().parent / "outputs" / "latest.json"


def print_result(label, run, evaluation):
    """展示各条规则、评分与理由，不把不同失败压缩成一个总分。"""
    print(f"\n{label}")
    print(f"回答：{run['answer'] or '未产生回答'}")
    for rule in evaluation["rules"]:
        print(f"  {rule['name']}：{'通过' if rule['passed'] else '未通过'}；{rule['reason']}")
    judge = evaluation["judge"]
    if judge["status"] == "completed":
        result = judge["result"]
        print(f"回答评分：{'暂不能判断' if result['score'] is None else str(result['score']) + '/2'}")
        print(f"评分理由：{result['reason']}")
        print(f"引用原文：{'；'.join(result['quotes'])}")
    else:
        print(f"模型评分：{judge['reason']}")
    print(f"最终结果：{evaluation['status']}")


def run_demo(model=None, *, output_path=OUTPUT_PATH):
    # Nest ApplicationContext 在原例仅用于依赖注入，不是 Web 服务。
    # Python 使用显式组合；注入 Fake Model 和临时输出路径可离线验证完整入口。
    model = ModelService() if model is None else model
    model.assert_configured()
    case = load_case()
    sample, fixture = case["sample"], case["fixture"]
    agent, evaluator = AgentService(model), EvaluationService(model)
    print("本次会调用答题模型和评分模型，产生 API 费用。")
    print("使用模型：", model.settings())
    print(f"用户问题：{sample['inputs']['turns'][0]}")

    # 给 Agent 独立的问题、身份和订单副本，参考答案只交给后续评分器。
    run = agent.run(deepcopy(sample["inputs"]), deepcopy(fixture["orders"]))
    evaluation = evaluator.evaluate(sample, run)
    print_result("一、真实 Agent 的执行结果", run, evaluation)
    comparisons = []
    print("\n二、下面四份是人为构造的评分对照材料，不是真实 Agent 的四次输出。")
    for example in teaching_examples(sample, fixture["orders"]):
        result = evaluator.evaluate(sample, example["run"])
        print_result(example["name"], example["run"], result)
        expected = example["expectedScore"]
        # null 的人工预期表示规则失败，不是 Judge 返回 null 的 needs_review。
        agreement = (result["status"] == "failed" and result["judge"]["status"] == "skipped"
                     if expected is None else result["judge"]["status"] == "completed"
                     and result["judge"]["result"]["score"] == expected)
        print(f"人工预期：{'规则不通过，不调用评分模型' if expected is None else str(expected) + '/2'}")
        print(f"人工核对：{'与预期一致' if agreement else '存在分歧或评分未完成，请查看理由与原文'}")
        comparisons.append({**example, "evaluation": result, "agreement": agreement})

    # 与原例相同：latest.json 是本次结果，重新运行会覆盖；不计算整套数据集成功率。
    report = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
              "datasetVersion": case["datasetVersion"], "fixtureId": fixture["id"], "caseId": sample["id"],
              "rubricVersion": RUBRIC_VERSION, "models": model.settings(),
              "actual": {"run": run, "evaluation": evaluation}, "comparisons": comparisons}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print("\n完整结果已保存到 outputs/latest.json。教学对照材料不计入 Agent 成功率。")
    # 回答不合格仍是有效评测；仅执行/评分技术异常使 CLI 以 1 退出。
    return 1 if any(item["status"] in ("evaluation_error", "agent_error")
                    for item in [evaluation, *(item["evaluation"] for item in comparisons)]) else 0


def main():
    try:
        return run_demo()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
