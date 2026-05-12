# ESP8266 Dial v2 实施笔记

> 从测试固件到双模式正式固件的完整实施记录
> 会话 ID：9cc90569-d840-4565-b3dd-218286cb1c08

## 目标

在现有 v1（WiFi AP + UDP + Web）基础上，做一版能**自动识别接入端类型**的双模式固件：

- 接电脑 USB 口 → **有线模式**（USB Serial 低延迟）
- 接普通 USB 充电器 → **无线模式**（WiFi AP + UDP 广播）
- 插拔自动切换

---

## 核心设计决策

### 1. 硬件检测方案被否决

最早想法是用 NPN 三极管 + VBUS 电平检测 USB 通断。否决原因：

- **电脑 USB 和充电器的 5V 物理层完全相同**，硬件根本无法区分
- 设备本身由 USB 供电，没电池——"有线/无线"区分的是**数据通路**不是**供电来源**

### 2. 最终方案：软件握手

唯一可区分的特征是**数据通道**——充电器不通信，电脑背后有 PC listener 会回应：

```
ESP8266 → 串口发 >HELLO
         │
  3秒内收到 PC 的 "ACK\n"？
    ├─ 是 → WIRED
    └─ 否 → WIRELESS

运行时心跳：
  WIRED: 每 2s 发 >PING，5s 无 ACK 切 WIRELESS
  WIRELESS: 每 10s 发 >HELLO，收到 ACK 切 WIRED
  切换冷却窗口 10s，防抖
```

关键认知：**串口和 WiFi 是两条独立通道，不互斥**。WIRELESS 模式下串口依然工作（用来发探测 HELLO），只是事件走 UDP 而不是串口。

### 3. 启动安全修复

旧固件 `ENC_DT_PIN = D4`，而 D4 (GPIO2) 是 ESP8266 的 **strapping pin**，启动时必须 HIGH。编码器在特定档位会把 DT 拉到 GND，导致芯片无法启动。

**修复**：DT 从 D4 改到 D6 (GPIO12)，D4 释放给板载 LED 做状态指示。

---

## 实施过程

### 阶段 1：计划

在 `firmware/integrated_test/PLAN.md` 写双模式方案。中途经过多轮审阅和修正：

1. 初版：NPN 三极管检测 → 被否决（硬件无法区分）
2. 二版：软件握手 → 通过
3. 三版：明确"listener 常驻"是基础假设，不是可选项
4. 四版：加性能约束（阻塞 IO、无黑窗口、<30MB 内存）
5. 五版：补充"串口和 WiFi 是独立通道"的说明

### 阶段 2：代码组织

不合并成单 .ino，拆成三层：

```
firmware/esp8266_dial_v2/
├── esp8266_dial_v2.ino    主程序（模式状态机 + 握手）
├── encoder.h / .cpp        编码器 + 按键（从 encoder_test 抽出）
└── wifi_module.h / .cpp    WiFi AP + UDP + Web（从 esp8266_dial 抽出）
```

回调解耦：`encoder` 模块产生事件 → 主程序按 mode 决定走串口还是 UDP。

### 阶段 3：代码审查发现的问题

已修复：

1. **`delay(5)` 阻塞** — encoder 按键防抖用了 `delay`，会卡住 WiFi 和串口心跳。改为时间戳防抖（`btnEdgeMs_ + btnPending_`）。
2. **`Serial.flush()` 无意义** — 切 WIRELESS 前的最后一条消息，flush 反而浪费时间。删掉。
3. **`udp.begin()` 没必要** — WiFi 模块只发不收，begin 会占用端口并接收自己的广播。删掉。
4. **Python walrus 语法不兼容** — `if m := RE.match():` 捕获没用到，改成普通 if。
5. **`_setup_logging()` 导入即执行** — 有 import 副作用，移到 `main()` 内。
6. **socket.close() 跨线程不安全** — 改为 `shutdown() + close()`。

### 阶段 4：烧录联调

Ubuntu 端联调方案：

1. 用 esptool.py 直接烧 bin 文件（避免 arduino-cli upload 的 DTR/RTS 不稳）
2. 用 pyserial 脚本模拟 PC listener，手动发 ACK
3. 验证：
   - 启动 3 秒观察窗口正常
   - 无 ACK 时 3 秒切 WIRELESS ✓
   - 发 ACK 后切 WIRED，PING 每 2s 一次 ✓
   - 编码器旋转产生 `>RIGHT/LEFT pos=N` ✓

### 阶段 5：Windows 部署

1. 打包：`pyinstaller --onefile --noconsole` → 单 exe（约 10MB）
2. 日志：写 `%LOCALAPPDATA%\dial\dial.log`，用 RotatingFileHandler
3. 自启：`HKCU\...\Run` 注册表写入，免管理员
4. **坑**：bat 文件中文乱码。Windows CMD 默认 GBK，文件是 UTF-8 无 BOM 时中文会破坏命令结构。改为全英文。
5. **未完成**：托盘图标。`--noconsole` 只隐藏控制台，图标需要用 `pystray` 在代码里实现。

