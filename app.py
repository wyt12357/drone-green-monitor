"""
app.py — 无人机国土绿化监测系统 · Web 服务入口
==============================================
启动： python app.py  （默认 http://127.0.0.1:5000）

REST 接口：
  GET  /                 监控台页面
  GET  /video_feed       MJPEG 实时画面
  GET  /mask_feed        MJPEG 色块分类掩膜
  GET  /api/status       无人机状态 + 分析结果（网页每 0.9s 轮询）
  POST /api/analyze      分析开关 / 掩膜开关 / 监测目标
  POST /api/control      飞行指令
  POST /api/connect      连接 Tello EDU（失败自动回落到演示）
  POST /api/disconnect   断开真机 → 演示
  POST /api/snapshot     保存当前画面
"""

from __future__ import annotations

import os
import socket
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from drone import DroneLink
from pipeline import Pipeline

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")

app = Flask(__name__, static_folder="static", template_folder="templates")
try:
    app.json.ensure_ascii = False       # Flask 2.3+
except Exception:
    app.config["JSON_AS_ASCII"] = False

link = DroneLink()
pipeline = Pipeline(link, CAPTURE_DIR)


# ---------------------------------------------------------------------- #
# 页面
# ---------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/captures/<path:name>")
def captures(name):
    return send_from_directory(CAPTURE_DIR, name)


# ---------------------------------------------------------------------- #
# 视频流
# ---------------------------------------------------------------------- #
def _mjpeg(kind: str):
    boundary = b"--frame\r\n"
    last = -1
    while True:
        data, fid = pipeline.wait_jpeg(kind, last, timeout=1.2)
        if not data:
            time.sleep(0.05)
            continue
        last = fid
        yield (boundary
               + b"Content-Type: image/jpeg\r\n"
               + b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
               + data + b"\r\n")


@app.route("/video_feed")
def video_feed():
    resp = Response(_mjpeg("main"), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/mask_feed")
def mask_feed():
    pipeline.touch_mask()
    resp = Response(_mjpeg("mask"), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------- #
# API
# ---------------------------------------------------------------------- #
@app.get("/api/status")
def api_status():
    return jsonify({
        "drone": link.status(),
        "analysis": pipeline.snapshot(),
        "server_time": time.strftime("%H:%M:%S"),
    })


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    if "enabled" in payload:
        pipeline.set_enabled(bool(payload["enabled"]))
    if "show_mask" in payload:
        pipeline.set_show_mask(bool(payload["show_mask"]))
    if "target" in payload:
        pipeline.set_target(str(payload["target"]))
    return jsonify({"ok": True, "analysis": pipeline.snapshot()})


@app.post("/api/control")
def api_control():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", ""))
    try:
        value = int(payload.get("value") or 0)
    except (TypeError, ValueError):
        value = 0
    ok, message = link.command(action, value)
    return jsonify({"ok": ok, "message": message, "drone": link.status()})


@app.post("/api/connect")
def api_connect():
    ok, message = link.connect()
    return jsonify({"ok": ok, "message": message, "drone": link.status()})


@app.post("/api/disconnect")
def api_disconnect():
    ok, message = link.disconnect()
    return jsonify({"ok": ok, "message": message, "drone": link.status()})


@app.post("/api/snapshot")
def api_snapshot():
    name = pipeline.save_capture()
    if not name:
        return jsonify({"ok": False, "message": "暂无可用画面"}), 503
    return jsonify({"ok": True, "message": "截图已保存", "url": "/captures/" + name})


# ---------------------------------------------------------------------- #
# 启动
# ---------------------------------------------------------------------- #
def _pick_port(preferred: int = 5000, tries: int = 12) -> int:
    """找一个真正可用的端口（Windows 上 SO_REUSEADDR 会误判，这里先探测连接再试绑定）"""
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:   # 已有服务在监听
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


if __name__ == "__main__":
    port = _pick_port(int(os.environ.get("PORT", 5000)))
    print("")
    print("  无人机国土绿化监测系统")
    print("  ────────────────────────────────")
    print("  访问地址 : http://127.0.0.1:%d" % port)
    print("  当前链路 : %s" % ("演示画面（点击页面右上角可连接真机）" if link.status()["mode"] == "demo" else "实机"))
    print("  退出     : Ctrl+C")
    print("")

    if os.environ.get("OPEN_BROWSER") == "1":
        def _open():
            try:
                import webbrowser
                webbrowser.open("http://127.0.0.1:%d" % port)
            except Exception:
                pass
        threading.Timer(1.5, _open).start()

    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
