/*
 * ESP8266 Dial — 双模式主程序
 *
 * 模式识别：软件握手（不依赖硬件检测）
 *   - 上电后 3 秒观察窗口，持续发 >HELLO，收到 ACK → WIRED，否则 → WIRELESS
 *   - 运行中：WIRED 5 秒无 ACK 切 WIRELESS；WIRELESS 每 10 秒探测一次，收到 ACK 切 WIRED
 *   - 10 秒冷却窗口防抖
 *
 * 引脚：
 *   D5 (GPIO14) — EC11 CLK
 *   D6 (GPIO12) — EC11 DT   ← 从 D4 改过来，避免 strapping pin 冲突
 *   D3 (GPIO0)  — EC11 SW
 *   D4 (GPIO2)  — 板载 LED（低电平点亮）
 */

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include "encoder.h"
#include "wifi_module.h"

// ── 引脚 ────────────────────────────────────────────────
static const uint8_t PIN_LED = D4;
static const uint8_t PIN_CLK = D5;
static const uint8_t PIN_DT  = D6;
static const uint8_t PIN_SW  = D3;

// ── 握手时序 ────────────────────────────────────────────
static const unsigned long BOOT_WINDOW_MS        = 3000;   // 启动观察窗口
static const unsigned long HELLO_INTERVAL_MS     = 500;    // 启动期 HELLO 频率
static const unsigned long WIRED_PING_MS         = 2000;   // 有线模式心跳
static const unsigned long WIRED_ACK_TIMEOUT_MS  = 5000;   // 有线模式 ACK 超时
static const unsigned long WIRELESS_PROBE_MS     = 10000;  // 无线模式探测频率
static const unsigned long SWITCH_COOLDOWN_MS    = 10000;  // 切换冷却窗口

// ── 模式 ────────────────────────────────────────────────
enum Mode { MODE_BOOT, MODE_WIRED, MODE_WIRELESS };

static Encoder    encoder;
static WiFiModule wifi;

static Mode          mode           = MODE_BOOT;
static unsigned long bootStartMs    = 0;
static unsigned long lastHelloMs    = 0;
static unsigned long lastPingMs     = 0;
static unsigned long lastAckMs      = 0;
static unsigned long lastProbeMs    = 0;
static unsigned long lastSwitchMs   = 0;

// 启动观察窗口内缓存事件，确定模式后再发
struct PendingEvent {
  EncoderEvent ev;
  long value;
};
static const int PENDING_CAP = 16;
static PendingEvent pendingBuf[PENDING_CAP];
static int pendingCount = 0;

// ── LED 辅助 ────────────────────────────────────────────
static void ledSet(bool on)           { digitalWrite(PIN_LED, on ? LOW : HIGH); }
static void ledBlink(int n, int ms) {
  for (int i = 0; i < n; i++) {
    ledSet(true);  delay(ms);
    ledSet(false); if (i < n - 1) delay(ms / 2);
  }
}

// ── 串口输出 ────────────────────────────────────────────
static void emitSerialEvent_(EncoderEvent ev, long value) {
  switch (ev) {
    case EV_ROTATE_RIGHT: Serial.printf(">RIGHT pos=%ld\n", value); break;
    case EV_ROTATE_LEFT:  Serial.printf(">LEFT  pos=%ld\n", value); break;
    case EV_BTN_DOWN:     Serial.print(">DOWN\n");                  break;
    case EV_BTN_UP:       Serial.print(">UP\n");                    break;
    case EV_BTN_PRESS:    Serial.printf(">PRESS #%ld\n", value);    break;
    case EV_BTN_LONG:     Serial.printf(">LONG %lds\n", value);     break;
    case EV_BTN_HOLD:     Serial.printf(">HOLD %lds\n", value);     break;
  }
}

// 把编码器事件转成 UDP action 名
static const char* evToAction_(EncoderEvent ev) {
  switch (ev) {
    case EV_ROTATE_LEFT:  return "left";
    case EV_ROTATE_RIGHT: return "right";
    case EV_BTN_PRESS:    return "press";
    case EV_BTN_LONG:     return "longpress";
    default:              return nullptr;  // DOWN/UP/HOLD 不走 UDP
  }
}

// ── LED 事件反馈（模式无关） ────────────────────────────
static void ledFeedback_(EncoderEvent ev) {
  switch (ev) {
    case EV_ROTATE_LEFT:
    case EV_ROTATE_RIGHT: ledBlink(1, 8);  break;
    case EV_BTN_DOWN:     ledSet(true);    break;
    case EV_BTN_UP:       ledSet(false);   break;
    case EV_BTN_PRESS:    ledBlink(3, 30); break;
    case EV_BTN_LONG:     ledBlink(2, 40); ledSet(true); break;
    default: break;
  }
}

// ── 事件路由（核心分发） ────────────────────────────────
static void encoderCb(EncoderEvent ev, long value) {
  ledFeedback_(ev);

  if (mode == MODE_BOOT) {
    // 观察窗口：缓存事件，等模式确定后回放
    if (pendingCount < PENDING_CAP) {
      pendingBuf[pendingCount].ev    = ev;
      pendingBuf[pendingCount].value = value;
      pendingCount++;
    }
    return;
  }

  if (mode == MODE_WIRED) {
    emitSerialEvent_(ev, value);
  } else {
    const char* action = evToAction_(ev);
    if (action) wifi.sendAction(action, value);
  }
}

