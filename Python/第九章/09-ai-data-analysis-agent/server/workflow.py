"""LangGraph：查询决策 → 受限查询 / 一次修正 → 根据真实证据生成报告。"""

import time
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .analysis import build_report, js_round
from .common import json_text, new_id, now
from .model import AnalysisProvider, verification_sql
from .sandbox import execute_query


class Flow(TypedDict):
    decision: dict | None
    evidence: dict | None
    error: str
    attempts: int
    report: dict | None


INITIAL_STATE = {"decision": None, "evidence": None, "error": "", "attempts": 0, "report": None}


def canonical(rows):
    # 对象字段/行顺序不应使同口径结果误判；amount 统一为数字，其他字段保持类型。
    normalized = [{key: float(value) if key == "amount" and value is not None else 0 if key == "amount" else value
                   for key, value in sorted(row.items())} for row in rows]
    return sorted(json_text(row) for row in normalized)


def create_analysis_graph(*, question, dataset, session, mode, directory, signal, emit,
                          provider=None, execute=None):
    provider = AnalysisProvider() if provider is None else provider
    execute = execute_query if execute is None else execute

    async def decide(state):
        signal.throw_if_aborted()
        emit({"stage": "decide", "message": "根据查询错误修正计划" if state["error"] else "确认本轮指标、时间和区域"})
        decision = await provider.decide(question, dataset, session, mode, signal, state["error"])
        return {"decision": decision, "attempts": state["attempts"] + 1, "error": ""}

    async def query(state):
        decision = state["decision"]
        emit({"stage": "query", "message": "在只读容器中计算，并核对分析口径"})
        started = time.perf_counter()
        try:
            rows = await execute(Path(directory) / "data.duckdb", decision["sql"], signal)
            standard = verification_sql(decision)
            if mode == "ai" and decision["sql"].strip() != standard.strip():
                verified = await execute(Path(directory) / "data.duckdb", standard, signal)
                if canonical(rows) != canonical(verified):
                    raise ValueError(f"模型 SQL 结果与已确认口径不一致。标准查询：{standard}")
            evidence = {"id": new_id(), "sql": decision["sql"], "rows": rows, "dataset": dataset,
                        "spec": decision["spec"], "createdAt": now(), "durationMs": js_round((time.perf_counter() - started) * 1000)}
            return {"evidence": evidence, "error": ""}
        except Exception as error:
            signal.throw_if_aborted()
            return {"error": str(error)}

    def prepare_report(state):
        signal.throw_if_aborted()
        emit({"stage": "report", "message": "从查询结果生成图表、说明与依据"})
        report = build_report(question, mode, state["evidence"]) if state["evidence"] else {
            "id": new_id(), "question": question, "mode": mode,
            "status": "clarify" if state["decision"]["route"] == "clarify" else "insufficient",
            "answer": f"本轮未取得有效结果：{state['error']}" if state["error"] else state["decision"]["message"],
            "evidence": None, "table": [], "chart": None, "notes": [], "createdAt": now()}
        return {"report": report}

    return (StateGraph(Flow).add_node("decide", decide).add_node("query", query)
            .add_node("prepare_report", prepare_report).add_edge(START, "decide")
            .add_conditional_edges("decide", lambda state: "query" if state["decision"]["route"] == "query" else "prepare_report",
                                   ["query", "prepare_report"])
            .add_conditional_edges("query", lambda state: "decide" if state["error"] and state["attempts"] < 2 else "prepare_report",
                                   ["decide", "prepare_report"])
            .add_edge("prepare_report", END).compile())
