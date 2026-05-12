"""
ESP8266 Dial Listener — 双模式接收版（带托盘图标）

- SerialReader 线程：扫描 COM 口 → 打开串口 → 回 ACK → 解析 >EVENT
- UDPReader 线程：监听 UDP 8888 → 解析 JSON action
- 两者共享 KEY_MAP 动作映射，行为一致
- 主线程运行 pystray 托盘图标（右键菜单 / 动态状态色）
- 无黑窗口后台运行（配合 pyinstaller --noconsole 打包）
"""

import json
import logging
import os
import re
import signal
import socket
import subprocess
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

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

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
KEY_MAP = {
    "left":      (Key.media_volume_down, "音量减小"),
    "right":     (Key.media_volume_up,   "音量增大"),
    "press":     (Key.media_play_pause,  "播放/暂停"),
    "longpress": (None,                   "Win+D 回到桌面"),
}

UDP_IP   = "0.0.0.0"
UDP_PORT = 8888


# ── 全局状态（给托盘图标用） ───────────────────────────
class State:
    """线程安全的设备状态快照"""

    MODE_DISCONNECTED = "未连接"
    MODE_WIRED        = "有线"
    MODE_WIRELESS     = "无线"

    def __init__(self):
        self._lock = threading.Lock()
        self.mode = State.MODE_DISCONNECTED
        self.last_event = "—"
        self.last_event_time = 0.0
        self.event_count = 0
        self.tray_icon = None   # 由 main 注入

    def set_mode(self, mode: str):
        with self._lock:
            if self.mode == mode:
                return
            self.mode = mode
        log.info("状态变更: %s", mode)
        # 模式变更：重绘图标 + 更新菜单
        self._refresh_icon()
        self._refresh_menu()

    def record_event(self, action: str):
        with self._lock:
            self.last_event = action
            self.last_event_time = time.time()
            self.event_count += 1
        # 事件只更新菜单文本，不重绘图标（避免高频旋转时 UI 拥塞）
        self._refresh_menu()

    def snapshot(self):
        with self._lock:
            return (self.mode, self.last_event, self.event_count)

    def _refresh_icon(self):
        if self.tray_icon is None:
            return
        try:
            self.tray_icon.icon = _make_icon_image(self.mode)
            self.tray_icon.title = f"ESP8266 Dial — {self.mode}"
        except Exception as e:
            log.debug("刷新托盘图标失败: %s", e)

    def _refresh_menu(self):
        if self.tray_icon is None:
            return
        try:
            if hasattr(self.tray_icon, "update_menu"):
                self.tray_icon.update_menu()
        except Exception as e:
            log.debug("刷新托盘菜单失败: %s", e)


STATE = State()


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
                    STATE.record_event("longpress")
                    return
                if action in KEY_MAP:
                    key, desc = KEY_MAP[action]
                    log.info("[%s] %s → %s", source, action, desc)
                    if key is not None:
                        self.keyboard.press(key)
                        self.keyboard.release(key)
                    STATE.record_event(action)
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
    PORT_HINTS = ("CH340", "CH341", "CP210", "FTDI", "USB-SERIAL", "wchusbserial")

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
                if scan_count % 15 == 1:
                    log.info("未检测到 ESP8266 串口，继续扫描...")
                self._stop.wait(2.0)
                continue
            scan_count = 0

            try:
                log.info("尝试打开串口 %s", port)
                self._ser = serial.Serial(
                    port=port,
                    baudrate=115200,
                    timeout=None,
                    write_timeout=1.0,
                )
                self._ser.dtr = False
                self._ser.rts = False
                log.info("串口已连接 %s", port)
                # 连上串口但模式未知，等到收到 >MODE 再更新
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
                # 失去有线连接，但 UDP 还能收 → 如果处于 WIRED 就降级到 DISCONNECTED
                if STATE.mode == State.MODE_WIRED:
                    STATE.set_mode(State.MODE_DISCONNECTED)
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
        MAX_BUF = 8192  # 防止对端狂发无 \n 的垃圾导致内存膨胀
        while not self._stop.is_set():
            try:
                chunk = ser.read(1)
            except serial.SerialException:
                raise
            if not chunk:
                continue
            buf += chunk
            if ser.in_waiting:
                buf += ser.read(ser.in_waiting)

            if len(buf) > MAX_BUF:
                log.warning("串口缓冲溢出（%d 字节），丢弃", len(buf))
                buf = b""
                continue

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip(b"\r\x00").decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self._handle_line(line)

    def _handle_line(self, line: str):
        if line.startswith(">HELLO") or line.startswith(">PING"):
            try:
                self._ser.write(b"ACK\n")
            except Exception as e:
                log.error("ACK 写入失败: %s", e)
            return

        if line.startswith(">MODE"):
            log.info("设备切换: %s", line)
            if "wired" in line:
                STATE.set_mode(State.MODE_WIRED)
            elif "wireless" in line:
                STATE.set_mode(State.MODE_WIRELESS)
            return

        if line.startswith(">STATUS") or line.startswith(">BOOT"):
            return

        if self.RE_RIGHT.match(line):
            self.runner.run("right", "serial")
        elif self.RE_LEFT.match(line):
            self.runner.run("left", "serial")
        elif self.RE_PRESS.match(line):
            self.runner.run("press", "serial")
        elif self.RE_LONG.match(line):
            self.runner.run("longpress", "serial")
        elif line.startswith(">DOWN") or line.startswith(">UP") or line.startswith(">HOLD"):
            pass
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

        self._sock.settimeout(None)
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
                # UDP 收到事件 → 当前一定是 WIRELESS 模式
                if STATE.mode != State.MODE_WIRELESS:
                    STATE.set_mode(State.MODE_WIRELESS)
                self.runner.run(action, f"udp:{addr[0]}")


