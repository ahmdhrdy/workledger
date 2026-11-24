"""Ordered bounded I/O jobs. Failed writes retry without blocking video threads."""

import queue
import threading
import time


class BackgroundIO:
    def __init__(self, capacity=2048):
        self.jobs = queue.Queue(maxsize=capacity)
        self.stop = threading.Event()
        self.error = None
        self.completed = 0
        self.thread = threading.Thread(target=self.run, name="vision-storage", daemon=True)

    def start(self):
        self.thread.start()

    def submit(self, function, *args, **kwargs):
        try:
            self.jobs.put_nowait((function, args, kwargs))
            return True
        except queue.Full:
            self.error = "Vision storage queue full; new writes not accepted"
            return False

    def status(self):
        return {
            "storage_pending": self.jobs.unfinished_tasks,
            "storage_error": self.error,
            "storage_completed": self.completed,
        }

    def run(self):
        while not self.stop.is_set() or not self.jobs.empty():
            try:
                function, args, kwargs = self.jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            while True:
                try:
                    function(*args, **kwargs)
                    self.completed += 1
                    self.error = None
                    break
                except Exception:
                    self.error = "Vision storage write failed; queued work will retry"
                    if self.stop.wait(0.5):
                        break
            self.jobs.task_done()
            if self.stop.is_set() and self.error:
                break

    def shutdown(self, timeout=5):
        deadline = time.monotonic() + timeout
        # Let the ordered worker drain before signalling a failing job to stop.
        while self.jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
