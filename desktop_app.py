import os
import sys
import time
import socket
import threading
import ctypes
import multiprocessing

# Khắc phục lỗi sys.stdout / sys.stderr trong chế độ GUI/windowed của Windows
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Xác định thư mục ứng dụng
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR

os.chdir(APP_DIR)
if RESOURCE_DIR not in sys.path:
    sys.path.insert(0, RESOURCE_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if os.path.exists(os.path.join(APP_DIR, "_internal")):
    sys.path.insert(0, os.path.join(APP_DIR, "_internal"))

import uvicorn
import webview
# Ưu tiên nạp main.py trực tiếp từ thư mục cài đặt (đảm bảo logic backend luôn mới nhất)
main_py_path = os.path.join(APP_DIR, "main.py")
if os.path.exists(main_py_path):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("main_live", main_py_path)
        main_mod = importlib.util.module_from_spec(spec)
        sys.modules["main"] = main_mod
        spec.loader.exec_module(main_mod)
        app = main_mod.app
        init_db = main_mod.init_db
    except Exception as e:
        from main import app, init_db
else:
    from main import app, init_db

WINDOW_TITLE = "TattPro - Quản lý tiệm xăm"

def check_single_instance():
    try:
        mutex_name = "Local\\TattPro_SingleInstance_Mutex_8688"
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183: # ERROR_ALREADY_EXISTS: Đã có ứng dụng đang chạy
            # Tìm cửa sổ TattPro đang mở và đưa lên trước mặt người dùng
            try:
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                def enum_proc(hwnd, lParam):
                    if user32.IsWindowVisible(hwnd):
                        length = user32.GetWindowTextLengthW(hwnd)
                        if length > 0:
                            buff = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, buff, length + 1)
                            if "TattPro" in buff.value:
                                user32.ShowWindow(hwnd, 9) # SW_RESTORE
                                user32.SetForegroundWindow(hwnd)
                                return False
                    return True
                user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
            except Exception:
                hwnd = user32.FindWindowW(None, WINDOW_TITLE)
                if hwnd:
                    user32.ShowWindow(hwnd, 9)
                    user32.SetForegroundWindow(hwnd)
            
            # KHÓA CHẶT 1 CỬA SỔ DUY NHẤT: Thoát ngay lập tức, tuyệt đối không mở cửa sổ thứ 2
            sys.exit(0)
        return mutex
    except Exception:
        return None

def find_free_port(preferred_port=8688):
    for p in [preferred_port] + list(range(8689, 8750)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    return preferred_port

class UvicornServerThread(threading.Thread):
    def __init__(self, port):
        super().__init__()
        self.daemon = True
        self.port = port
        config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            loop="asyncio",
            http="h11"
        )
        self.server = uvicorn.Server(config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True

def wait_for_server(port, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(0.15)
    return False

def apply_native_window_icon(icon_path):
    """Gán logo ứng dụng trực tiếp vào thanh tiêu đề (Title Bar) và Taskbar của Windows"""
    if not os.path.exists(icon_path):
        return

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    WM_SETICON = 0x0080
    ICON_SMALL = 0  # Biểu tượng 16x16 trên thanh tiêu đề góc trên bên trái
    ICON_BIG = 1    # Biểu tượng 32x32 trên thanh Taskbar và Alt+Tab

    for delay in [0.3, 0.8, 1.5, 3.0]:
        time.sleep(delay)
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
            if hwnd:
                hicon_small = ctypes.windll.user32.LoadImageW(0, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                hicon_big = ctypes.windll.user32.LoadImageW(0, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                if hicon_small:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                if hicon_big:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                break
        except Exception:
            pass

def main():
    _instance_mutex = check_single_instance()

    # 1. Khởi tạo Cơ sở dữ liệu SQLite
    try:
        init_db()
    except Exception as e:
        print(f"Lỗi khởi tạo DB: {e}")

    # 2. Tìm cổng mạng khả dụng
    port = find_free_port(8688)

    # 3. Khởi chạy máy chủ ngầm
    server_thread = UvicornServerThread(port)
    server_thread.start()

    # 4. Chờ máy chủ sẵn sàng
    wait_for_server(port, timeout=10.0)

    app_url = f"http://127.0.0.1:{port}"

    # Tìm đường dẫn file icon
    icon_path = os.path.join(RESOURCE_DIR, "tattpro.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(APP_DIR, "tattpro.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(RESOURCE_DIR, "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(APP_DIR, "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(APP_DIR, "static", "app_icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(APP_DIR, "tiem_xam_icon_v2.ico")

    # 5. Cấu hình cửa sổ ứng dụng Desktop Windows (Native App qua WebView2)
    window = webview.create_window(
        title=WINDOW_TITLE,
        url=app_url,
        width=1400,
        height=900,
        min_size=(1080, 700),
        confirm_close=False,
        text_select=True,
        easy_drag=False
    )

    def on_closed():
        try:
            server_thread.stop()
        except Exception:
            pass
        # Thoát dứt điểm toàn bộ tiến trình, giải phóng socket và handle sạch sẽ
        os._exit(0)

    window.events.closed += on_closed

    # Chạy luồng cập nhật icon góc trên thanh tiêu đề Windows
    if os.path.exists(icon_path):
        threading.Thread(target=apply_native_window_icon, args=(icon_path,), daemon=True).start()

    # Khởi chạy giao diện WebView (Edge Chromium WebView2)
    webview.start(debug=False, icon=icon_path if os.path.exists(icon_path) else None)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