---

## 关键协议

### 串口协议

| 消息 | 方向 | 时机 |
|------|------|------|
| `>BOOT integrated_test` | ESP→PC | 启动 |
| `>HELLO\n` | ESP→PC | BOOT 每 500ms；WIRELESS 每 10s |
| `>PING\n` | ESP→PC | WIRED 每 2s 心跳 |
| `ACK\n` | PC→ESP | 响应 HELLO 或 PING |
| `>MODE wired\n` / `>MODE wireless\n` | ESP→PC | 切换瞬间 |
| `>RIGHT pos=N` / `>LEFT pos=N` | ESP→PC | 旋转事件 |
| `>DOWN` / `>UP` / `>PRESS #N` / `>LONG Ns` / `>HOLD Ns` | ESP→PC | 按键事件 |

### UDP 协议

WIRELESS 模式下，ESP 通过 `192.168.4.255:8888` 广播：

```json
{"action":"right","pos":5}
```

action 取值：`right / left / press / longpress`

---

## 硬件接线

```
EC11 CLK ── D5 (GPIO14)
EC11 DT  ── D6 (GPIO12)    ★ 不可用 D4
EC11 SW  ── D3 (GPIO0)
EC11 GND ── GND
板载 LED ── D4 (GPIO2)      低电平点亮
```

无额外元件（三极管、电阻全部不需要）。

---

## 最终文件清单

### 固件
```
firmware/esp8266_dial_v2/
├── esp8266_dial_v2.ino
├── encoder.h / .cpp
├── wifi_module.h / .cpp
├── PLAN.md
└── README.md
```

编译占用：Flash 26%，RAM 36%，IRAM 92%。

### PC 端
```
pc-client/
├── dial_listener.py          双线程（Serial + UDP）
├── requirements.txt
├── run_debug.bat             前台调试
├── build_windows.bat         打包 exe
├── install_autostart.bat     开机自启
├── uninstall_autostart.bat   取消自启
└── README.md
```

### 保留的历史版本
```
firmware/
├── encoder_test/     独立测试固件，配合 encoder_monitor.py
├── esp8266_dial/     v1 旧固件，保留作回滚参考
├── integrated_test/  开发中间产物（已复制到 v2）
└── esp8266_dial_v2/  正式版
```

---

## 待办事项

### 已完成
- [x] 双模式状态机 + 握手协议
- [x] DT 引脚从 D4 迁移到 D6（启动安全）
- [x] 编码器/按键防抖（无 delay 版本）
- [x] LED 反馈（旋转/按下/长按/模式切换）
- [x] PC 端双线程接收 + 日志文件 + 打包脚本
- [x] Ubuntu 端握手联调
- [x] Windows 端部署方案（bat 脚本全英文）

### 未完成
- [ ] **托盘图标**（pystray 集成，当前只是 `--noconsole` 后台进程）
- [ ] **按键映射可配置**（config.json，目前 KEY_MAP 硬编码）
- [ ] Windows 端完整联调（有线 + 无线 + 自动切换全链路）
- [ ] 外壳 + 固定方式（面包板 → PCB 或 3D 打印外壳）
- [ ] 按键映射 Web 配置页面

---

## 遇到的坑总结

| # | 坑 | 教训 |
|---|-----|-----|
| 1 | 以为可以用硬件检测 USB 接入类型 | 仔细想清楚物理层再设计方案 |
| 2 | D4 是 strapping pin | ESP8266 的 D0/D3/D4/D8 都要小心 |
| 3 | encoder 用 delay(5) 防抖 | 多任务环境下禁止 delay，用时间戳 |
| 4 | bat 文件 UTF-8 中文乱码 | Windows CMD 默认 GBK，bat 脚本写英文最稳 |
| 5 | `--noconsole` ≠ 托盘图标 | 无窗口后台还需要 pystray 才有托盘 |
| 6 | Windows 鼠标乱跳 | 禁用 sermouse 服务，一次性注册表操作 |
| 7 | pyserial 用 `timeout=0.1` | 轮询占 CPU，改为 `timeout=None` 阻塞读 |
| 8 | Python walrus 语法 | 不需要捕获时别用 `:=`，降低兼容性 |

---

## 参考

- 项目路径：`/home/zza/esp8266-dial/`
- v2 固件：`firmware/esp8266_dial_v2/`
- PC 端：`pc-client/`
- 详细设计：`firmware/esp8266_dial_v2/PLAN.md`
- 会话记录：`/home/zza/.claude/projects/-home-zza/9cc90569-d840-4565-b3dd-218286cb1c08.jsonl`
