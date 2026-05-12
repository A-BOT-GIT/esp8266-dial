"""
ESP8266 Dial Listener — 双模式接收版

- SerialReader 线程：扫描 COM 口 → 打开串口 → 回 ACK → 解析 >EVENT
- UDPReader 线程：监听 UDP 8888 → 解析 JSON action
- 两者共享 KEY_MAP 动作映射，行为一致
- 无黑窗口后台运行（配合 pyinstaller --noconsole 打包）
"""

import json
import logging
import os
import re
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pynput.keyboard import Key, Controller

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None  # pyserial 未安装则只走 UDP

# ── Windows 控制台 QuickEdit 禁用（--console 打包时才用到） ──
if sys.platform == "win32" and sys.stdout is not None and sys.stdout.isatty():
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(h, ctypes.byref(mode))
        kernel32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


# ── 日志：写文件，避免 --noconsole 下的 stdout 异常 ───────
def _log_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.path.expanduser("~/.local/share")
    d = Path(base) / "dial"
    d.mkdir(parents=True, exist_ok=True)
    return d


_log_initialized = False


def _setup_logging():
    global _log_initialized
    if _log_initialized:
        return
    _log_initialized = True

    log_file = _log_dir() / "dial.log"
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # 有 tty 时也回显到控制台
    if sys.stdout is not None and sys.stdout.isatty():
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
        root.addHandler(ch)


log = logging.getLogger("dial")


# ── 按键映射 ────────────────────────────────────────────
# 键是动作名（有线/无线事件都会转为此名），值是 (pynput Key, 描述)
KEY_MAP = {
    "left":      (Key.media_volume_down, "音量减小"),
    "right":     (Key.media_volume_up,   "音量增大"),
    "press":     (Key.media_play_pause,  "播放/暂停"),
    "longpress": (None,                   "Win+D 回到桌面"),  # 特殊处理
}

UDP_IP   = "0.0.0.0"
UDP_PORT = 8888


# ── 动作执行 ────────────────────────────────────────────
class ActionRunner:
    def __init__(self):
        self.keyboard = Controller()
        self._lock = threading.Lock()

    def run(self, action: str, source: str):
        with self._lock:
            try:
                if action == "longpress":
                    log.info("[%s] longpress → Win+D 回到桌面", source)
                    self._win_d()
                    return
                if action in KEY_MAP:
                    key, desc = KEY_MAP[action]
                    log.info("[%s] %s → %s", source, action, desc)
                    if key is not None:
                        self.keyboard.press(key)
                        self.keyboard.release(key)
                else:
                    log.warning("[%s] 未知动作: %s", source, action)
            except Exception as e:
                log.error("[%s] 动作执行失败: %s", source, e)

    def _win_d(self):
        self.keyboard.press(Key.cmd_l)
        self.keyboard.press("d")
        self.keyboard.release("d")
        self.keyboard.release(Key.cmd_l)


