"""持久化后台任务系统。

任务状态写入 SQLite：应用重启后记录不丢失；执行中被中断的任务标记为失败（可重试），
排队中的任务自动继续。长任务通过 JobContext 上报进度、写日志并响应取消。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .context import AppContext
from .util import ApiError, dumps, loads, new_id, utcnow

log = logging.getLogger("paperdesk.jobs")

STATUSES = ("pending", "running", "waiting_approval", "paused", "completed", "failed", "cancelled")
TERMINAL = {"completed", "failed", "cancelled"}


def _days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str, payload: dict, cancel_event: threading.Event):
        self.manager = manager
        self.job_id = job_id
        self.payload = payload
        self._cancel = cancel_event

    @property
    def app(self) -> AppContext:
        return self.manager.app

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def run_async(self, coro, poll: float = 0.2):
        """运行异步任务处理器的顶层协程（代替 asyncio.run）：取消事件一旦发出，立刻中断正在等待的任何网关请求，
        并抛出 JobCancelled（3 秒内结束），而不是等当前这次模型调用自己结束。"""

        async def main():
            task = asyncio.ensure_future(coro)
            while True:
                done, _pending = await asyncio.wait({task}, timeout=poll)
                if done:
                    return task.result()
                if self._cancel.is_set():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=2.0)
                    except BaseException:  # noqa: BLE001  被中断的调用抛什么都不重要
                        pass
                    raise JobCancelled()

        return asyncio.run(main())

    async def cancellable(self, awaitable, poll: float = 0.2):
        """在异步任务处理器里等待一次耗时调用（网关请求等）：取消一旦发出就立刻中断它并抛出 JobCancelled，
        而不是等它自己结束（一次模型调用可能要几十秒，加上重试更久）。"""
        self.check_cancelled()
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=poll)
                if done:
                    return task.result()
                if self._cancel.is_set():
                    task.cancel()
                    try:
                        await task
                    except BaseException:  # noqa: BLE001  被取消的调用抛什么都不重要
                        pass
                    raise JobCancelled()
        except asyncio.CancelledError:  # 外层被取消（例如事件循环关闭）：也要把内部调用一起取消
            task.cancel()
            raise

    def progress(self, value: float | None = None, message: str | None = None) -> None:
        sets, args = [], []
        if value is not None:
            sets.append("progress = ?")
            args.append(max(0.0, min(1.0, value)))
        if message is not None:
            sets.append("message = ?")
            args.append(message)
        if not sets:
            return
        with self.app.db.session() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*args, self.job_id))

    def log(self, line: str) -> None:
        with self.app.db.session() as conn:
            conn.execute(
                "INSERT INTO job_logs(job_id, ts, line) VALUES (?, ?, ?)", (self.job_id, utcnow(), line[:2000])
            )


Handler = Callable[[JobContext], "dict | None"]


class JobManager:
    def __init__(self, app: AppContext, max_workers: int = 2):
        self.app = app
        self._handlers: dict[str, Handler] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")

    # ---- 注册与提交 ----
    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    def submit(self, job_type: str, title: str, payload: dict | None = None, paper_id: str | None = None) -> str:
        if job_type not in self._handlers:
            raise ApiError("bad_request", f"未知任务类型：{job_type}")
        job_id = new_id()
        with self.app.db.session() as conn:
            conn.execute(
                "INSERT INTO jobs(id, type, title, paper_id, status, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (job_id, job_type, title, paper_id, dumps(payload or {}), utcnow()),
            )
        self._enqueue(job_id)
        return job_id

    def submit_waiting(self, job_type: str, title: str, payload: dict | None = None, paper_id: str | None = None,
                       message: str = "等待你的批准") -> str:
        """提交一个“等待批准”的任务：只入库、不占用工作线程；approve() 后才开始执行（说明书：大文件下载需先确认）。"""
        if job_type not in self._handlers:
            raise ApiError("bad_request", f"未知任务类型：{job_type}")
        job_id = new_id()
        with self.app.db.session() as conn:
            conn.execute(
                "INSERT INTO jobs(id, type, title, paper_id, status, message, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, 'waiting_approval', ?, ?, ?)",
                (job_id, job_type, title, paper_id, message, dumps(payload or {}), utcnow()),
            )
        return job_id

    def approve(self, job_id: str) -> dict:
        """批准一个等待批准的任务。批准标记写进 payload（approved=true），重试失败任务时才不会再次要求批准，
        而被拒绝（cancelled）的任务重试时仍然没有该标记，处理器应据此拒绝执行。"""
        with self.app.db.session() as conn:
            row = conn.execute("SELECT payload_json FROM jobs WHERE id=? AND status='waiting_approval'", (job_id,)).fetchone()
            if row is None:
                if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
                    raise ApiError("not_found", "任务不存在", 404)
                raise ApiError("conflict", "该任务不在等待批准状态", 409)
            payload = loads(row["payload_json"], {}) or {}
            payload["approved"] = True
            conn.execute(
                "UPDATE jobs SET status='pending', message='已批准，排队中', payload_json=? WHERE id=?",
                (dumps(payload), job_id),
            )
        self._enqueue(job_id)
        return self.get(job_id)  # type: ignore[return-value]

    def reject(self, job_id: str, reason: str = "已拒绝") -> dict:
        with self.app.db.session() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='cancelled', finished_at=?, message=? WHERE id=? AND status='waiting_approval'",
                (utcnow(), reason, job_id),
            )
            if cur.rowcount == 0:
                if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
                    raise ApiError("not_found", "任务不存在", 404)
                raise ApiError("conflict", "该任务不在等待批准状态", 409)
        return self.get(job_id)  # type: ignore[return-value]

    def _enqueue(self, job_id: str) -> None:
        with self._lock:
            self._events[job_id] = threading.Event()
        self._pool.submit(self._run, job_id)

    # ---- 执行 ----
    def _run(self, job_id: str) -> None:
        with self.app.db.session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["status"] != "pending":
                return
            conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (utcnow(), job_id))
        handler = self._handlers.get(row["type"])
        event = self._events.get(job_id) or threading.Event()
        ctx = JobContext(self, job_id, loads(row["payload_json"], {}), event)
        try:
            if handler is None:
                raise ApiError("bad_request", f"未知任务类型：{row['type']}")
            result = handler(ctx)
        except JobCancelled:
            self._finish(job_id, "cancelled", message="已取消")
        except ApiError as exc:
            ctx.log(f"失败：{exc.message}")
            self._finish(job_id, "failed", error=exc.message)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job_id)
            ctx.log(f"内部错误：{type(exc).__name__}: {exc}")
            self._finish(job_id, "failed", error=f"内部错误：{type(exc).__name__}: {exc}"[:500])
        else:
            self._finish(job_id, "completed", result=result, progress=1.0)
        finally:
            with self._lock:
                self._events.pop(job_id, None)

    def _finish(
        self, job_id: str, status: str, *, error: str | None = None, result: Any = None,
        message: str | None = None, progress: float | None = None,
    ) -> None:
        sets = ["status = ?", "finished_at = ?", "error = ?", "result_json = ?"]
        args: list[Any] = [status, utcnow(), error, dumps(result) if result is not None else None]
        if message is not None:
            sets.append("message = ?")
            args.append(message)
        if progress is not None:
            sets.append("progress = ?")
            args.append(progress)
        with self.app.db.session() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*args, job_id))

    # ---- 查询与控制 ----
    @staticmethod
    def _to_dict(row) -> dict:
        status = row["status"]
        return {
            "id": row["id"], "type": row["type"], "title": row["title"], "paper_id": row["paper_id"],
            "status": status, "progress": row["progress"], "message": row["message"], "error": row["error"],
            "result": loads(row["result_json"]), "created_at": row["created_at"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "can_cancel": status in ("pending", "running", "waiting_approval"),
            "can_retry": status in ("failed", "cancelled"),
            "can_approve": status == "waiting_approval",
        }

    def get(self, job_id: str, with_log: bool = False) -> dict | None:
        with self.app.db.session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            data = self._to_dict(row)
            if with_log:
                logs = conn.execute(
                    "SELECT ts, line FROM job_logs WHERE job_id = ? ORDER BY id DESC LIMIT 500", (job_id,)
                ).fetchall()
                data["log"] = [f"{r['ts'][11:19]} {r['line']}" for r in reversed(logs)]
            return data

    def list(self, status: str | None = None, job_type: str | None = None, limit: int = 50) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if job_type:
            where.append("type = ?")
            args.append(job_type)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        with self.app.db.session() as conn:
            return [self._to_dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]

    def cancel(self, job_id: str) -> dict:
        with self.app.db.session() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ApiError("not_found", "任务不存在", 404)
            if row["status"] == "pending":
                conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?, message='已取消' WHERE id=? AND status='pending'",
                    (utcnow(), job_id),
                )
            elif row["status"] == "running":
                with self._lock:
                    ev = self._events.get(job_id)
                if ev is not None:
                    ev.set()
                conn.execute("UPDATE jobs SET message='正在取消…' WHERE id=?", (job_id,))
            elif row["status"] == "waiting_approval":
                conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?, message='已取消' WHERE id=? AND status='waiting_approval'",
                    (utcnow(), job_id),
                )
            else:
                raise ApiError("conflict", "该任务当前状态无法取消", 409)
        return self.get(job_id)  # type: ignore[return-value]

    def delete(self, job_id: str) -> None:
        """删除一条已结束的任务记录（日志随之删除）；进行中的任务要先取消。不影响任务产出的论文或分析结果。"""
        with self.app.db.session() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ApiError("not_found", "任务不存在", 404)
            if row["status"] not in TERMINAL:
                raise ApiError("conflict", "任务还没有结束，请先取消", 409)
            conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def clear_finished(self) -> int:
        """删除所有已结束（完成、失败、已取消）的任务记录，返回删除条数。"""
        marks = ",".join("?" * len(TERMINAL))
        with self.app.db.session() as conn:
            ids = [r["id"] for r in conn.execute(f"SELECT id FROM jobs WHERE status IN ({marks})", tuple(TERMINAL)).fetchall()]
            for i in ids:
                conn.execute("DELETE FROM job_logs WHERE job_id = ?", (i,))
                conn.execute("DELETE FROM jobs WHERE id = ?", (i,))
        return len(ids)

    def retry(self, job_id: str) -> str:
        with self.app.db.session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ApiError("not_found", "任务不存在", 404)
        if row["status"] not in ("failed", "cancelled"):
            raise ApiError("conflict", "只有失败或已取消的任务可以重试", 409)
        return self.submit(row["type"], row["title"], loads(row["payload_json"], {}), row["paper_id"])

    def recover(self) -> None:
        """应用启动时调用：被中断的运行中任务标记失败，排队中的继续执行。"""
        with self.app.db.session() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', error='应用被中断，可重试', finished_at=? WHERE status='running'",
                (utcnow(),),
            )
            conn.execute(
                "UPDATE paper_analysis SET status='failed', error='应用被中断，可重试' WHERE status='running'"
            )
            conn.execute("DELETE FROM insight_cards WHERE status = 'streaming'")
            # housekeeping：未保存的临时卡片保留 7 天；隐私请求日志只保留最近 2000 条
            conn.execute("DELETE FROM insight_cards WHERE saved = 0 AND created_at < ?", (_days_ago(7),))
            conn.execute("DELETE FROM insight_chains WHERE id NOT IN (SELECT DISTINCT chain_id FROM insight_cards)")
            conn.execute("DELETE FROM ai_requests WHERE id <= (SELECT COALESCE(MAX(id), 0) - 2000 FROM ai_requests)")
            pending = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status='pending'").fetchall()]
        for job_id in pending:
            self._enqueue(job_id)

    def wait(self, job_id: str, timeout: float = 30.0) -> dict:
        """测试辅助：等待任务进入终态。"""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(job_id)
            if job and job["status"] in TERMINAL:
                return job
            time.sleep(0.05)
        raise TimeoutError(f"job {job_id} did not finish")

    def shutdown(self) -> None:
        """通知运行中的任务取消（它们会在下一个检查点退出），并丢弃尚未开始的任务。"""
        with self._lock:
            for ev in self._events.values():
                ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