// ── 模式切换 ────────────────────────────────────────────
static void enterWired_() {
  if (mode == MODE_WIRED) return;

  if (wifi.isRunning()) wifi.stop();
  encoder.reset();

  mode = MODE_WIRED;
  lastSwitchMs = millis();
  lastPingMs   = millis();
  lastAckMs    = millis();

  Serial.print(">MODE wired\n");
  ledBlink(2, 80);
}

static void enterWireless_() {
  if (mode == MODE_WIRELESS) return;

  Serial.print(">MODE wireless\n");  // 切换前发出最后一条

  encoder.reset();
  if (!wifi.isRunning()) wifi.begin();

  mode = MODE_WIRELESS;
  lastSwitchMs = millis();
  lastProbeMs  = millis();

  ledBlink(3, 200);
}

// ── 观察窗口结束后，回放缓存事件 ────────────────────────
static void flushPending_() {
  for (int i = 0; i < pendingCount; i++) {
    if (mode == MODE_WIRED) {
      emitSerialEvent_(pendingBuf[i].ev, pendingBuf[i].value);
    } else {
      const char* a = evToAction_(pendingBuf[i].ev);
      if (a) wifi.sendAction(a, pendingBuf[i].value);
    }
  }
  pendingCount = 0;
}

// ── 串口接收：监听 ACK ──────────────────────────────────
static void readSerialAck_() {
  static char line[16];
  static uint8_t len = 0;

  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (len > 0) {
        line[len] = '\0';
        if (strcmp(line, "ACK") == 0) {
          lastAckMs = millis();
        }
        len = 0;
      }
    } else if (len < sizeof(line) - 1) {
      line[len++] = c;
    } else {
      len = 0;  // 溢出丢弃
    }
  }
}

// ── 握手 + 模式判断 ─────────────────────────────────────
static void handleHandshake_() {
  unsigned long now = millis();

  switch (mode) {
    case MODE_BOOT: {
      // 每 500ms 发 HELLO
      if (now - lastHelloMs >= HELLO_INTERVAL_MS) {
        lastHelloMs = now;
        Serial.print(">HELLO\n");
      }
      // 收到 ACK → WIRED
      if (lastAckMs > bootStartMs) {
        enterWired_();
        flushPending_();
      }
      // 窗口超时 → WIRELESS
      else if (now - bootStartMs >= BOOT_WINDOW_MS) {
        enterWireless_();
        flushPending_();
      }
      break;
    }

    case MODE_WIRED: {
      // 心跳
      if (now - lastPingMs >= WIRED_PING_MS) {
        lastPingMs = now;
        Serial.print(">PING\n");
      }
      // ACK 超时且过冷却窗口 → 切 WIRELESS
      if (now - lastAckMs >= WIRED_ACK_TIMEOUT_MS &&
          now - lastSwitchMs >= SWITCH_COOLDOWN_MS) {
        enterWireless_();
      }
      break;
    }

    case MODE_WIRELESS: {
      // 每 10 秒探测一次电脑是否接入
      if (now - lastProbeMs >= WIRELESS_PROBE_MS) {
        lastProbeMs = now;
        Serial.print(">HELLO\n");
      }
      // 收到 ACK 且过冷却窗口 → 切 WIRED
      if (lastAckMs > lastSwitchMs &&
          now - lastSwitchMs >= SWITCH_COOLDOWN_MS) {
        enterWired_();
      }
      break;
    }
  }
}

// ── setup / loop ────────────────────────────────────────
void setup() {
  pinMode(PIN_LED, OUTPUT);
  ledSet(false);

  Serial.begin(115200);
  delay(100);
  Serial.print("\n>BOOT integrated_test\n");

  // 默认 WiFi 关闭，进入 WIRED 时保持关，进入 WIRELESS 再开
  WiFi.mode(WIFI_OFF);
  WiFi.forceSleepBegin();

  encoder.begin(PIN_CLK, PIN_DT, PIN_SW, encoderCb);

  bootStartMs = millis();
  lastHelloMs = 0;     // 立即发第一条 HELLO
  lastAckMs   = 0;     // 未收到过 ACK
  mode        = MODE_BOOT;
}

void loop() {
  encoder.loop();
  wifi.loop();            // WIRELESS 模式下处理 HTTP 请求；其他模式内部直接 return
  readSerialAck_();
  handleHandshake_();

  // 可选：WIRED 模式下定期发 STATUS（调试用，默认关闭）
  // 打开方式：把下方 #if 0 改成 #if 1
#if 0
  static unsigned long lastStatusMs = 0;
  if (mode == MODE_WIRED && millis() - lastStatusMs >= 1000) {
    lastStatusMs = millis();
    Serial.printf(">STATUS clk=%d dt=%d sw=%d pos=%ld\n",
                  encoder.clk(), encoder.dt(), encoder.sw(),
                  encoder.position());
  }
#endif
}
