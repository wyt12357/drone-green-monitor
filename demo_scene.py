"""
demo_scene.py — 程序化生成的"航拍地表"演示视频源
================================================
在没有 Tello 无人机时提供与真机同规格的画面（640×480 BGR @30fps）。
场景是一张大地图（农田/林地拼块），相机在图上"飞行"：
  · 前进/后退/左右 → 相机平移，看到的田块随之改变
  · 上升/下降     → 视场缩放，覆盖率随高度轻微变化
  · 偏航          → 画面旋转
这样即使不接真机，操控面板和覆盖率分析也是联动的、真实的。
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def _bgr(rgb):
    return (int(rgb[2]), int(rgb[1]), int(rgb[0]))


class DemoScene:
    """合成航拍地表视频源"""

    def __init__(self, width: int = 640, height: int = 480, seed: int = 20261107):
        self.W, self.H = width, height
        self.cw, self.ch = 1600, 1200
        self.rng = np.random.default_rng(seed)

        self.canvas = self._build_canvas()

        self.cx, self.cy = self._pick_start()
        self.yaw = 0.0
        self.alt = 0.0
        self.t = 0.0
        self._vig = self._make_vignette()
        # 预生成噪点帧轮换使用，避免逐帧调用随机数生成器（省 ~10ms/帧）
        self._noise_bank = [
            self.rng.normal(0, 2.4, (self.H, self.W, 3)).astype(np.float32)
            for _ in range(6)
        ]
        self._noise_i = 0

    # ------------------------------------------------------------------ #
    # 场景构建
    # ------------------------------------------------------------------ #
    def _noise(self, w: int, h: int, cells: int) -> np.ndarray:
        cy = max(2, int(round(cells * h / max(w, 1))))
        cx = max(2, cells)
        small = self.rng.random((cy, cx)).astype(np.float32)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)

    def _paint(self, canvas, poly, rgb, stripe=None, texture=0.10):
        """在画布上铺一块田，可带垄沟纹理和斑驳质感（只处理地块包围盒，保证速度）"""
        ch, cw = canvas.shape[:2]
        poly = np.asarray(poly, dtype=np.int32)
        x0 = max(0, int(poly[:, 0].min()) - 2)
        y0 = max(0, int(poly[:, 1].min()) - 2)
        x1 = min(cw - 1, int(poly[:, 0].max()) + 2)
        y1 = min(ch - 1, int(poly[:, 1].max()) + 2)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw < 4 or bh < 4:
            return

        local = poly - np.array([x0, y0], dtype=np.int32)
        mask = np.zeros((bh, bw), np.uint8)
        cv2.fillPoly(mask, [local], 255)
        if not mask.any():
            return

        layer = np.empty((bh, bw, 3), np.uint8)
        layer[:] = _bgr(rgb)

        if stripe is not None:
            ang, gap = stripe
            th = math.radians(ang)
            dx, dy = math.cos(th), math.sin(th)
            nx, ny = -dy, dx
            ccx, ccy = float(local[:, 0].mean()), float(local[:, 1].mean())
            diag = int(math.hypot(bw, bh)) + 8
            shade = _bgr([c * 0.90 for c in rgb])
            n = int(diag / max(gap, 4)) + 2
            for k in range(-n, n + 1):
                ox, oy = ccx + nx * k * gap, ccy + ny * k * gap
                cv2.line(layer,
                         (int(ox - dx * diag), int(oy - dy * diag)),
                         (int(ox + dx * diag), int(oy + dy * diag)),
                         shade, 1, cv2.LINE_AA)

        # 田块内部的低频斑驳
        grain = self._noise(bw, bh, max(6, bw // 32))
        grain = (0.90 + texture * 2.0 * (grain - 0.5))[..., None]
        layer = np.clip(layer.astype(np.float32) * grain, 0, 255).astype(np.uint8)

        region = canvas[y0:y1 + 1, x0:x1 + 1]
        region[mask > 0] = layer[mask > 0]

    def _build_canvas(self) -> np.ndarray:
        cw, ch = self.cw, self.ch
        canvas = np.empty((ch, cw, 3), np.float32)
        canvas[:] = _bgr((78, 126, 62))                     # 底色：草地
        mottle = self._noise(cw, ch, 34)
        canvas *= (0.92 + 0.16 * mottle)[..., None]
        canvas = np.clip(canvas, 0, 255).astype(np.uint8)

        # ---- 田块拼贴（4×3，带随机偏移与轻微旋转）----
        cols, rows = 6, 4
        pw, ph = cw / cols, ch / rows
        kinds = ["veg"] * 13 + ["soil"] * 5 + ["dry"] * 3 + ["water"] * 3
        self.rng.shuffle(kinds)

        palette = {
            "veg": (74, 132, 58),
            "soil": (178, 134, 96),
            "dry": (206, 182, 106),
            "water": (60, 132, 176),
        }

        for r in range(rows):
            for c in range(cols):
                kind = kinds.pop()
                gx = pw * (0.055 + self.rng.uniform(0, 0.045))
                gy = ph * (0.06 + self.rng.uniform(0, 0.05))
                x0 = c * pw + gx
                y0 = r * ph + gy
                bw = pw - gx - self.rng.uniform(pw * 0.04, pw * 0.09)
                bh = ph - gy - self.rng.uniform(ph * 0.04, ph * 0.09)

                ang = self.rng.uniform(-5.5, 5.5)
                box = ((x0 + bw / 2, y0 + bh / 2), (bw, bh), ang)
                poly = cv2.boxPoints(box).astype(np.int32)

                base = palette[kind]
                jitter = self.rng.uniform(-10, 10, 3)
                rgb = [min(255, max(0, base[i] + jitter[i])) for i in range(3)]

                stripe = None
                if kind in ("veg", "soil", "dry"):
                    stripe = (ang + self.rng.uniform(80, 100), self.rng.uniform(13, 22))

                self._paint(canvas, poly, rgb, stripe=stripe,
                            texture=0.07 if kind == "soil" else 0.10)

        # ---- 道路 ----
        road = (150, 146, 140)
        cv2.line(canvas, (0, int(ch * 0.34)), (int(cw * 0.42), int(ch * 0.31)),
                 road, 13, cv2.LINE_AA)
        cv2.line(canvas, (int(cw * 0.42), int(ch * 0.31)), (cw, int(ch * 0.38)),
                 road, 13, cv2.LINE_AA)
        cv2.line(canvas, (int(cw * 0.63), 0), (int(cw * 0.60), int(ch * 0.32)),
                 road, 9, cv2.LINE_AA)
        cv2.line(canvas, (int(cw * 0.60), int(ch * 0.38)), (int(cw * 0.66), ch),
                 road, 9, cv2.LINE_AA)

        # ---- 树丛（沿地块边界与道路生长）----
        for _ in range(220):
            x = self.rng.uniform(0, cw)
            y = self.rng.uniform(0, ch)
            rad = self.rng.uniform(3.0, 8.0)
            dark = self.rng.uniform(0.55, 0.9)
            leaf = (int(28 * dark + 20), int(92 * dark + 24), int(30 * dark + 16))
            cv2.circle(canvas, (int(x + rad * 0.35), int(y + rad * 0.4)),
                       int(rad * 1.05), (46, 52, 44), -1, cv2.LINE_AA)   # 影子
            for _ in range(self.rng.integers(3, 6)):
                ox = x + self.rng.uniform(-rad, rad)
                oy = y + self.rng.uniform(-rad, rad)
                cv2.circle(canvas, (int(ox), int(oy)),
                           int(rad * self.rng.uniform(0.6, 1.0)), leaf, -1, cv2.LINE_AA)

        # ---- 收尾：轻微模糊 + 细颗粒 ----
        canvas = cv2.GaussianBlur(canvas, (3, 3), 0)
        canvas = cv2.convertScaleAbs(canvas, alpha=1.06, beta=-9)
        canvas = canvas.astype(np.float32) + self.rng.normal(0, 4.0, canvas.shape)
        return np.clip(canvas, 0, 255).astype(np.uint8)

    def _pick_start(self):
        """挑一个植被占比约 70%、水面不抢镜、且靠近地图中心的机位作为初始取景"""
        s = 4
        small = cv2.resize(self.canvas, (self.cw // s, self.ch // s), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        veg = ((hh >= 30) & (hh <= 90) & (ss >= 55) & (vv >= 40)).astype(np.float32)
        water = ((hh >= 88) & (hh <= 128) & (ss >= 40)).astype(np.float32)
        iv, iw = cv2.integral(veg), cv2.integral(water)

        win_w, win_h = self.W // s, self.H // s
        H, W = small.shape[:2]
        area = float(win_w * win_h)
        best, best_score = (self.cw / 2.0, self.ch / 2.0), -1e9
        for y in range(0, max(1, H - win_h), 6):
            for x in range(0, max(1, W - win_w), 6):
                v = iv[y + win_h, x + win_w] - iv[y, x + win_w] - iv[y + win_h, x] + iv[y, x]
                w = iw[y + win_h, x + win_w] - iw[y, x + win_w] - iw[y + win_h, x] + iw[y, x]
                veg_r, water_r = v / area, w / area
                cx = (x + win_w / 2.0) * s
                cy = (y + win_h / 2.0) * s
                dx = (cx - self.cw / 2) / (self.cw / 2)
                dy = (cy - self.ch / 2) / (self.ch / 2)
                score = -abs(veg_r - 0.70) - 0.08 * (dx * dx + dy * dy) - 1.6 * max(0.0, water_r - 0.05)
                if score > best_score:
                    best_score, best = score, (cx, cy)
        return best

    def _make_vignette(self) -> np.ndarray:
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        nx = (xx - self.W / 2) / (self.W / 2)
        ny = (yy - self.H / 2) / (self.H / 2)
        d = np.sqrt(nx ** 2 + ny ** 2) / math.sqrt(2)
        v = 1.0 - 0.22 * (d ** 2.2)
        return v[..., None].astype(np.float32)

    # ------------------------------------------------------------------ #
    # 相机运动
    # ------------------------------------------------------------------ #
    def move(self, forward_cm: float = 0.0, right_cm: float = 0.0) -> None:
        th = math.radians(self.yaw)
        fx, fy = math.sin(th), -math.cos(th)          # 机头方向（画布 y 轴向下）
        rx, ry = math.cos(th), math.sin(th)
        k = 2.1                                       # cm → 画布像素
        self.cx += (fx * forward_cm + rx * right_cm) * k
        self.cy += (fy * forward_cm + ry * right_cm) * k

    def rotate(self, deg: float) -> None:
        self.yaw = (self.yaw + deg) % 360

    def tick(self, dt: float) -> None:
        self.t += dt

    # ------------------------------------------------------------------ #
    # 出帧
    # ------------------------------------------------------------------ #
    def get_frame(self) -> np.ndarray:
        zoom = 1.0 + self.alt / 460.0
        ww = min(self.W * zoom, self.cw)
        wh = min(self.H * zoom, self.ch)

        # 悬停时仍有极轻微漂移，让画面"活着"
        dx = 3.0 * math.sin(self.t * 0.63)
        dy = 2.2 * math.cos(self.t * 0.47)
        x = float(np.clip(self.cx + dx, ww / 2, self.cw - ww / 2))
        y = float(np.clip(self.cy + dy, wh / 2, self.ch - wh / 2))

        patch = cv2.getRectSubPix(self.canvas, (int(round(ww)), int(round(wh))), (x, y))
        frame = cv2.resize(patch, (self.W, self.H), interpolation=cv2.INTER_AREA)

        # 偏航 → 旋转画面
        if abs(self.yaw % 360) > 0.4:
            m = cv2.getRotationMatrix2D((self.W / 2, self.H / 2), -self.yaw, 1.10)
            frame = cv2.warpAffine(frame, m, (self.W, self.H), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REFLECT)

        # 光照呼吸 + 传感器噪点 + 暗角（全部用预生成数组，避免逐帧随机与多次分配）
        f = frame.astype(np.float32)
        gain = 1.0 + 0.032 * math.sin(self.t * 1.27) + 0.014 * math.sin(self.t * 3.13)
        vig = self._vig if abs(gain - 1.0) < 0.004 else self._vig * gain
        f += self._noise_bank[self._noise_i]
        self._noise_i = (self._noise_i + 1) % len(self._noise_bank)
        f *= vig
        np.clip(f, 0, 255, out=f)
        return f.astype(np.uint8)
