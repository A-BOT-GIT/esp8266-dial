/*
 * wifi_module.h — WiFi AP + UDP 广播 + HTTP Web 控制页
 *
 * 从 esp8266_dial.ino 抽出，支持动态启停（模式切换时使用）。
 */

#ifndef INTEGRATED_WIFI_MODULE_H
#define INTEGRATED_WIFI_MODULE_H

#include <Arduino.h>

class WiFiModule {
 public:
  // 启动 AP + UDP + Web
  void begin();

  // 停止 WiFi（切到有线模式时调用，省电降热）
  void stop();

  // 主循环：处理 HTTP 请求（仅运行中调用）
  void loop();

  // 发送动作到 UDP 广播
  void sendAction(const char* action, long pos);

  // 是否正在运行
  bool isRunning() const { return running_; }

  // 客户端数量
  uint8_t clientCount() const;

 private:
  bool running_ = false;
  unsigned long startMs_ = 0;

  void setupRoutes_();
};

#endif