# ── 托盘图标 ────────────────────────────────────────────
_MODE_COLOR = {
    State.MODE_DISCONNECTED: (136, 136, 136),  # 灰
    State.MODE_WIRED:        (0,   170, 0),    # 绿
    State.MODE_WIRELESS:     (0,   102, 255),  # 蓝
}


def _make_icon_image(mode: str):
    """生成一张 64x64 的单色圆形图标"""
    if Image is None:
        return None
    color = _MODE_COLOR.get(mode, (136, 136, 136))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color + (255,), outline=(255, 255, 255, 230), width=3)
    # 中心画 D
    try:
        from PIL import ImageFont
        # PIL 默认字体可能不够大，用 load_default 就行
        font = ImageFont.load_default()
        draw.text((22, 18), "D", fill=(255, 255, 255, 255), font=font)
    except Exception:
        pass
    return img


def _open_log_file(icon=None, item=None):
    """右键菜单：打开日志文件"""
    path = str(_log_dir() / "dial.log")
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        log.error("打开日志失败: %s", e)


def _open_log_dir(icon=None, item=None):
    """右键菜单：打开日志目录"""
    path = str(_log_dir())
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        log.error("打开日志目录失败: %s", e)


def _quit_app(icon, item=None):
    log.info("用户通过托盘退出")
    icon.visible = False
    icon.stop()


def _build_tray_icon():
    """构造托盘图标（需要在主线程调用 icon.run()）"""
    if pystray is None:
        log.warning("pystray 未安装，托盘图标禁用")
        return None

    def _mode_label(_item=None):
        mode, last, count = STATE.snapshot()
        return f"模式: {mode}"

    def _event_label(_item=None):
        _, last, count = STATE.snapshot()
        if count == 0:
            return "最近事件: —"
        return f"最近: {last}  (#{count})"

    menu = pystray.Menu(
        pystray.MenuItem(_mode_label, None, enabled=False),
        pystray.MenuItem(_event_label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开日志文件", _open_log_file),
        pystray.MenuItem("打开日志目录", _open_log_dir),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _quit_app),
    )

    icon = pystray.Icon(
        "dial_listener",
        icon=_make_icon_image(STATE.mode),
        title="ESP8266 Dial — 未连接",
        menu=menu,
    )
    return icon


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

    icon = _build_tray_icon()
    STATE.tray_icon = icon

    if icon is not None:
        # Windows 下 pystray 的消息泵会吞 Ctrl+C，主动注册信号处理器
        def _sig_handler(sig, _frame):
            log.info("收到信号 %s，触发退出", sig)
            try:
                icon.stop()
            except Exception:
                pass

        try:
            signal.signal(signal.SIGINT, _sig_handler)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, _sig_handler)
        except Exception as e:
            log.debug("注册信号处理器失败（非主线程?）: %s", e)

        try:
            icon.run()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C（fallback）")
    else:
        # 没有 pystray，退化成轮询等线程
        try:
            while sr.is_alive() or ur.is_alive():
                time.sleep(1.0)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C")

    log.info("正在退出...")
    sr.stop()
    ur.stop()
    sr.join(timeout=2)
    ur.join(timeout=2)
    log.info("已退出")


if __name__ == "__main__":
    main()
