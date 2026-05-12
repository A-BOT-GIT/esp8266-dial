# ESP8266 Dial PC Listener

## 文件

| 文件 | 用途 |
|------|------|
| `dial_listener.py` | 主程序（双模式接收） |
| `requirements.txt` | Python 依赖清单 |
| `run_debug.bat` | 前台调试运行（看日志） |
| `build_windows.bat` | 打包成无窗口 exe |
| `install_autostart.bat` | 注册开机自启 |
| `uninstall_autostart.bat` | 取消自启 |

## 首次使用

### 1. 安装 Python 3.10+
https://www.python.org/downloads/ — 安装时勾选 **Add Python to PATH**。

### 2. 禁用 Windows 串口鼠标（一次性）
以管理员身份打开 CMD：
```cmd
REG ADD "HKLM\SYSTEM\CurrentControlSet\Services\sermouse" /V Start /T REG_DWORD /F /D 4
```
**重启电脑。** 否则插 ESP8266 后鼠标会乱跳。

### 3. 调试运行
双击 `run_debug.bat`，首次运行自动装依赖，然后前台启动，能看到实时日志。

验证没问题后继续第 4 步。

### 4. 打包成后台 exe
双击 `build_windows.bat`，等待完成后产物在 `dist\dial_listener.exe`，双击即后台运行，无窗口。

### 5. 开机自启（可选）
双击 `install_autostart.bat`，下次开机自动后台启动。

## 运行时

- **日志文件**：`%LOCALAPPDATA%\dial\dial.log`
  在资源管理器地址栏输入即可打开
- **实时看日志**（PowerShell）：
  ```powershell
  Get-Content $env:LOCALAPPDATA\dial\dial.log -Wait -Tail 20
  ```
- **查进程**：
  ```cmd
  tasklist | findstr dial
  ```
- **杀进程**：
  ```cmd
  taskkill /IM dial_listener.exe /F
  ```

## 按键映射（默认硬编码）

| 旋钮动作 | 执行 |
|---------|------|
| 右转 | 音量增大 |
| 左转 | 音量减小 |
| 短按 | 播放/暂停 |
| 长按 | Win+D 回到桌面 |

修改 `dial_listener.py` 里的 `KEY_MAP` 可自定义。

## 常见问题

**打包后 exe 启动没反应**
- 检查 `%LOCALAPPDATA%\dial\dial.log`，很可能有错误信息
- 检查任务管理器是否真的启动了进程（有时防病毒会误杀）

**串口始终连不上**
- 设备管理器看看 CH340 COM 口是否出现
- 关掉可能占用串口的程序（Arduino IDE、putty 等）

**旋转没反应但日志有事件**
- pynput 权限不足，用管理员身份运行试试

**UDP 无线模式收不到事件**
- Windows 连上 `ESP8266-Dial` WiFi
- 关掉 Windows 防火墙对 8888 端口的拦截，或放行 `dial_listener.exe`
