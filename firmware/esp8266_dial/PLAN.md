# ESP8266 Dial 双模式项目计划

## Context

当前项目已有两套独立固件：
- `encoder_test/`：USB Serial + LED 反馈，已充分验证
- `esp8266_dial/`：WiFi AP + UDP + Web 页面，存在 D4 启动安全 bug

目标：将二者融合为一份主固件，使设备在**有线模式**（接电脑 USB，走串口协议）和**无线模式**（接普通 USB 电源适配器，走 WiFi UDP）之间自动切换。

**关键前提**：设备始终由 USB 供电（没有电池）。"有线/无线"区分的是**数据通路**，不是供电来源。接电脑时电脑既供电又通信，接充电器时只供电。

---

## 一、模式识别机制（核心问题）

### 1.1 硬件检测不可行

电脑 USB 和充电器的 5V 供电在硬件层面完全相同，NPN 三极管、VBUS 分压、GPIO 电平这些方案**无法区分两者**。排除。

### 1.2 软件握手方案（采用）

唯一可区分的特征是**数据通道**——充电器不通信，电脑背后有 PC 程序在接收。通过双向握手判断：

```
上电 → 启动串口 → 进入"观察窗口"（3 秒）
      │
      └─ 每 500ms 输出 >HELLO\n
            │
            ├─ 3 秒内收到 PC 的 "ACK\n" → 进入【有线模式】
            └─ 超时无回应                 → 进入【无线模式】(启动 WiFi)

运行中维持心跳：
  【有线模式】
    - ESP8266 每 2 秒发 >PING\n
    - 若连续 5 秒未收到 ACK → 切换到无线模式（关串口心跳，启动 WiFi）

  【无线模式】
    - ESP8266 每 10 秒发一次 >HELLO\n 到串口
    - 若收到 ACK → 切换到有线模式（停 WiFi 活动，开串口输出）
```

### 1.3 前提与代价

**前提**：PC 端 `dial_listener.py` 常驻运行（SerialReader + UDPReader 双线程同时工作）。listener 未运行时设备根本无法被使用，这是基础假设，不是可选项。

优势：
- 零硬件改动，零额外元件
- 充电器物理上没有数据通路，不可能回应 ACK，识别 100% 可靠
- 拔线瞬间 PC 端 pyserial 抛异常，双向都能快速感知

代价：
- 模式切换延迟：充电器 → 电脑约 3 秒；电脑 → 充电器约 5 秒
- 启动 3 秒观察窗口内，编码器事件需本地缓存，确定模式后按对应通路发出

---

## 二、固件设计

### 2.1 协议扩展

握手相关：
```
ESP8266 → PC:  >HELLO\n     启动/无线模式周期性发送（探测 PC 是否在线）
ESP8266 → PC:  >PING\n      有线模式心跳（每 2 秒）
PC → ESP8266:  ACK\n        PC 确认存在（响应 HELLO 或 PING）
ESP8266 → PC:  >MODE wired\n       模式切换为有线（通知 PC）
ESP8266 → PC:  >MODE wireless\n    模式切换为无线（切换后不再有串口输出）
```

事件相关（复用 encoder_test 已验证格式）：
```
>RIGHT pos=N
>LEFT  pos=N
>DOWN
>UP
>PRESS #N
>LONG Ns
>HOLD Ns
>STATUS clk=N dt=N sw=N pos=N
```

无线模式下事件仍通过 UDP 发 JSON（保持与现有 `dial_listener.py` 兼容）：
```json
{"action":"right","pos":5}
```

### 2.2 模式状态机

```
┌─────────────────┐
│  BOOT (3秒观察) │
└────────┬────────┘
         │
    收到 ACK?
    ┌────┴────┐
   是         否
    │         │
    ▼         ▼
┌──────┐  ┌────────┐
│ WIRED│  │WIRELESS│
└──┬───┘  └───┬────┘
   │          │
   │ 5秒无ACK │ 收到ACK
   └─────┬────┘
         │
   （互相切换）
```

