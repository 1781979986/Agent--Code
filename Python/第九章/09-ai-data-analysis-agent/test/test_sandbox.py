"""Fake Docker 校验挂载、取消和清理；真实 SQL 策略另用临时 DuckDB 验证。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from runtime.query_worker import run_read_only_query
from scripts.build_sandbox import build_environment
from server.sandbox import IMAGE, container_args, execute_query, start_and_collect
from server.signal import CancelSignal, TurnCancelled


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_attached_client_output_over_limit_removes_container_before_kill(self):
        order = []
        class Child:
            returncode = None
            def __init__(self):
                self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                self.stdout.feed_data(b"x" * (256 * 1024 + 1)); self.stderr.feed_eof()
                self.ended = asyncio.Event()
            def kill(self):
                order.append("kill-client"); self.returncode = -9; self.ended.set()
            async def wait(self):
                await self.ended.wait()
        child = Child()
        async def spawn(*args, **kwargs):
            self.assertEqual(args[:3], ("docker", "start", "--attach"))
            return child
        async def remove():
            order.append("remove-container")
        with patch("server.sandbox.asyncio.create_subprocess_exec", spawn), self.assertRaisesRegex(RuntimeError, "输出过大"):
            await start_and_collect("owned-test", CancelSignal(), 1000, remove)
        self.assertEqual(order, ["remove-container", "kill-client"])

    async def test_attached_client_cancel_and_timeout_wait_for_cleanup(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                entered, ended = asyncio.Event(), asyncio.Event()
                order = []
                class Child:
                    returncode = None
                    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
                    def kill(self):
                        order.append("kill-client"); self.returncode = -9; ended.set()
                    async def wait(self):
                        await ended.wait()
                child = Child()
                async def spawn(*args, **kwargs):
                    entered.set(); return child
                async def remove():
                    order.append("remove-container")
                signal = CancelSignal()
                with patch("server.sandbox.asyncio.create_subprocess_exec", spawn):
                    task = asyncio.create_task(start_and_collect("owned-test", signal, 1000 if cancel else 10, remove))
                    await entered.wait()
                    if cancel:
                        signal.abort("用户停止")
                    with self.assertRaisesRegex(TurnCancelled if cancel else RuntimeError, "用户停止" if cancel else "超过 10 秒"):
                        await asyncio.wait_for(task, 1)
                self.assertEqual(order, ["remove-container", "kill-client"])

    async def test_container_contract_real_copy_and_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "source.duckdb"; original.write_bytes(b"authorized-data")
            calls, mounts = [], []
            async def docker(args, **kwargs):
                calls.append(args)
                if args[0] == "create":
                    directory = Path(args[args.index("--mount") + 1].split(",")[1].split("=", 1)[1])
                    mounts.append(directory)
                    self.assertEqual((directory / "data.duckdb").read_bytes(), b"authorized-data")
                    self.assertEqual(json.loads((directory / "request.json").read_text()), {"sql": "SELECT * FROM sales"})
                    self.assertEqual(set(path.name for path in directory.iterdir()), {"data.duckdb", "request.json"})
                    self.assertEqual(args[args.index("--network") + 1], "none")
                    self.assertEqual(args[args.index("--user") + 1], "1000:1000")
                    self.assertIn("--read-only", args); self.assertEqual(args[-1], IMAGE)
                return "test"
            async def start(*args):
                return json.dumps({"ok": True, "rows": [{"amount": "10.00"}], "truncated": False})
            rows = await execute_query(original, "SELECT * FROM sales", CancelSignal(), docker_impl=docker, start_impl=start, work_root=Path(root) / "work")
            self.assertEqual(rows, [{"amount": "10.00"}]); self.assertEqual(calls[-1][0], "rm")
            self.assertFalse(mounts[0].exists()); self.assertEqual(original.read_bytes(), b"authorized-data")

    async def test_error_cancel_truncated_and_cleanup_failure(self):
        for scenario in ("provider", "cancel", "truncated", "structure", "cleanup"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as root:
                database = Path(root) / "db"; database.write_bytes(b"db")
                signal, calls = CancelSignal(), []
                async def docker(args, **kwargs):
                    calls.append(args)
                    if args[0] == "rm" and scenario == "cleanup":
                        raise RuntimeError("清理失败")
                    return "created"
                async def start(*args):
                    if scenario == "cancel":
                        signal.abort("用户停止")
                    if scenario == "provider":
                        raise RuntimeError("执行失败")
                    return json.dumps({"ok": True, "rows": {} if scenario == "structure" else [], "truncated": scenario == "truncated"})
                with self.assertRaises((ValueError, RuntimeError, TurnCancelled)):
                    await execute_query(database, "SELECT * FROM sales", signal, docker_impl=docker, start_impl=start, work_root=Path(root) / "work")
                self.assertEqual(calls[-1][0], "rm")
                self.assertEqual(list((Path(root) / "work").iterdir()), [])

    async def test_precanceled_and_invalid_input_never_calls_docker(self):
        signal = CancelSignal(); signal.abort()
        with self.assertRaises(TurnCancelled):
            await execute_query("/unused", "SELECT * FROM sales", signal)
        for sql in ("", "x" * 12001):
            with self.assertRaisesRegex(ValueError, "为空或过长"):
                await execute_query("/unused", sql, CancelSignal())

    def test_sql_ast_refuses_other_tables_files_writes_cte_and_subqueries(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "data.duckdb"
            connection = duckdb.connect(str(path)); connection.execute("CREATE TABLE sales (amount DECIMAL(18,2))"); connection.execute("INSERT INTO sales VALUES (1),(2)"); connection.close()
            self.assertEqual(run_read_only_query(path, "SELECT SUM(amount) AS amount FROM sales")["rows"], [{"amount": "3.00"}])
            for sql in ("DELETE FROM sales", "SELECT * FROM products", "SELECT * FROM read_csv('/etc/passwd')",
                        "WITH x AS (SELECT * FROM sales) SELECT * FROM x", "SELECT * FROM (SELECT * FROM sales)",
                        "SELECT * FROM sales; SELECT * FROM sales", "SELECT * FROM sales UNION SELECT * FROM sales"):
                with self.subTest(sql=sql), self.assertRaises(ValueError):
                    run_read_only_query(path, sql)

    def test_build_proxy_environment_is_new_and_explicit_values_win(self):
        proxy = "HTTPEnable : 1\nHTTPProxy : 127.0.0.1\nHTTPPort : 17890\nHTTPSEnable : 1\nHTTPSProxy : 127.0.0.1\nHTTPSPort : 17890"
        env = {"PATH": "/usr/bin"}
        result = build_environment(env, "darwin", lambda: proxy)
        self.assertEqual(result["HTTP_PROXY"], "http://127.0.0.1:17890"); self.assertEqual(env, {"PATH": "/usr/bin"})
        explicit = {"http_proxy": "http://localhost:8888", "HTTPS_PROXY": "http://localhost:8889", "NO_PROXY": "localhost"}
        self.assertEqual(build_environment(explicit, "darwin", lambda: self.fail("不应读取系统代理")), explicit)
        self.assertEqual(build_environment({}, "linux", lambda: self.fail("不应读取系统代理")), {})


if __name__ == "__main__":
    unittest.main()
