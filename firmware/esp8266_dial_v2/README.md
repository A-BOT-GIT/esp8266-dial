# ESP8266 Dial v2 — 双模式正式固件

## 功能

- **有线模式**：USB 串口直连电脑，低延迟
- **无线模式**：WiFi AP + UDP 广播，插普通充电器即可用
- **自动切换**：通过软件握手（HELLO/ACK）识别接入端类型
- **编码器驱动**：EC11 状态机 + 3ms 防抖（时间戳版，无 delay 阻塞）
- **LED 反馈**：旋转/按下/长按/模式切换各有明确闪烁

## 硬件接线

```
EC11 CLK ── D5 (GPIO14)
EC11 DT  ── D6 (GPIO12)    ★ 不可用 D4，GPIO2 是 strapping pin
EC11 SW  ── D3 (GPIO0)
EC11 GND ── GND
板载 LED ── D4 (GPIO2)      低电平点亮
```

## 文件

- `esp8266_dial_v2.ino` — 主程序（模式状态机 + 握手）
- `encoder.h / .cpp` — 编码器 + 按键状态机
- `wifi_module.h / .cpp` — WiFi AP + UDP + Web 控制页

## 烧录

### Arduino IDE
板型：`NodeMCU 1.0 (ESP-12E Module)`，打开 `esp8266_dial_v2.ino` → 上传

### arduino-cli
```bash
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 .
arduino-cli upload  --fqbn esp8266:esp8266:nodemcuv2 --port /dev/ttyUSB0 .
```

### esptool.py 直烧
```bash
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 --output-dir /tmp/build .
python esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
  --before default_reset --after hard_reset \
  write_flash 0x0 /tmp/build/esp8266_dial_v2.ino.bin
```

## PC 端配合

见 `pc-client/dial_listener.py`。必须常驻运行才能：
- 有线模式下回复 ACK 维持连接
- 接收 UDP 事件并模拟按键

## 握手协议

| 方向 | 消息 | 时机 |
|------|------|------|
| ESP → PC | `>HELLO\n` | BOOT 期每 500ms；WIRELESS 期每 10s |
| ESP → PC | `>PING\n`  | WIRED 期每 2s |
| PC → ESP | `ACK\n`    | 收到 HELLO 或 PING 后立即回 |
| ESP → PC | `>MODE wired\n` / `>MODE wireless\n` | 模式切换瞬间 |

超时：WIRED 模式 5 秒无 ACK → 切 WIRELESS；切换冷却 10s 防抖。

## 关联文档

- 详细设计：[PLAN.md](./PLAN.md)
- 项目根目录：[../../PLAN.md](../../PLAN.md)
- 回滚参考：旧固件保留在 `../esp8266_dial/` 和 `../encoder_test/`
