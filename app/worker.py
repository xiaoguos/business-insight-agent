"""Persistent PostgreSQL job worker with a renewable lease and fencing."""

import logging
import signal
import threading
from .platform import LeaseLost, uid

log = logging.getLogger(__name__)
stop = threading.Event()


def main():
    from .main import create_app

    app = create_app()
    platform = app.state.platform
    worker_id = uid()
    active = {"job": None}

    def heartbeat():
        while not stop.is_set():
            try:
                platform.heartbeat(worker_id, active["job"])
            except Exception:
                log.exception("heartbeat failed")
            stop.wait(15)

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()

    def shutdown(*args):
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        while not stop.is_set():
            job = platform.claim()
            if not job:
                stop.wait(1)
                continue
            active["job"] = job
            try:
                app.state.handle_job(job)
            except LeaseLost:
                log.warning("stale worker stopped for %s", job.id)
            except Exception as error:
                log.exception("job %s failed", job.id)
                platform.fail(
                    job,
                    type(error).__name__
                    + ": 处理失败，请管理员通过任务 ID 查询服务端日志",
                )
            finally:
                active["job"] = None
    finally:
        stop.set()
        pulse.join(timeout=5)
        platform.engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
