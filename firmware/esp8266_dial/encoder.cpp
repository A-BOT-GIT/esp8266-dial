#include "encoder.h"

void Encoder::begin(uint8_t pinClk, uint8_t pinDt, uint8_t pinSw, EncoderCallback cb) {
  pinClk_ = pinClk;
  pinDt_  = pinDt;
  pinSw_  = pinSw;
  cb_     = cb;

  pinMode(pinClk_, INPUT_PULLUP);
  pinMode(pinDt_,  INPUT_PULLUP);
  pinMode(pinSw_,  INPUT_PULLUP);
}

void Encoder::loop() {
  readEncoder_();
  readButton_();
}

void Encoder::reset() {
  // 清编码器状态机（切换模式时避免半步残留）
  encState_ = 0;
  lastEncEventMs_ = 0;

  // 清按键状态（避免长按跨模式触发）
  btnLast_     = digitalRead(pinSw_);
  btnDown_     = false;
  btnHandled_  = false;
  btnPressMs_  = 0;
  lastHoldMs_  = 0;
  btnEdgeMs_   = 0;
  btnPending_  = false;
  // 注意：encPos_ 和 btnCount_ 保留，用户期望位置和累计计数连续
}

// ── 编码器状态机 + 3ms 软件防抖 ─────────────────────────
void Encoder::readEncoder_() {
  unsigned long now = millis();
  if (now - lastEncEventMs_ < 3) return;  // 防抖窗口

  uint8_t clk = digitalRead(pinClk_);
  uint8_t dt  = digitalRead(pinDt_);

  encState_ = ((encState_ << 2) | (clk << 1) | dt) & 0x0F;

  if (encState_ == 0b1101 || encState_ == 0b0010) {
    lastEncEventMs_ = now;
    encPos_++;
    if (cb_) cb_(EV_ROTATE_RIGHT, encPos_);
  } else if (encState_ == 0b1110 || encState_ == 0b0001) {
    lastEncEventMs_ = now;
    encPos_--;
    if (cb_) cb_(EV_ROTATE_LEFT, encPos_);
  }
}

// ── 按键状态机：短按 / 长按 / HOLD（时间戳防抖，无 delay） ──
void Encoder::readButton_() {
  unsigned long now = millis();
  bool sw = digitalRead(pinSw_);

  // 边沿检测 + 5ms 时间戳防抖（不阻塞主循环）
  if (sw != btnLast_) {
    if (!btnPending_) {
      // 首次检测到电平变化，开始计时
      btnPending_ = true;
      btnEdgeMs_  = now;
    } else if (now - btnEdgeMs_ >= 5) {
      // 5ms 后复核电平，仍然不同 → 确认为真实边沿
      btnPending_ = false;
      btnLast_    = sw;

      if (sw == LOW) {
        // 按下
        btnDown_    = true;
        btnPressMs_ = now;
        btnHandled_ = false;
        lastHoldMs_ = now;
        if (cb_) cb_(EV_BTN_DOWN, 0);
      } else {
        // 松开
        if (btnDown_ && !btnHandled_ && (now - btnPressMs_ < 500)) {
          btnCount_++;
          if (cb_) cb_(EV_BTN_PRESS, btnCount_);
        }
        btnDown_ = false;
        if (cb_) cb_(EV_BTN_UP, 0);
      }
    }
    // 未到防抖时间：继续等待，不 block
  } else {
    // 电平稳定
    btnPending_ = false;
  }

  // 长按首次触发（500ms）
  if (btnDown_ && !btnHandled_ && (now - btnPressMs_ >= 500)) {
    unsigned long s = (now - btnPressMs_) / 1000;
    btnHandled_ = true;
    lastHoldMs_ = now;
    if (cb_) cb_(EV_BTN_LONG, s);
  }

  // HOLD：每秒重复一次
  if (btnDown_ && btnHandled_ && (now - lastHoldMs_ >= 1000)) {
    lastHoldMs_ = now;
    unsigned long s = (now - btnPressMs_) / 1000;
    if (cb_) cb_(EV_BTN_HOLD, s);
  }
}