# ── 串口接收 ────────────────────────────────────────────
class SerialReader(threading.Thread):
    """
    扫描可用串口，打开后阻塞读取 >EVENT 行。
    遇到 >HELLO / >PING 自动回 ACK。
    """

    # 串口 VID/描述特征（常见 ESP8266 USB 转串口芯片）
    PORT_HINTS = ("CH340", "CH341", "CP210", "FTDI", "USB-SERIAL", "wchusbserial")

    # 事件正则
    RE_RIGHT = re.compile(r"^>RIGHT\s+pos=(-?\d+)")
    RE_LEFT  = re.compile(r"^>LEFT\s+pos=(-?\d+)")
    RE_PRESS = re.compile(r"^>PRESS\s+#(\d+)")
    RE_LONG  = re.compile(r"^>LONG\s+(\d+)")

    def __init__(self, runner: ActionRunner):
        super().__init__(daemon=True, name="serial")
        self.runner = runner
        self._stop = threading.Event()
        self._ser = None

    def stop(self):
        self._stop.set()

    def run(self):
        if serial is None:
            log.warning("pyserial 未安装，串口功能禁用")
            return

        log.info("开始扫描 ESP8266 串口...")
        scan_count = 0
        while not self._stop.is_set():
            port = self._find_port()
            if port is None:
                scan_count += 1
                if scan_count % 15 == 1:  # 每 30 秒提示一次
                    log.info("未检测到 ESP8266 串口，继续扫描...")
                self._stop.wait(2.0)
                continue
            scan_count = 0

            try:
                log.info("尝试打开串口 %s", port)
                self._ser = serial.Serial(
                    port=port,
                    baudrate=115200,
                    timeout=None,   # 阻塞读，不轮询
                    write_timeout=1.0,
                )
                # 关闭 DTR/RTS 避免触发 ESP 复位
                self._ser.dtr = False
                self._ser.rts = False
                log.info("串口已连接 %s", port)
                self._read_loop()
            except serial.SerialException as e:
                log.info("串口 %s 异常: %s", port, e)
            except Exception as e:
                log.error("串口意外错误: %s", e)
            finally:
                if self._ser:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                log.info("串口已关闭，2 秒后重新扫描")
                self._stop.wait(2.0)

    def _find_port(self):
        try:
            for p in serial.tools.list_ports.comports():
                desc = (p.description or "") + " " + (p.manufacturer or "")
                if any(hint.lower() in desc.lower() for hint in self.PORT_HINTS):
                    return p.device
        except Exception as e:
            log.error("扫描串口失败: %s", e)
        return None

    def _read_loop(self):
        ser = self._ser
        assert ser is not None
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = ser.read(1)   # 阻塞，来一个字节返回
            except serial.SerialException:
                raise
            if not chunk:
                continue
            buf += chunk
            # 批量读完当前缓冲
            if ser.in_waiting:
                buf += ser.read(ser.in_waiting)

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip(b"\r\x00").decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self._handle_line(line)

    def _handle_line(self, line: str):
        # 握手：HELLO / PING 立即回 ACK
        if line.startswith(">HELLO") or line.startswith(">PING"):
            try:
                self._ser.write(b"ACK\n")
            except Exception as e:
                log.error("ACK 写入失败: %s", e)
            return

        if line.startswith(">MODE"):
            log.info("设备切换: %s", line)
            return

        if line.startswith(">STATUS") or line.startswith(">BOOT"):
            return  # 忽略状态行

        # 事件解析
        if self.RE_RIGHT.match(line):
            self.runner.run("right", "serial")
        elif self.RE_LEFT.match(line):
            self.runner.run("left", "serial")
        elif self.RE_PRESS.match(line):
            self.runner.run("press", "serial")
        elif self.RE_LONG.match(line):
            self.runner.run("longpress", "serial")
        elif line.startswith(">DOWN") or line.startswith(">UP") or line.startswith(">HOLD"):
            pass   # DOWN/UP/HOLD 不触发动作
        else:
            log.debug("未解析行: %s", line)


# ── UDP 接收 ────────────────────────────────────────────
class UDPReader(threading.Thread):
    def __init__(self, runner: ActionRunner):
        super().__init__(daemon=True, name="udp")
        self.runner = runner
        self._stop = threading.Event()
        self._sock = None

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass

    def run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((UDP_IP, UDP_PORT))
        except OSError as e:
            log.error("UDP 端口 %d 绑定失败: %s", UDP_PORT, e)
            return

        self._sock.settimeout(None)   # 阻塞读
        log.info("UDP 监听 %d", UDP_PORT)

        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
            except OSError:
                break
            except Exception as e:
                log.error("UDP 接收错误: %s", e)
                continue

            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                log.warning("UDP JSON 解析失败: %r", data)
                continue

            action = payload.get("action", "")
            if action:
                self.runner.run(action, f"udp:{addr[0]}")


# ── 主入口 ──────────────────────────────────────────────
def main():
    _setup_logging()

    log.info("=" * 40)
    log.info("ESP8266 Dial Listener 启动")
    log.info("日志文件: %s", _log_dir() / "dial.log")
    log.info("按键映射:")
    for a, (_, d) in KEY_MAP.items():
        log.info("  %-10s → %s", a, d)

    runner = ActionRunner()

    sr = SerialReader(runner)
    ur = UDPReader(runner)

    sr.start()
    ur.start()

    try:
        while sr.is_alive() or ur.is_alive():
            time.sleep(1.0)
    except KeyboardInterrupt:
        log.info("收到退出信号")
    finally:
        sr.stop()
        ur.stop()
        sr.join(timeout=2)
        ur.join(timeout=2)
        log.info("已退出")


if __name__ == "__main__":
    main()
