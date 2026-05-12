# ESP8266 Dial v2

基于 ESP8266 NodeMCU 的类 Microsoft Surface Dial 旋钮控制器，支持**有线/无线自动切换**。

- 接电脑 USB 口 → **有线模式**（USB Serial 低延迟）
- 接普通 USB 充电器 → **无线模式**（WiFi AP + UDP 广播）
- 拔插自动识别切换，无需手动操作

## 硬件

| 零件 | 数量 | 说明 |
|------|------|------|
| ESP8266 NodeMCU | 1 | 主控 |
| EC11 旋转编码器 | 1 | 带按键 |
| 杜邦线 | 若干 | 连接用 |

### 接线

```
EC11 CLK ── D5 (GPIO14)
EC11 DT  ── D6 (GPIO12)    ★ 不可用 D4（GPIO2 是 strapping pin）
EC11 SW  ── D3 (GPIO0)
EC11 GND ── GND
板载 LED ── D4 (GPIO2)      低电平点亮
```

**无需额外元件**（三极管、电阻、电容全部不需要）。

## 工作原理

### 模式识别：软件握手

电脑 USB 和充电器的 5V 供电在物理层完全相同，硬件无法区分。唯一的可判断特征是**数据通道**——充电器不通信。

```
上电 → 串口发 >HELLO
  ↓
  3 秒内收到 PC 的 "ACK\n"？
    ├─ 是 → 有线模式（事件走串口）
    └─ 否 → 无线模式（启动 WiFi AP + UDP）

运行时心跳：
  有线：每 2s 发 >PING，5s 无 ACK 切无线
  无线：每 10s 发 >HELLO 探测，收到 ACK 切有线
  切换冷却 10s 防抖
```

### 两条通道并存

串口和 WiFi 是独立的两条通路，不互斥：

| 通道 | BOOT | 有线 | 无线 |
|------|------|------|------|
| 串口 TX | HELLO 每 500ms | PING 每 2s + 事件 | HELLO 每 10s |
| WiFi AP | 关 | 关 | 开（ESP8266-Dial） |
| UDP 广播 | 关 | 关 | 开（事件走这里） |
| HTTP Web | 关 | 关 | 开（手机控制页） |

## 仓库结构

```
esp8266-dial/
├── firmware/
│   └── esp8266_dial_v2/
│       ├── esp8266_dial_v2.ino      主程序（模式状态机 + 握手）
│       ├── encoder.h / .cpp          编码器 + 按键状态机
│       ├── wifi_module.h / .cpp      WiFi AP + UDP + Web
│       ├── PLAN.md                   详细设计
│       └── README.md
├── pc-client/
│   ├── dial_listener.py              PC 端监听（Serial + UDP 双线程）
│   ├── requirements.txt
│   ├── run_debug.bat                 Windows 前台调试
│   ├── build_windows.bat             打包无窗口 exe
│   ├── install_autostart.bat         注册开机自启
│   ├── uninstall_autostart.bat       取消自启
│   └── README.md
└── NOTES_V2.md                       实施笔记
```

## 快速开始

### 1. 烧录固件

```bash
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/esp8266_dial_v2/
arduino-cli upload  --fqbn esp8266:esp8266:nodemcuv2 --port /dev/ttyUSB0 firmware/esp8266_dial_v2/
```

### 2. Windows 端（推荐）

1. 拷贝 `pc-client/` 目录到 Windows
2. 以管理员身份执行（禁用串口鼠标服务，一次性）：
   ```cmd
   REG ADD "HKLM\SYSTEM\CurrentControlSet\Services\sermouse" /V Start /T REG_DWORD /F /D 4
   ```
   **重启电脑。**
3. 双击 `run_debug.bat` 前台调试，验证事件接收
4. 双击 `build_windows.bat` 打包为无窗口 exe
5. 双击 `install_autostart.bat` 注册开机自启

日志文件：`%LOCALAPPDATA%\dial\dial.log`

### 3. Linux 端

```bash
cd pc-client
pip install -r requirements.txt
python dial_listener.py
```

日志：`~/.local/share/dial/dial.log`

## 默认按键映射

| 旋钮动作 | 系统操作 |
|---------|---------|
| 右转 | 音量增大 |
| 左转 | 音量减小 |
| 短按 | 播放/暂停 |
| 长按 | Win+D 回到桌面 |

修改 `dial_listener.py` 的 `KEY_MAP` 可自定义。

## 协议

### 串口（有线模式）

| 消息 | 方向 | 说明 |
|------|------|------|
| `>HELLO` | ESP→PC | BOOT 期 500ms；WIRELESS 期 10s |
| `>PING` | ESP→PC | WIRED 期 2s 心跳 |
| `ACK\n` | PC→ESP | 回应 HELLO 或 PING |
| `>MODE wired` / `>MODE wireless` | ESP→PC | 切换通知 |
| `>RIGHT pos=N` / `>LEFT pos=N` | ESP→PC | 旋转事件 |
| `>DOWN` / `>UP` / `>PRESS #N` / `>LONG Ns` / `>HOLD Ns` | ESP→PC | 按键事件 |

### UDP（无线模式）

ESP 广播到 `192.168.4.255:8888`：

```json
{"action":"right","pos":5}
```

`action` 取值：`right / left / press / longpress`

## 性能

| 指标 | 数值 |
|------|------|
| 固件 Flash | 277KB / 1MB (26%) |
| 固件 RAM | 29KB / 80KB (36%) |
| 有线延迟 | <10ms |
| 无线延迟 | ~30ms（局域网 UDP） |
| 模式切换 | 充电器→电脑 ~3s；电脑→充电器 ~5s |
| PC listener CPU 空闲 | <0.1% |
| PC listener 内存 | ~18MB |

## 已知问题与待办

- [ ] 托盘图标（pystray，当前仅 `--noconsole` 后台）
- [ ] 按键映射可配置（config.json）
- [ ] 外壳和固定方案

## 相关文档

- [实施笔记](NOTES_V2.md) — 完整开发历程、设计决策、踩坑总结
- [详细设计](firmware/esp8266_dial_v2/PLAN.md) — 固件设计文档
- [PC 端说明](pc-client/README.md) — Windows 部署步骤

## License

MIT
