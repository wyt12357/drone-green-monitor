"""
pipeline.py — 取帧 → 分析 → 渲染 → 编码 的流水线
=================================================
单一后台线程以约 25fps 处理最新帧，网页各端只读取"最新成品帧"，
因此带宽/分析开销与访问者数量无关，也不会拖慢无人机指令。
"""

from __future__ import annotations

import os
import threading
import time

import cv2
import numpy as np

from analyzer import TARGETS, ColorAnalyzer, to_jpeg, write_image


class Pipeline:
    def __init__(self, link, capture_dir: str):
        self.link = link
        self.analyzer = ColorAnalyzer()
        self.capture_dir = capture_dir
        os.makedirs(capture_dir, exist_ok=True)

        self._enabled = True
        self._show_mask = False
        self._target = "vegetation"

        self._jpeg = {"main": b"", "mask": b""}
        self._jpeg_id = 0
        self._cond = threading.Condition()

        self._analysis: dict = {"enabled": True, "coverage": None, "target": self._target}
        self._fps = 0.0
        self._last_analysis_ts = 0.0
        self._mask_ts = 0.0        # 最近一次有人请求掩膜流的时间
        self._raw_frame = None
        self._stop = threading.Event()

        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ #
    # 控制
    # ------------------------------------------------------------------ #
    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def set_show_mask(self, value: bool) -> None:
        self._show_mask = bool(value)

    def set_target(self, value: str) -> None:
        if value in TARGETS and value != self._target:
            self._target = value
            self.analyzer.reset_smoothing()

    def touch_mask(self) -> None:
        """有人看掩膜流时才编码掩膜，省掉无用的 JPEG 开销"""
        self._mask_ts = time.time()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        last_id = -1
        prev_t = time.time()
        while not self._stop.is_set():
            frame, fid = self.link.wait_frame(last_id, timeout=0.6)
            if frame is None or fid == last_id:
                continue
            last_id = fid

            now = time.time()
            dt = now - prev_t
            prev_t = now
            if dt > 0:
                inst = 1.0 / dt
                self._fps = inst if self._fps == 0 else self._fps * 0.88 + inst * 0.12

            try:
                self._process(frame, now)
            except Exception as exc:                     # 单帧异常不影响整条流水线
                print("[pipeline] frame error:", exc)

    def _process(self, frame: np.ndarray, now: float) -> None:
        result = None
        labels = None

        if self._enabled:
            result, labels = self.analyzer.analyze(frame, self._target)
            result["enabled"] = True
            result["fps"] = round(self._fps, 1)
            main = self.analyzer.render_main(frame, labels, result, self._show_mask)
            self._analysis = result
            self._last_analysis_ts = now
            if now - self._mask_ts < 5:
                jpeg_mask = to_jpeg(self.analyzer.render_mask(labels, result), 72)
            else:
                jpeg_mask = self._jpeg["mask"]
        else:
            main = frame.copy()
            jpeg_mask = self._jpeg["mask"]
            if now - self._mask_ts < 5:
                gray = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
                cv2.putText(gray, "ANALYSIS OFF", (14, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (240, 200, 120), 1, cv2.LINE_AA)
                jpeg_mask = to_jpeg(gray, 72)
            self._analysis = {
                "enabled": False,
                "coverage": None,
                "target": self._target,
                "target_label": TARGETS[self._target]["label"],
                "target_hex": "#3ddc84",
                "quality": None,
                "dominant": "—",
                "dominant_hex": "#5f7482",
                "patches": 0,
                "largest_patch": 0,
                "categories": [],
                "history": list(self.analyzer.history),
                "fps": round(self._fps, 1),
            }

        self._raw_frame = main
        jpeg_main = to_jpeg(main, 78)

        with self._cond:
            self._jpeg["main"] = jpeg_main
            if jpeg_mask:
                self._jpeg["mask"] = jpeg_mask
            self._jpeg_id += 1
            self._cond.notify_all()

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def wait_jpeg(self, kind: str, last_id: int, timeout: float = 1.0):
        with self._cond:
            if self._jpeg_id <= last_id:
                self._cond.wait(timeout)
            return self._jpeg.get(kind, b""), self._jpeg_id

    def snapshot(self) -> dict:
        data = dict(self._analysis)
        # 开关/目标以当前设置为准，避免"点了没反应"的一帧延迟
        data["enabled"] = self._enabled
        data["target"] = self._target
        data["target_label"] = TARGETS[self._target]["label"]
        data["target_hex"] = "#%02x%02x%02x" % TARGETS[self._target]["rgb"]
        if not self._enabled:
            data["coverage"] = None
            data["coverage_raw"] = None
            data["quality"] = None
            data["categories"] = []
            data["patches"] = 0
            data["dominant"] = "—"
        data["show_mask"] = self._show_mask
        data["fps"] = round(self._fps, 1)
        data["stale"] = (time.time() - self._last_analysis_ts) > 3 or self._last_analysis_ts == 0
        return data

    def save_capture(self) -> str | None:
        if self._raw_frame is None:
            return None
        name = "survey_%s.jpg" % time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.capture_dir, name)
        if not write_image(path, self._raw_frame):
            return None
        return name

    def shutdown(self) -> None:
        self._stop.set()
