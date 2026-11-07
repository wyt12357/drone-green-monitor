# 无人机国土绿化监测系统

基于 **Tello EDU + Python(Flask/djitellopy/OpenCV)** 的国土绿化覆盖率实时监测与飞行控制地面站。
一个网页完成四件事：看实时航拍画面、看色块覆盖率分析、开关分析功能、直接操控无人机。

![tech](https://img.shields.io/badge/Python-3.13-3776AB) ![tech](https://img.shields.io/badge/Flask-3.x-000000) ![tech](https://img.shields.io/badge/OpenCV-4.x/5.x-5C3EE8) ![tech](https://img.shields.io/badge/Tello-EDU-00A8E8)

---

## 一、功能

| 模块 | 说明 |
|---|---|
| 实时画面 | 后端把 Tello 图传（UDP 11111）解码为 MJPEG，浏览器 `<img>` 直接播放，无需 WebRTC；断流自动重连 |
| 演示画面 | 未连接真机时自动启用程序化生成的航拍地表（农田拼块/水体/道路/树丛），**飞行指令会真实改变画面**，可离线演示完整流程 |
| 覆盖率分析 | OpenCV HSV 分类：茂密植被 / 稀疏植被 / 枯黄草地 / 裸土硬化 / 水体 / 其他，实时统计目标色块像素占比 |
| 分析开关 | 网页开关即可启停分析（关闭后后端不做逐帧计算，CPU 立刻回落），并可切换监测目标色块、叠加分类色块 |
| 飞行控制 | 起飞、降落、紧急停止、前/后/左/右/升/降、左右旋转，支持步长与速度设定、键盘快捷键 |
| 状态监测 | 电量、高度、速度、温度、飞行时长、图传信号、航向角、任务日志 |
| 取证 | 一键截图，保存带分析标注的画面到 `captures/` |

## 二、目录结构

```
.
├── app.py            # Flask 入口：路由 / MJPEG 推流 / REST API
├── drone.py          # Tello EDU 链路：连接、取帧、指令队列、状态
├── pipeline.py       # 取帧 → 分析 → 渲染 → JPEG 编码 的后台流水线
├── analyzer.py       # OpenCV HSV 色块分类与覆盖率统计
├── demo_scene.py     # 程序化航拍演示场景
├── selftest.py       # 环境与算法自检（无需无人机）
├── templates/index.html
├── static/css/style.css
├── static/js/app.js
├── captures/         # 截图输出
├── .venv/            # 已装好依赖的 Python 环境
├── requirements.txt
└── run.bat           # Windows 一键启动
```

### 性能

单帧处理开销（640×480，Ryzen 级 CPU）：场景出帧 ~11ms、色块分析 ~3ms、叠加渲染 ~8ms、JPEG 编码 ~1ms，
整条流水线稳定 **约 30fps**，网页端与飞控指令互不阻塞。

## 三、快速开始

### 方式一：双击 `run.bat`（推荐）

`run.bat` 会自动按顺序查找可用的 Python：项目内 `.venv` → 本机已配置的环境 → 系统 `python`，
检查依赖，然后启动服务并自动打开浏览器。**本项目已内置 `.venv`，通常双击即可运行。**

### 方式二：命令行

```bash
# 1. 安装依赖（已内置 .venv 可跳过）
python -m pip install -r requirements.txt

# 2. 启动
python app.py

# 3. 打开浏览器
#    http://127.0.0.1:5000
```

启动后默认进入**演示链路**，无需无人机即可看到完整界面与联动的分析数据。

### run.bat 打不开怎么办

| 现象 | 原因 | 处理 |
|---|---|---|
| 窗口一闪就没了 | Python 未找到 / 依赖缺失 | 在项目目录按住 Shift 右键 → “在此处打开 PowerShell”，执行 `\.venv\Scripts\python.exe app.py`，看具体报错 |
| 提示 `Python not found` | 项目内 `.venv` 被删除且系统无 Python | 安装 Python 3.9+（勾选 Add to PATH），再执行 `pip install -r requirements.txt` |
| 提示端口被占用 | 5000 被其他程序占用 | 程序会自动改用 5001–5011，请以窗口里打印的地址为准；也可 `set PORT=8000` 后启动 |
| 中文路径导致异常 | 旧版脚本写死了中文用户名路径 | 新版 `run.bat` 已改用 `%USERPROFILE%`，不再出现该问题 |

> 若本机安全软件拦截 `.bat`，直接把 `run.bat` 里的最后一行替换为
> `"%PY%" app.py` 手动执行，或直接用命令行方式启动。

## 四、连接 Tello EDU

1. 打开无人机电源，等待机身指示灯**黄灯慢闪**。
2. 电脑 WiFi 连接到 `TELLO-XXXXXX`（Tello EDU 默认开放网络，无密码）。
3. 网页右上角点击 **「连接无人机」**。
4. 成功后面板顶部标签变为 **实机链路**，画面切换为真机图传，状态栏读取真实电量/高度。

> 连接失败（超时/无响应）时系统不会崩溃，会记录日志并自动回落到演示画面，方便排查问题。

**注意事项**

- 真机指令是串行阻塞的（如 `takeoff` 约 8 秒），后端已放入独立工作线程执行，**不会卡住视频流**。
- 空旷室内飞行请设置 `set_speed` ≤ 60 cm/s，并保持 1.5 m 以上净空。
- 若飞行中需要立刻停桨，使用「紧急停止」——该操作会让无人机**直接断电坠落**，仅在危险时使用。
- 图传 UDP 11111 需本机可监听；Windows 首次运行请允许防火墙放行 Python。

## 五、界面说明

```
┌────────────────────────────────────────────┬──────────────────┐
│  实时画面（HUD：REC/高度/速度/帧率）        │  覆盖率分析       │
│  ├ 覆盖率分析开关  ├ 色块叠加  ├ 监测目标   │  环形仪表 + 走势  │
│  └ 截图 / 全屏                              │  组成占比 + 斑块  │
├────────────────────────────────────────────┤──────────────────┤
│  飞行操控：起飞·降落·紧急 | 方向盘 | 步长速度│  无人机状态       │
│                                            │  任务日志         │
└────────────────────────────────────────────┴──────────────────┘
```

**快捷键**：`W/A/S/D` 前后左右 · `↑/↓` 升降 · `Q/E` 转向 · `T` 起飞 · `L` 降落

## 六、分析算法

1. 每帧缩放到 320px 宽（速度/精度平衡），轻高斯去噪后转 HSV。
2. 按优先级依次匹配 HSV 区间，像素归属唯一类别（避免重叠重复计数）：

| 类别 | H | S | V |
|---|---|---|---|
| 水体 | 88–128 | 40–255 | 25–230 |
| 茂密植被 | 38–90 | 50–255 | 25–255 |
| 稀疏植被 | 30–38 | 60–255 | 55–255 |
| 枯黄草地 | 17–30 | 60–255 | 90–255 |
| 裸土/硬化 | 3–17 | 45–190 | 55–205 |

3. **覆盖率** = 目标类别像素 / 全画面像素 × 100%，经 EMA（α=0.34）平滑抑制逐帧抖动。
4. **绿化质量指数** = Σ(类别占比 × 权重) / 地表像素（排除天空/道路等"其他"），权重：茂密 1.0、稀疏 0.62、枯黄 0.16、水体 0.12、裸土 0.04。
5. **斑块统计**：对目标掩膜做形态学开运算后连通域分析，输出斑块数与最大斑块占比。

## 七、REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/video_feed` | MJPEG 主画面 |
| GET | `/mask_feed` | MJPEG 分类掩膜 |
| GET | `/api/status` | 无人机状态 + 最新分析结果（网页 0.9s 轮询） |
| POST | `/api/analyze` | `{"enabled":true,"show_mask":false,"target":"vegetation"}` |
| POST | `/api/control` | `{"action":"forward","value":40}` |
| POST | `/api/connect` / `/api/disconnect` | 连接真机 / 回落到演示 |
| POST | `/api/snapshot` | 保存当前画面到 `captures/` |

`action` 取值：`takeoff` `land` `emergency` `up` `down` `forward` `back` `left` `right` `cw` `ccw` `flip` `speed`

## 八、常见问题

**Q：`ImportError: DLL load failed while importing cv2`？**
同时装了 `opencv-python` 与 `opencv-python-headless` 会互相覆盖。执行
`pip uninstall -y opencv-python opencv-python-headless && pip install opencv-python-headless` 后重装即可（djitellopy 会自动再装 `opencv-python`，两者共存时通常以最后安装者为准，无碍使用）。

**Q：点击连接一直转圈？**
电脑未连上 `TELLO-XXXXXX` WiFi，或无人机未开机。djitellopy 默认会重试约 20 秒后抛出超时。检查无人机指示灯是否黄灯慢闪。

**Q：画面能出但状态全 0？**
Tello 状态包（UDP 8890）被防火墙拦截，放行 Python 即可。

**Q：端口 5000 被占用？**
`app.py` 会自动向后寻找可用端口（5000–5011），以控制台打印地址为准；也可 `set PORT=8000` 指定。

## 九、安全声明

本项目面向教学与科研演示。真机飞行前请确认：场地净空、电量 > 30%、GPS 无关但 WiFi 干扰可控、遵守当地无人机飞行管理规定。作者不对任何飞行事故负责。