**关键：串口和 WiFi 是两条独立通道，不互斥**

只要 USB 线接着电脑，串口永远是通的，不管处于哪个模式。"WIRED / WIRELESS" 区分的是**事件往哪条路径发**，不是"关掉另一条"。

| 通道 | BOOT | WIRED | WIRELESS |
|------|------|-------|----------|
| 串口 TX（ESP → PC） | HELLO 每 500ms | PING 每 2s + 编码器事件 | HELLO 每 10s（探测 PC） |
| 串口 RX（PC → ESP） | 等 ACK | 心跳 ACK | 等 ACK（收到就切 WIRED） |
| WiFi AP | 关 | 关 | 开（ESP8266-Dial） |
| UDP 广播 | 关 | 关 | 开（编码器事件走这里） |
| HTTP Web | 关 | 关 | 开（手机控制页） |

即 **WIRELESS 模式下串口和 WiFi 同时工作**：串口用于持续探测 PC 是否接入（PC 一旦回 ACK 就切回 WIRED），WiFi 用于处理手机的 Web 请求并广播事件。

WiFi 启停策略：
- **WIRED 模式**：WiFi 关闭（`WiFi.mode(WIFI_OFF)` + `WiFi.forceSleepBegin()`），省电降热。因为已经有串口这条低延迟通道，不需要 WiFi。
- **WIRELESS 模式**：启动 AP + Web + UDP。事件通过 UDP 广播，PC 上的 listener 从 `0.0.0.0:8888` 收。
- **切换开销**：从 OFF 启动 AP 约 300ms，可接受。

### 2.3 模式切换的清理动作

切换瞬间必须重置状态，避免残留：
- 清空 `btnState.isPressed / pressTime / handled`（防止长按跨模式触发）
- 清空编码器状态机 `encState.state`（避免半步动作误判）
- 位置 `encPos` 保留（用户期望）
- 设置 10 秒冷却窗口，防止边界抖动导致反复切换

### 2.4 硬件接线

```
EC11 CLK ── D5  (GPIO14)    不变
EC11 DT  ── D6  (GPIO12)    ★主固件要从 D4 改到 D6（strapping pin 修复）
EC11 SW  ── D3  (GPIO0)     不变
EC11 GND ── GND             不变
板载 LED ── D4  (GPIO2)     已从 DT 解放，可复用做状态指示
```

无新增元件。

### 2.5 LED 反馈

保持 encoder_test 已验证设计：旋转 8ms 闪、短按 3 闪、长按 2 闪后常亮、松开熄灭。新增模式指示：
- 观察窗口中：呼吸慢闪
- 进入有线模式：快闪 2 下
- 进入无线模式：慢闪 3 下

### 2.6 代码组织

```
firmware/
├── encoder_test/              保留，独立测试用
├── integrated_test/           本计划落地点
│   ├── PLAN.md               本文档
│   ├── integrated_test.ino   主程序（模式切换 + 握手）
│   ├── encoder.h / .cpp       编码器+按键状态机（从 encoder_test 移植）
│   └── wifi_module.h / .cpp   WiFi AP + Web + UDP（从 esp8266_dial 移植）
└── esp8266_dial/              保留作为回滚参考，暂不动
```

整合稳定后，再把 `integrated_test/` 重命名替代 `esp8266_dial/`。

---

## 三、PC 端改动

### 3.1 dial_listener.py 改造

当前只监听 UDP。新增：

1. **SerialReader 线程**
   - 扫描可用串口（Windows: CH340 描述；Linux: `/dev/ttyUSB*`）
   - 尝试打开 115200，`DTR=False, RTS=False`（防止触发 ESP 复位）
   - 读到 `>HELLO` 或 `>PING` 立即回 `ACK\n`
   - 解析 `>EVENT` 行 → 触发按键动作
   - 收到 `>MODE wireless` → 预期串口即将静默，不报错

2. **UDPReader 线程**
   - 保持现有逻辑，绑定 0.0.0.0:8888
   - 收到 JSON → 触发按键动作

