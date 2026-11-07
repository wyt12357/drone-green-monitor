"""
analyzer.py — 基于 OpenCV 的画面色块分析模块
================================================
把一帧 BGR 图像按 HSV 色彩空间分割成若干地表类别，
统计"特定色块"（植被 / 裸土 / 水体 / 枯黄）的像素覆盖率，
并输出着色后的类别索引图，供叠加渲染使用。
"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np

# 分析分辨率（越小越快，320 宽足够做覆盖率统计）
ANALYSIS_WIDTH = 320

# 地表类别定义：HSV 区间使用 OpenCV 的 H∈[0,179] / S,V∈[0,255]
# 顺序即优先级（先匹配者优先占用像素）
CATEGORIES = [
    {
        "key": "water",
        "label": "水体",
        "rgb": (66, 158, 208),
        "ranges": [((88, 40, 25), (128, 255, 230))],
    },
    {
        "key": "dense",
        "label": "茂密植被",
        "rgb": (52, 186, 88),
        "ranges": [((38, 50, 25), (90, 255, 255))],
    },
    {
        "key": "sparse",
        "label": "稀疏植被",
        "rgb": (142, 214, 96),
        "ranges": [((30, 60, 55), (38, 255, 255))],
    },
    {
        "key": "dry",
        "label": "枯黄草地",
        "rgb": (232, 190, 66),
        "ranges": [((17, 60, 90), (30, 255, 255))],
    },
    {
        "key": "soil",
        "label": "裸土/硬化",
        "rgb": (186, 132, 92),
        "ranges": [((3, 45, 55), (17, 190, 205))],
    },
    {
        "key": "other",
        "label": "其他",
        "rgb": (140, 148, 156),
        "ranges": [],
    },
]

CAT_INDEX = {c["key"]: i for i, c in enumerate(CATEGORIES)}
OTHER_ID = CAT_INDEX["other"]

# 可监测的目标色块
TARGETS = {
    "vegetation": {"label": "植被绿", "keys": ["dense", "sparse"], "rgb": (61, 220, 132)},
    "soil": {"label": "裸土色块", "keys": ["soil"], "rgb": (186, 132, 92)},
    "water": {"label": "水体色块", "keys": ["water"], "rgb": (66, 158, 208)},
    "dry": {"label": "枯黄色块", "keys": ["dry"], "rgb": (232, 190, 66)},
}

# 绿化质量指数的权重（作用于"地表像素"，即排除天空/建筑后的部分）
QUALITY_W = {"dense": 1.00, "sparse": 0.62, "dry": 0.16, "water": 0.12, "soil": 0.04}


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


class ColorAnalyzer:
    """有状态的色块分析器（带帧间平滑与历史序列）"""

    def __init__(self, history_len: int = 120):
        self.history = deque(maxlen=history_len)
        self._smooth = None
        # 预生成类别着色查找表（BGR）
        self._lut = np.zeros((len(CATEGORIES), 3), np.uint8)
        for i, cat in enumerate(CATEGORIES):
            self._lut[i] = (cat["rgb"][2], cat["rgb"][1], cat["rgb"][0])

    def reset_smoothing(self) -> None:
        """切换监测目标时重置指数平滑，避免新目标从旧数值缓慢过渡"""
        self._smooth = None

    # ------------------------------------------------------------------ #
    # 分析
    # ------------------------------------------------------------------ #
    def analyze(self, frame_bgr: np.ndarray, target: str = "vegetation") -> tuple[dict, np.ndarray]:
        h, w = frame_bgr.shape[:2]
        if w > ANALYSIS_WIDTH:
            scale = ANALYSIS_WIDTH / w
            small = cv2.resize(frame_bgr, (ANALYSIS_WIDTH, max(1, int(round(h * scale)))),
                               interpolation=cv2.INTER_AREA)
        else:
            small = frame_bgr

        small = cv2.GaussianBlur(small, (3, 3), 0)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hh, ww = hsv.shape[:2]

        labels = np.full((hh, ww), OTHER_ID, np.uint8)
        claimed = np.zeros((hh, ww), bool)

        for idx, cat in enumerate(CATEGORIES):
            if idx == OTHER_ID:
                continue
            mask = np.zeros((hh, ww), bool)
            for lo, hi in cat["ranges"]:
                mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0
            mask &= ~claimed
            labels[mask] = idx
            claimed |= mask

        total = float(hh * ww)
        counts = np.bincount(labels.ravel(), minlength=len(CATEGORIES)).astype(np.float64)
        ratios = {CATEGORIES[i]["key"]: counts[i] / total * 100.0 for i in range(len(CATEGORIES))}

        # ---- 目标色块覆盖率（EMA 平滑，抑制逐帧抖动）----
        tkey = target if target in TARGETS else "vegetation"
        raw_cov = sum(ratios[k] for k in TARGETS[tkey]["keys"])
        if self._smooth is None:
            self._smooth = raw_cov
        else:
            self._smooth = 0.34 * raw_cov + 0.66 * self._smooth
        coverage = round(self._smooth, 1)
        self.history.append(coverage)

        # ---- 绿化质量指数：只看地表像素，排除天空/道路等 ----
        surface = total - counts[OTHER_ID]
        if surface > 0:
            acc = 0.0
            for key, weight in QUALITY_W.items():
                acc += counts[CAT_INDEX[key]] * weight
            quality = round(acc / surface * 100.0, 1)
        else:
            quality = 0.0

        # ---- 斑块统计（连通域）----
        target_mask = np.zeros((hh, ww), np.uint8)
        for k in TARGETS[tkey]["keys"]:
            target_mask[labels == CAT_INDEX[k]] = 255
        target_mask = cv2.morphologyEx(target_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(target_mask, connectivity=8)
        areas = [int(a) for a in stats[1:, cv2.CC_STAT_AREA]] if n > 1 else []
        areas = [a for a in areas if a >= 30]
        largest = (max(areas) / total * 100.0) if areas else 0.0

        dominant = max(CATEGORIES, key=lambda c: ratios[c["key"]])

        categories = [
            {
                "key": c["key"],
                "label": c["label"],
                "ratio": round(ratios[c["key"]], 1),
                "hex": _hex(c["rgb"]),
            }
            for c in CATEGORIES
        ]

        result = {
            "coverage": coverage,
            "coverage_raw": round(raw_cov, 1),
            "target": tkey,
            "target_label": TARGETS[tkey]["label"],
            "target_hex": _hex(TARGETS[tkey]["rgb"]),
            "quality": quality,
            "dominant": dominant["label"],
            "dominant_hex": _hex(dominant["rgb"]),
            "patches": len(areas),
            "largest_patch": round(largest, 1),
            "categories": categories,
            "history": list(self.history),
        }
        return result, labels

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def render_main(self, frame: np.ndarray, labels: np.ndarray, result: dict,
                    show_mask: bool) -> np.ndarray:
        out = frame.copy()
        if show_mask:
            # 全部走 uint8 + copyTo，比 float32 混合快一个数量级
            up = cv2.resize(labels, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_NEAREST)
            color = self._lut[up]
            blended = cv2.addWeighted(out, 0.6, color, 0.4, 0)
            mask8 = cv2.inRange(up, 0, OTHER_ID - 1)
            cv2.copyTo(blended, mask8, out)

        if result:
            self._draw_footer(out, result)
        return out

    def render_mask(self, labels: np.ndarray, result: dict) -> np.ndarray:
        color = self._lut[labels]
        color = (color.astype(np.float32) * 0.92).astype(np.uint8)
        out = cv2.resize(color, (640, 480), interpolation=cv2.INTER_NEAREST)
        # 网格标尺
        for x in range(0, 640, 64):
            cv2.line(out, (x, 0), (x, 480), (40, 46, 52), 1)
        for y in range(0, 480, 64):
            cv2.line(out, (0, y), (640, y), (40, 46, 52), 1)
        if result:
            cv2.putText(out, "CLASSIFICATION MASK", (14, 26), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (235, 242, 238), 1, cv2.LINE_AA)
            cv2.putText(out, "%s %.1f%%" % (result["target"].upper(), result["coverage"]),
                        (14, 462), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 242, 238), 1, cv2.LINE_AA)
        return out

    @staticmethod
    def _draw_footer(img: np.ndarray, result: dict) -> None:
        h, w = img.shape[:2]
        bar_h = max(22, int(h * 0.058))
        overlay = img.copy()
        cv2.rectangle(overlay, (0, h - bar_h), (w, h), (12, 20, 16), -1)
        cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
        cv2.line(img, (0, h - bar_h), (w, h - bar_h), (90, 200, 130), 1)
        text = "COVERAGE %.1f%%  |  TARGET %s  |  PATCHES %d" % (
            result["coverage"], result["target"].upper(), result["patches"])
        cv2.putText(img, text, (12, h - bar_h // 2 + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (200, 245, 215), 1, cv2.LINE_AA)


def to_jpeg(img: np.ndarray, quality: int = 78) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return b""
    return buf.tobytes()


def write_image(path: str, img: np.ndarray, quality: int = 92) -> bool:
    """cv2.imwrite 不支持中文路径，统一走 imencode + 字节写入"""
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return False
    with open(path, "wb") as fh:
        fh.write(buf.tobytes())
    return True
