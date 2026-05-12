#include "wifi_module.h"

#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <ESP8266WebServer.h>

// ── 配置 ────────────────────────────────────────────────
static const char*    AP_SSID      = "ESP8266-Dial";
static const char*    AP_PASSWORD  = "12345678";
static const IPAddress AP_IP       (192, 168, 4, 1);
static const IPAddress AP_GW       (192, 168, 4, 1);
static const IPAddress AP_MASK     (255, 255, 255, 0);
static const IPAddress BCAST_IP    (192, 168, 4, 255);
static const uint16_t  UDP_PORT    = 8888;

// ── 模块级对象（避免头文件暴露 ESP8266WiFi 依赖） ──────
static WiFiUDP          udp;
static ESP8266WebServer server(80);

// ── HTML 页面（AP 模式手机控制） ────────────────────────
static const char PAGE_INDEX[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>ESP8266 Dial</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#eee;font-family:-apple-system,sans-serif;
  display:flex;justify-content:center;align-items:center;min-height:100dvh;
  touch-action:manipulation;user-select:none}
.c{text-align:center;padding:20px;max-width:420px;width:100%}
h1{font-size:1.5rem;margin-bottom:4px;letter-spacing:2px}
.sub{color:#888;font-size:0.8rem;margin-bottom:24px}
.ring{width:210px;height:210px;border-radius:50%;border:4px solid #333;
  background:#16213e;margin:0 auto 20px;display:flex;
  align-items:center;justify-content:center;
  box-shadow:0 0 30px rgba(0,150,255,0.1),inset 0 0 20px rgba(0,0,0,0.5)}
.btn{border:none;border-radius:14px;font-weight:bold;cursor:pointer;
  background:linear-gradient(145deg,#1a1a3e,#0d0d1f);
  box-shadow:3px 3px 10px rgba(0,0,0,0.4);transition:all 0.08s}
.btn:active{transform:scale(0.92);box-shadow:inset 2px 2px 8px rgba(0,0,0,0.7)}
.btn-press{width:110px;height:110px;border-radius:50%;color:#0f6;font-size:1rem}
.row{display:flex;justify-content:center;gap:16px;margin-bottom:16px}
.btn-side{width:95px;height:55px;font-size:0.95rem}
.btn-l{color:#f60}.btn-r{color:#0f6}.btn-long{color:#ff6;padding:0 24px;height:55px;font-size:0.95rem}
.status{margin-top:16px;padding:8px 16px;background:#16213e;border-radius:20px;
  display:inline-block;font-size:0.8rem;color:#888}
</style></head><body><div class="c">
<h1>ESP8266 DIAL</h1><p class="sub">无线旋钮 · AP 模式</p>
<div class="ring"><button class="btn btn-press" id="bp">按下</button></div>
<div class="row">
  <button class="btn btn-side btn-l" id="bl">&larr; 左转</button>
  <button class="btn btn-side btn-r" id="br">右转 &rarr;</button>
</div>
<button class="btn btn-long" id="bg">长按</button>
<div class="status" id="s">就绪</div>
</div><script>
const s=document.getElementById('s');
function act(a){fetch('/action?cmd='+a).then(r=>r.text()).then(t=>{s.textContent=t}).catch(()=>{s.textContent='请求失败'})}
bp.onclick=()=>act('press');bl.onclick=()=>act('left');
br.onclick=()=>act('right');bg.onclick=()=>act('longpress');
</script></body></html>
)rawliteral";

// ── 路由 ────────────────────────────────────────────────
static void handleRoot_() {
  server.send_P(200, "text/html", PAGE_INDEX);
}

static void handleAction_() {
  if (!server.hasArg("cmd")) {
    server.send(400, "text/plain", "ERR: missing cmd");
    return;
  }
  String cmd = server.arg("cmd");
  cmd.trim();
  if (cmd != "left" && cmd != "right" && cmd != "press" && cmd != "longpress") {
    server.send(400, "text/plain", "ERR: unknown: " + cmd);
    return;
  }
  // 通过 UDP 广播出去，PC 端 listener 会收到
  String msg = "{\"action\":\"" + cmd + "\",\"pos\":0}";
  udp.beginPacket(BCAST_IP, UDP_PORT);
  udp.write(msg.c_str());
  udp.endPacket();
  server.send(200, "text/plain", "OK: " + cmd);
}

static void handleStatus_() {
  String json = "{";
  json += "\"uptime_ms\":" + String(millis()) + ",";
  json += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",";
  json += "\"clients\":"   + String(WiFi.softAPgetStationNum()) + ",";
  json += "\"ap_ip\":\""   + WiFi.softAPIP().toString() + "\"";
  json += "}";
  server.send(200, "application/json", json);
}

static void handleNotFound_() {
  server.send(404, "text/plain", "404");
}

// ── WiFiModule 实现 ─────────────────────────────────────
void WiFiModule::setupRoutes_() {
  server.on("/",       handleRoot_);
  server.on("/action", handleAction_);
  server.on("/status", handleStatus_);
  server.onNotFound(handleNotFound_);
}

void WiFiModule::begin() {
  if (running_) return;

  // 从 forceSleep 唤醒（若之前调用过 stop）
  WiFi.forceSleepWake();
  delay(1);

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_GW, AP_MASK);
  WiFi.softAP(AP_SSID, AP_PASSWORD, 1, 0, 4);  // ch1, 非隐藏, 最多 4 连接

  // 只发不收：不调 udp.begin(port)。beginPacket/write/endPacket 足够
  setupRoutes_();
  server.begin();

  running_ = true;
  startMs_ = millis();
}

void WiFiModule::stop() {
  if (!running_) return;

  server.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  WiFi.forceSleepBegin();
  delay(1);

  running_ = false;
}

void WiFiModule::loop() {
  if (!running_) return;
  server.handleClient();
}

void WiFiModule::sendAction(const char* action, long pos) {
  if (!running_) return;

  char buf[64];
  snprintf(buf, sizeof(buf), "{\"action\":\"%s\",\"pos\":%ld}", action, pos);

  udp.beginPacket(BCAST_IP, UDP_PORT);
  udp.write(buf);
  udp.endPacket();
}

uint8_t WiFiModule::clientCount() const {
  if (!running_) return 0;
  return WiFi.softAPgetStationNum();
}
