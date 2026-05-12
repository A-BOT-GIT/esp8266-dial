/*
 * encoder.h — EC11 编码器 + 按键状态机模块
 *
 * 从 encoder_test.ino 抽出，独立可复用。
 * 通过回调函数把事件发给上层，避免模块直接依赖 Serial/UDP。
 */

#ifndef INTEGRATED_ENCODER_H
#define INTEGRATED_ENCODER_H

#include <Arduino.h>

// 编码器事件类型
enum EncoderEvent {
  EV_ROTATE_LEFT,   // 左旋一格
  EV_ROTATE_RIGHT,  // 右旋一格
  EV_BTN_DOWN,      // 按键按下
  EV_BTN_UP,        // 按键松开
  EV_BTN_PRESS,     // 短按（按下到松开 < 500ms）
  EV_BTN_LONG,      // 长按触发（按下 >= 500ms）
  EV_BTN_HOLD       // 持续按住（每 1s）
};

// 事件回调：上层按 mode 决定走串口还是 UDP
typedef void (*EncoderCallback)(EncoderEvent ev, long value);

class Encoder {
 public:
  // 初始化引脚 + 回调
  void begin(uint8_t pinClk, uint8_t pinDt, uint8_t pinSw, EncoderCallback cb);

  // 主循环调用（高频）
  void loop();

  // 重置状态（模式切换时调用，避免残留）
  void reset();

  // 查询接口
  long position() const { return encPos_; }
  bool clk() const { return digitalRead(pinClk_); }
  bool dt()  const { return digitalRead(pinDt_);  }
  bool sw()  const { return digitalRead(pinSw_);  }
  unsigned long pressCount() const { return btnCount_; }

 private:
  // 引脚
  uint8_t pinClk_ = 0;
  uint8_t pinDt_  = 0;
  uint8_t pinSw_  = 0;

  EncoderCallback cb_ = nullptr;

  // 编码器状态机
  uint8_t encState_ = 0;
  long    encPos_   = 0;
  unsigned long lastEncEventMs_ = 0;

  // 按键状态机
  bool btnLast_     = HIGH;
  bool btnDown_     = false;
  bool btnHandled_  = false;
  unsigned long btnPressMs_    = 0;
  unsigned long btnCount_      = 0;
  unsigned long lastHoldMs_    = 0;
  unsigned long btnEdgeMs_     = 0;  // 上次检测到边沿的时间（软件防抖用）
  bool          btnPending_    = false;  // 正在等防抖窗口确认

  void readEncoder_();
  void readButton_();
};

#endif
