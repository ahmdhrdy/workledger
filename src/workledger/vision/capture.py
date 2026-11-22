"""Killable RTSP decoder processes; bounded latest-frame buffers and reconnects."""

import multiprocessing as mp
import os
import queue
import time
from collections import deque


def _publish(output, value):
    try:
        output.put_nowait(value)
    except queue.Full:
        try:
            output.get_nowait()
        except queue.Empty:
            pass
        try:
            output.put_nowait(value)
        except queue.Full:
            pass


def capture_process(config, output, stop):
    # Native FFmpeg diagnostics can include URLs and passwords. They must never
    # reach the service console; expose only fixed, credential-free status codes.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 2)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
        os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
        import cv2

        from .onvif import rtsp_url
        from .timing import FrameCadence

        cv2.setNumThreads(1)

        backoff = 2
        while not stop.is_set():
            capture = None
            try:
                _publish(output, {"state": "connecting", "at": time.time()})
                capture = cv2.VideoCapture(
                    rtsp_url(config),
                    cv2.CAP_FFMPEG,
                    [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                        6000,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                        4000,
                        cv2.CAP_PROP_N_THREADS,
                        int(config.get("decode_threads", 2)),
                    ],
                )
                if not capture.isOpened():
                    raise RuntimeError("Camera unavailable")
                source_fps = capture.get(cv2.CAP_PROP_FPS)
                cadence = FrameCadence()
                decoded_at = deque()
                while not stop.is_set():
                    okay = capture.grab()
                    if not okay:
                        raise RuntimeError("Stream interrupted")
                    now = time.time()
                    tick = time.monotonic()

                    # Count successful RTSP reads before preview sampling.
                    decoded_at.append(tick)
                    while decoded_at and tick - decoded_at[0] >= 5:
                        decoded_at.popleft()

                    if not cadence.ready(tick, min(config.get("live_fps", 10), 15)):
                        continue

                    okay, frame = capture.retrieve()
                    if not okay:
                        raise RuntimeError("Frame conversion failed")
                    height, width = frame.shape[:2]
                    if width > 1280:
                        frame = cv2.resize(frame, (1280, round(height * 1280 / width)))

                    okay, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                    if okay:
                        _publish(
                            output,
                            {
                                "state": "online",
                                "at": now,
                                "jpeg": jpeg.tobytes(),
                                "decoded_fps": round(len(decoded_at) / 5, 1),
                                "source_width": width,
                                "source_height": height,
                                "source_fps": source_fps,
                            },
                        )
                        backoff = 2
            except Exception:
                _publish(
                    output,
                    {
                        "state": "reconnecting",
                        "at": time.time(),
                        "message": "Unable to read RTSP. Check address, credentials, RTSP service and Wi-Fi.",
                    },
                )
            finally:
                if capture is not None:
                    capture.release()
            stop.wait(backoff)
            backoff = min(30, backoff * 2)


class Decoder:
    def __init__(self, config):
        context = mp.get_context("spawn")
        self.output = context.Queue(maxsize=1)
        self.stop_event = context.Event()
        self.process = context.Process(
            target=capture_process, args=(config, self.output, self.stop_event), daemon=True
        )
        self.process.start()
        self.started = time.time()

    def poll(self):
        latest = None
        while True:
            try:
                latest = self.output.get_nowait()
            except queue.Empty:
                return latest

    def close(self):
        self.stop_event.set()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        self.output.close()
        self.output.cancel_join_thread()
