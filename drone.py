"""
drone.py — Tello EDU 链路管理
=============================
职责：
  1. 通过 djitellopy 连接 Tello EDU，后台线程持续抓取摄像头帧；
  2. 把阻塞的 SDK 指令放进独立工作线程执行，避免卡住网页视频流；
  3. 未连接真机时自动使用程序化演示场景（demo_scene），接口完全一致；
  4. 对外提供统一的 status() / command() / frame() 接口。

真机使用时：电脑连上 Tello-XXXXXX 的 WiFi，点击网页"连接无人机"。
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from demo_scene import DemoScene

# 允许的指令 → 中文标签
ACTIONS = {
    "takeoff": "起飞",
    "land": "降落",
    "emergency": "紧急停止",
    "up": "上升",
    "down": "下降",
    "forward": "前进",
    "back": "后退",
    "left": "左移",
    "right": "右移",
    "cw": "顺时针旋转",
    "ccw": "逆时针旋转",
    "flip": "空翻",
    "speed": "设置速度",
}

MOVE_ACTIONS = {"up", "down", "forward", "back", "left", "right", "cw", "ccw", "flip"}

ALT_MIN, ALT_MAX = 20, 320


class DroneLink:
    def __init__(self, demo: bool = True):
        self._lock = threading.RLock()

        # —— 帧缓存 ——
        self._frame = None
        self._frame_id = 0
        self._frame_cond = threading.Condition()

        # —— 业务状态 ——
        self._mode = "demo" if demo else "idle"
        self._connected = True if demo else False
        self._connecting = False
        self._flying = False
        self._alt = 0.0
        self._alt_target = 0.0
        self._yaw = 0.0
        self._pitch = 0.0
        self._roll = 0.0
        self._speed = 0.0
        self._battery = 94
        self._flight_time = 0.0
        self._temperature = 41.0
        self._wifi = 82
        self._last_error = None
        self._t = 0.0
        self._events = deque(maxlen=60)

        # —— 演示场景 ——
        self._scene = DemoScene()

        # —— 真机句柄 ——
        self._tello = None
        self._frame_reader = None

        self._cmd_q: "deque[tuple[str, int]]" = deque()
        self._cmd_event = threading.Event()
        self._stop = threading.Event()

        self._log("系统启动，演示链路已就绪")
        threading.Thread(target=self._frame_loop, daemon=True).start()
        threading.Thread(target=self._cmd_loop, daemon=True).start()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._events.appendleft({
                "t": time.strftime("%H:%M:%S"),
                "msg": message,
                "level": level,
            })

    # ------------------------------------------------------------------ #
    # 帧循环
    # ------------------------------------------------------------------ #
    def _frame_loop(self) -> None:
        last = time.time()
        while not self._stop.is_set():
            now = time.time()
            dt = min(now - last, 0.25)
            last = now
            frame = None
            try:
                if self._mode == "live" and self._tello is not None:
                    frame = self._grab_live(dt)
                else:
                    self._tick_demo(dt)
                    frame = self._scene.get_frame()
            except Exception as exc:                      # 保护线程不退出
                self._last_error = str(exc)
                self._log("视频链路异常：%s" % exc, "error")
                time.sleep(0.3)

            if frame is not None:
                with self._frame_cond:
                    self._frame = frame
                    self._frame_id += 1
                    self._frame_cond.notify_all()

            cost = time.time() - now
            time.sleep(max(0.004, 1 / 30 - cost))

    def _grab_live(self, dt: float):
        reader = self._frame_reader
        frame = None
        if reader is not None:
            raw = reader.frame
            if raw is not None:
                frame = raw.copy()

        st = self._tello.get_current_state() or {}
        if st:
            self._battery = int(st.get("bat", self._battery) or self._battery)
            tof = st.get("tof") or st.get("h") or 0
            self._alt = float(tof)
            self._alt_target = self._alt
            self._temperature = float(st.get("temph") or st.get("templ") or self._temperature)
            self._flight_time = float(st.get("time", self._flight_time) or 0)
            self._pitch = float(st.get("pitch", 0) or 0)
            self._roll = float(st.get("roll", 0) or 0)
            self._yaw = float(st.get("yaw", 0) or 0)
            vx = float(st.get("vgx", 0) or 0)
            vy = float(st.get("vgy", 0) or 0)
            vz = float(st.get("vgz", 0) or 0)
            self._speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            wifi = st.get("wifi")
            if wifi is not None:
                self._wifi = int(max(0, min(100, (float(wifi) + 90) / 60 * 100)))
        return frame

    def _tick_demo(self, dt: float) -> None:
        self._t += dt
        if self._flying:
            self._flight_time += dt
            self._battery = max(3.0, self._battery - dt * 0.012)
            self._temperature = 42 + 2.4 * math.sin(self._t * 0.35)
            self._speed = max(0.0, self._speed - dt * 55)
        else:
            self._speed = max(0.0, self._speed - dt * 90)

        goal = self._alt_target if self._flying else 0.0
        self._alt += (goal - self._alt) * min(1.0, dt * 2.2)
        if abs(goal - self._alt) < 0.6:
            self._alt = goal
        if not self._flying and self._alt < 1.0:
            self._alt = 0.0

        self._wifi = int(80 + 8 * math.sin(self._t * 0.21))
        self._pitch = 0.0
        self._roll = 0.0
        self._scene.alt = self._alt
        self._scene.tick(dt)

    def wait_frame(self, last_id: int, timeout: float = 1.0):
        """阻塞等待比 last_id 更新的帧，返回 (frame, id)"""
        with self._frame_cond:
            if self._frame_id <= last_id:
                self._frame_cond.wait(timeout)
            if self._frame is None:
                return None, last_id
            return self._frame, self._frame_id

    # ------------------------------------------------------------------ #
    # 指令执行
    # ------------------------------------------------------------------ #
    def _cmd_loop(self) -> None:
        while not self._stop.is_set():
            if not self._cmd_q:
                self._cmd_event.wait(0.2)
                self._cmd_event.clear()
                continue
            action, value = self._cmd_q.popleft()
            try:
                if self._mode == "live":
                    self._exec_live(action, value)
                else:
                    self._exec_demo(action, value)
            except Exception as exc:
                self._last_error = str(exc)
                self._log("%s 失败：%s" % (ACTIONS.get(action, action), exc), "error")

    def _exec_live(self, action: str, value: int) -> None:
        t = self._tello
        if t is None:
            raise RuntimeError("未连接无人机")

        if action == "takeoff":
            t.takeoff()
            self._flying = True
            self._log("起飞完成", "ok")
        elif action == "land":
            t.land()
            self._flying = False
            self._log("已降落", "ok")
        elif action == "emergency":
            t.emergency()
            self._flying = False
            self._log("紧急停止：电机已断电", "warn")
        elif action == "up":
            t.move_up(value)
            self._log("上升 %dcm" % value)
        elif action == "down":
            t.move_down(value)
            self._log("下降 %dcm" % value)
        elif action == "forward":
            t.move_forward(value)
            self._log("前进 %dcm" % value)
        elif action == "back":
            t.move_back(value)
            self._log("后退 %dcm" % value)
        elif action == "left":
            t.move_left(value)
            self._log("左移 %dcm" % value)
        elif action == "right":
            t.move_right(value)
            self._log("右移 %dcm" % value)
        elif action == "cw":
            t.rotate_clockwise(value)
            self._log("顺时针 %d°" % value)
        elif action == "ccw":
            t.rotate_counter_clockwise(value)
            self._log("逆时针 %d°" % value)
        elif action == "flip":
            t.flip_back() if value else t.flip_forward()
            self._log("执行空翻", "warn")
        elif action == "speed":
            t.set_speed(value)
            self._log("飞行速度设为 %dcm/s" % value)

    def _exec_demo(self, action: str, value: int) -> None:
        if action == "takeoff":
            self._flying = True
            self._alt_target = 110
            self._battery = max(3.0, self._battery - 1.5)
            self._log("起飞完成（演示链路）", "ok")
        elif action == "land":
            self._flying = False
            self._alt_target = 0
            self._log("已降落（演示链路）", "ok")
        elif action == "emergency":
            self._flying = False
            self._alt_target = 0
            self._alt = 0
            self._log("紧急停止：电机已断电", "warn")
        elif action in ("up", "down"):
            delta = value if action == "up" else -value
            self._alt_target = float(min(ALT_MAX, max(ALT_MIN, self._alt_target + delta)))
            self._speed = value * 1.4
            self._log("%s %dcm" % (ACTIONS[action], value))
        elif action in ("forward", "back", "left", "right"):
            sign = -1 if action == "back" else 1
            if action in ("forward", "back"):
                self._scene.move(forward_cm=value * sign)
            else:
                self._scene.move(right_cm=value * (-1 if action == "left" else 1))
            self._speed = value * 1.4
            self._log("%s %dcm" % (ACTIONS[action], value))
        elif action in ("cw", "ccw"):
            self._scene.rotate(value if action == "cw" else -value)
            self._yaw = (self._yaw + (value if action == "cw" else -value)) % 360
            self._log("%s %d°" % (ACTIONS[action], value))
        elif action == "flip":
            self._log("空翻（演示链路跳过）", "warn")

    # ------------------------------------------------------------------ #
    # 对外指令接口
    # ------------------------------------------------------------------ #
    def command(self, action: str, value: int = 0) -> tuple[bool, str]:
        if action not in ACTIONS:
            return False, "未知指令：%s" % action
        if action in MOVE_ACTIONS and not self._flying:
            return False, "无人机未起飞，无法执行移动指令"
        if action == "takeoff" and self._flying:
            return False, "无人机已在飞行中"
        if action == "land" and not self._flying:
            return False, "无人机未起飞"
        if action in MOVE_ACTIONS and action != "flip":
            value = int(max(10, min(200, value or 30)))
        if action == "speed":
            value = int(max(10, min(100, value or 50)))

        self._cmd_q.append((action, value))
        self._cmd_event.set()
        return True, "%s 已下发" % ACTIONS[action]

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #
    def connect(self) -> tuple[bool, str]:
        if self._connecting:
            return False, "正在连接中"
        if self._mode == "live":
            return True, "已连接"
        self._connecting = True
        self._last_error = None
        self._log("正在连接 Tello EDU（请确认已连接无人机 WiFi）…")
        threading.Thread(target=self._do_connect, daemon=True).start()
        return True, "正在连接"

    def _do_connect(self) -> None:
        try:
            from djitellopy import Tello      # 延迟导入：无无人机时不影响启动

            tello = Tello()
            tello.connect()
            battery = tello.get_battery()
            # get_frame_read() 内部会自动调用 streamon()
            reader = tello.get_frame_read()

            self._tello = tello
            self._frame_reader = reader
            self._battery = float(battery)
            self._mode = "live"
            self._connected = True
            self._flying = False
            self._log("已连接 Tello EDU，电量 %d%%" % battery, "ok")
        except Exception as exc:
            self._last_error = str(exc)
            self._mode = "demo"
            self._connected = True
            self._log("连接失败，已回落到演示画面：%s" % exc, "error")
        finally:
            self._connecting = False

    def disconnect(self) -> tuple[bool, str]:
        if self._mode == "live" and self._tello is not None:
            try:
                if self._flying:
                    self._tello.land()
                self._tello.streamoff()
                self._tello.end()
            except Exception as exc:
                self._last_error = str(exc)
        self._tello = None
        self._frame_reader = None
        self._mode = "demo"
        self._connected = True
        self._flying = False
        self._alt = 0.0
        self._alt_target = 0.0
        self._log("已断开真机，切回演示链路")
        return True, "已切换为演示画面"

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "mode_label": "实机链路" if self._mode == "live" else "演示链路",
                "connected": self._connected,
                "connecting": self._connecting,
                "flying": self._flying,
                "battery": int(round(self._battery)),
                "height": round(self._alt, 1),
                "temperature": round(self._temperature, 1),
                "speed": round(self._speed, 1),
                "flight_time": int(self._flight_time),
                "wifi": int(self._wifi),
                "attitude": {
                    "pitch": round(self._pitch, 1),
                    "roll": round(self._roll, 1),
                    "yaw": round(self._yaw, 1),
                },
                "error": self._last_error,
                "events": list(self._events)[:8],
                "frame_id": self._frame_id,
            }

    def shutdown(self) -> None:
        self._stop.set()
        self._cmd_event.set()