3. **共享动作映射**
   - 两线程用同一个 `KEY_MAP`，行为一致

### 3.2 配置文件

暂不强求，保留现有硬编码 `KEY_MAP`。"按键映射可配置"属于后续独立计划，不纳入本次双模式改动。

### 3.3 性能约束（无感常驻）

listener 必须后台常驻，资源占用要压到用户无感。硬性约束：

- **所有 I/O 阻塞读**：UDP `recvfrom()` 和串口 `read()` 均用 `timeout=None`，禁止定时轮询。内核阻塞时进程睡眠，空闲 CPU ≈ 0%
- **串口扫描**：周期 ≥ 2 秒，已成功打开串口时立即暂停扫描（避免反复查 Registry/sysfs）
- **日志输出**：写 `%LOCALAPPDATA%\dial\dial.log`（Windows）或 `~/.local/share/dial/dial.log`（Linux），用 `RotatingFileHandler`（单文件 1MB，保留 3 个）。禁止写 stdout，配合 `--noconsole` 打包
- **打包**：`pyinstaller --onefile --noconsole --icon=dial.ico`，单 exe 约 8~10 MB
- **托盘图标**：用 `pystray` 提供退出入口；不加图标时纯后台也可

预期常驻占用：

| 指标 | 目标 |
|------|------|
| CPU 空闲 | < 0.1% |
| CPU 事件触发瞬时 | < 1% |
| 内存（含托盘） | < 30 MB |
| 启动时间 | < 1 秒 |
| 用户感知 | 任务栏一个图标 |

---

## 四、涉及的文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `firmware/integrated_test/integrated_test.ino` | **新建** | 主程序 + 模式状态机 + 握手 |
| `firmware/integrated_test/encoder.h / .cpp` | **新建** | 从 encoder_test.ino 抽出的编码器/按键模块 |
| `firmware/integrated_test/wifi_module.h / .cpp` | **新建** | 从 esp8266_dial.ino 抽出的 WiFi/UDP/Web 模块 |
| `pc-client/dial_listener.py` | 修改 | 新增 SerialReader 线程 + ACK 回复 |
| `pc-client/requirements.txt` | 修改 | 新增 `pyserial` 依赖 |
| `firmware/esp8266_dial/` | **不动** | 作为回滚参考保留 |

---

## 五、验证方法

| # | 测试场景 | 预期结果 |
|---|----------|----------|
| 1 | 启动时编码器处于不同档位 × 10 次冷启动 | 10/10 均正常启动，串口有 `>HELLO` 输出 |
| 2 | 接电脑 + 运行 listener | 3 秒内串口显示 `>MODE wired`，旋转/按键事件进 PC |
| 3 | 接充电器（无电脑） | 3 秒后 `>MODE wireless`，AP `ESP8266-Dial` 可见 |
| 4 | 无线运行中 → 改插电脑 + listener 已开 | 约 3 秒内切到 wired，WiFi 关闭 |
| 5 | 有线运行中 → 改插充电器 | 约 5 秒内切到 wireless，AP 启动，手机可连 |
| 6 | 模式切换瞬间按住按键 | 切换后释放不会误触发短按/长按 |

全部通过后，才能把 `integrated_test/` 提升为正式固件。

---

## 六、风险与回滚

- **风险**：整合过程中写坏主固件，设备变砖。**对策**：保留 `esp8266_dial/` 不动，`encoder_test/` 随时可烧回排查。
- **风险**：握手协议在某些 PC 环境下不稳定（串口缓冲、QuickEdit）。**对策**：心跳超时给足余量（5 秒），必要时加"强制有线模式"的物理按键组合（长按 SW 5 秒）。
- **风险**：模式频繁切换造成 WiFi 反复启停损耗芯片。**对策**：切换后至少停留 10 秒才允许再次切换（冷却窗口）。

---

## claude 会话记录
/home/zza/.claude/projects/-home-zza/9cc90569-d840-4565-b3dd-218286cb1c08.jsonl
