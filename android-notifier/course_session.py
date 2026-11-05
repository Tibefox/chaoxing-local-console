"""One receiver, one preloaded course index and one local model per desktop session."""
from contextlib import ExitStack
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import android_events as notifications
import local_model
from course_qa import Knowledge
from app_runtime import DATA, SingleInstance, read_json, status

WINDOW = notifications.DATA / "active-window.json"
ENGINE = "android_course_qwen"


def serve(launch_epoch=None):
    from process_lifecycle import epoch
    from login_control import revision
    expected = launch_epoch or epoch()
    account_revision = revision()
    stop = DATA / "stop"
    def cancelled():
        return stop.exists() or epoch() != expected or revision() != account_revision
    with SingleInstance():
        if epoch() != expected:
            return
        stop.unlink(missing_ok=True)
        if epoch() != expected:
            stop.touch()
            return
        try:
            with ExitStack() as resources:
                inbox = notifications.Inbox(notifications.DATA / "events.sqlite3")
                server = notifications.make_server(inbox, notifications.token())
                resources.callback(server.server_close)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
                thread.start()
                resources.callback(thread.join, 3)
                resources.callback(server.shutdown)
                def heartbeat(message, ready=False):
                    status("watching", message, event_source="android_notification", engine=ENGINE,
                           model_ready=ready, notification_counts=inbox.counts())
                heartbeat("正在载入本地课件和 Qwen3-4B")
                knowledge = Knowledge()
                if not knowledge.chunks:
                    raise RuntimeError("课件索引为空")
                model = local_model.start(stop_requested=cancelled)
                if not model["already_running"]:
                    resources.callback(local_model.stop)
                from group_watch import watch
                serial = read_json(notifications.DATA / "device.json", {}).get("serial")
                if serial:
                    chat_stop = threading.Event()
                    chat = threading.Thread(target=watch, args=(serial, chat_stop, knowledge), daemon=True)
                    chat.start()
                    resources.callback(chat.join, 20)
                    resources.callback(chat_stop.set)
                next_update = 0
                while not cancelled():
                    window = read_json(WINDOW, {})
                    window_end = window.get("end") if window.get("receiver") == os.getpid() else None
                    if window_end and time.time() >= window_end:
                        break
                    if time.monotonic() >= next_update:
                        heartbeat("本地模型和课件已就绪", True)
                        next_update = time.monotonic() + 2
                    time.sleep(.2)
        except Exception as exc:
            status("needs_user", str(exc)[:500], engine=ENGINE)
            raise
        else:
            status("stopped", "课程通知和本地答题服务已结束", engine=ENGINE)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--launch-epoch')
    serve(parser.parse_args().launch_epoch)
