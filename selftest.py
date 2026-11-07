"""
selftest.py — 环境与算法自检
============================
不启动 Web 服务、不连接无人机，直接验证：
  1. 依赖是否齐全（flask / numpy / cv2 / djitellopy）
  2. 演示场景能否出帧
  3. 色块分类与覆盖率计算是否正常
  4. 演示链路的指令与状态是否联动

用法： python selftest.py
输出： 控制台报告 + _selftest_frames/ 下的样张
"""

from __future__ import annotations

import os
import sys
import time

OK, BAD = "[OK]  ", "[FAIL]"


def line(title):
    print("\n" + "─" * 58)
    print("  " + title)
    print("─" * 58)


def main() -> int:
    failed = 0
    line("1 / 4  依赖检查")
    for mod in ("flask", "numpy", "cv2", "djitellopy"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            print(OK, "%-12s %s" % (mod, ver))
        except Exception as exc:
            failed += 1
            print(BAD, "%-12s %s" % (mod, exc))

    if failed:
        print("\n请先安装依赖：python -m pip install -r requirements.txt")
        return 1

    import cv2

    line("2 / 4  演示场景出帧")
    from demo_scene import DemoScene

    t0 = time.time()
    scene = DemoScene()
    print(OK, "场景构建耗时 %.2fs（%dx%d 底图）" % (time.time() - t0, scene.cw, scene.ch))

    t0 = time.time()
    frames = [scene.get_frame() for _ in range(30)]
    dt = time.time() - t0
    print(OK, "连续取帧 30 张：%.1f fps，尺寸 %s" % (30 / dt, frames[0].shape))

    line("3 / 4  色块分类与覆盖率")
    from analyzer import ColorAnalyzer, TARGETS, to_jpeg, write_image

    an = ColorAnalyzer()
    res, labels = an.analyze(frames[-1])

    print("  目标类别：%s" % res["target_label"])
    print("  覆盖率  ：%.1f%%   绿化质量指数：%.1f" % (res["coverage"], res["quality"]))
    print("  斑块数  ：%d   最大斑块占比：%.1f%%   主色：%s"
          % (res["patches"], res["largest_patch"], res["dominant"]))
    print("\n  各地表类别占比：")
    for c in res["categories"]:
        bar = "█" * int(round(c["ratio"] / 2))
        print("    %-10s %5.1f%%  %s" % (c["label"], c["ratio"], bar))

    total = sum(c["ratio"] for c in res["categories"])
    if 99.0 <= total <= 101.0:
        print(OK, "占比合计 %.1f%%（应为 100%%）" % total)
    else:
        failed += 1
        print(BAD, "占比合计异常：%.1f%%" % total)

    if 10 <= res["coverage"] <= 95:
        print(OK, "覆盖率在合理范围")
    else:
        failed += 1
        print(BAD, "覆盖率异常：%.1f%%" % res["coverage"])

    # 分类稳定性：三帧之间波动应很小
    vals = [an.analyze(frames[i])[0]["coverage_raw"] for i in (0, 1, 2)]
    if max(vals) - min(vals) < 3:
        print(OK, "连续帧覆盖率波动 %.2f%%（稳定）" % (max(vals) - min(vals)))
    else:
        print(BAD, "连续帧覆盖率波动过大：%.2f%%" % (max(vals) - min(vals)))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_frames")
    os.makedirs(out, exist_ok=True)
    write_image(os.path.join(out, "demo_raw.jpg"), frames[-1])
    write_image(os.path.join(out, "demo_mask_overlay.jpg"),
                an.render_main(frames[-1], labels, res, True))
    write_image(os.path.join(out, "demo_mask.jpg"), an.render_mask(labels, res))
    print(OK, "样张已保存到 %s" % out)

    line("4 / 4  演示链路指令联动")
    from drone import DroneLink

    link = DroneLink()
    time.sleep(1.2)
    s0 = link.status()
    print("  初始：模式=%s  电量=%d%%  高度=%.1f" % (s0["mode"], s0["battery"], s0["height"]))

    link.command("takeoff", 0)
    time.sleep(1.6)
    link.command("forward", 60)
    time.sleep(1.0)
    s1 = link.status()
    print("  起飞后：飞行中=%s  高度=%.1f  信号=%d%%" % (s1["flying"], s1["height"], s1["wifi"]))
    if s1["flying"] and s1["height"] > 0:
        print(OK, "起飞指令生效")
    else:
        failed += 1
        print(BAD, "起飞指令未生效")

    frame_a = link.wait_frame(0, timeout=2)[0]
    link.command("forward", 120)
    time.sleep(0.8)
    frame_b = link.wait_frame(0, timeout=2)[0]
    if frame_a is not None and frame_b is not None and frame_a.shape == frame_b.shape:
        print(OK, "飞行指令驱动画面变化（%s → %s）" % (frame_a.shape, frame_b.shape))
    else:
        failed += 1
        print(BAD, "取帧失败")

    ok, msg = link.command("land", 0)
    time.sleep(0.6)
    print("  降落：%s / %s" % ("成功" if ok else "失败", msg))
    ok, msg = link.command("up", 40)
    if not ok:
        print(OK, "未起飞时的移动指令被正确拦截：%s" % msg)
    else:
        failed += 1
        print(BAD, "安全检查失效：未起飞却能执行移动")

    link.shutdown()

    line("结果")
    if failed == 0:
        print(OK, "全部自检通过，可以启动：python app.py")
    else:
        print(BAD, "存在 %d 项异常，请检查上方输出" % failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
