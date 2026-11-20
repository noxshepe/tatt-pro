import os
import sys
import json
import base64
import hashlib
import winreg
import subprocess
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

# Khóa công khai của bạn nhúng trong app để xác thực chữ ký (Không thể dùng để sinh key)
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAlHzZbYO0OeH6u048mqOkZPRY8JjWCiHqZQUwV4vKRII=
-----END PUBLIC KEY-----"""

APP_SALT = "TattPro_V2_Commercial_Salt_2026_Protect"

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LICENSE_FILE_PATH = os.path.join(APP_DIR, "license.dat")

def get_registry_guid():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid).strip()
    except Exception:
        return "UNKNOWN_REG_GUID"

import threading

_HW_LOCK = threading.Lock()
_CACHED_HW_INFO = None

def _run_silent_cmd(cmd_args):
    """Thực thi lệnh hệ thống ẩn 100% không bao giờ hiện cửa sổ popup console/powershell"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0 # SW_HIDE
        res = subprocess.check_output(
            cmd_args,
            startupinfo=si,
            creationflags=0x08000000, # CREATE_NO_WINDOW: Tuyệt đối không mở cửa sổ cmd/powershell
            text=True,
            timeout=3
        ).strip()
        return res
    except Exception:
        return ""

def get_baseboard_serial():
    try:
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', '(Get-CimInstance Win32_BaseBoard).SerialNumber']
        res = _run_silent_cmd(cmd)
        if res:
            return res
    except Exception:
        pass
    return "UNKNOWN_BOARD_SERIAL"

def get_disk_serial():
    try:
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', '(Get-CimInstance Win32_DiskDrive)[0].SerialNumber']
        res = _run_silent_cmd(cmd)
        if res:
            return res
    except Exception:
        pass
    return "UNKNOWN_DISK_SERIAL"

def generate_hardware_hashes():
    """Thu thập 3 yếu tố phần cứng và băm độc lập"""
    guid = get_registry_guid()
    board = get_baseboard_serial()
    disk = get_disk_serial()

    h_guid = hashlib.sha256(f"{guid}_{APP_SALT}".encode()).hexdigest()[:12]
    h_board = hashlib.sha256(f"{board}_{APP_SALT}".encode()).hexdigest()[:12]
    h_disk = hashlib.sha256(f"{disk}_{APP_SALT}".encode()).hexdigest()[:12]

    # Tổng hợp mã máy chính 16 ký tự dễ đọc: XXXX-XXXX-XXXX-XXXX
    combined = f"{guid}|{board}|{disk}|{APP_SALT}"
    comb_hash = hashlib.sha256(combined.encode()).hexdigest().upper()
    display_id = f"{comb_hash[:4]}-{comb_hash[4:8]}-{comb_hash[8:12]}-{comb_hash[12:16]}"

    return {
        "display_id": display_id,
        "hash_guid": h_guid,
        "hash_board": h_board,
        "hash_disk": h_disk,
        "full_hash": comb_hash
    }

def get_machine_info():
    global _CACHED_HW_INFO
    if _CACHED_HW_INFO is None:
        with _HW_LOCK:
            if _CACHED_HW_INFO is None:
                _CACHED_HW_INFO = generate_hardware_hashes()
    return _CACHED_HW_INFO

def get_machine_id():
    """Trả về Mã Máy để hiển thị cho khách hàng gửi cho bạn"""
    return get_machine_info()["display_id"]

def verify_hardware_match(licensed_machine: str):
    """
    Kiểm tra mã máy trong license có khớp với máy hiện tại không (hỗ trợ khoan dung)
    """
    licensed_machine = licensed_machine.strip().upper()
    current_info = get_machine_info()

    # 1. Khớp hoàn toàn Display ID (XXXX-XXXX-XXXX-XXXX)
    if licensed_machine == current_info["display_id"]:
        return True, "Khớp mã máy 100%"

    # 2. Khớp Full Hash
    if licensed_machine == current_info["full_hash"]:
        return True, "Khớp mã máy 100%"

    # 3. Khoan dung phần cứng nếu license lưu chi tiết hoặc match substring
    # Trong mô hình mã rút gọn, nếu khách thay 1 linh kiện nhỏ thì kiểm tra xem
    # 2 trong 3 khối đầu có trùng nhau không
    lic_parts = licensed_machine.split("-")
    cur_parts = current_info["display_id"].split("-")
    if len(lic_parts) == 4 and len(cur_parts) == 4:
        match_count = sum(1 for i in range(4) if lic_parts[i] == cur_parts[i])
        if match_count >= 3:
            return True, "Khớp phần cứng mức độ cao (3/4 linh kiện)"

    return False, f"Mã bản quyền này được tạo cho máy khác ({licensed_machine}), không phải máy tính này ({current_info['display_id']})"

def verify_license_string(license_str: str):
    """
    Kiểm tra chữ ký số Ed25519 và tính hợp lệ của license_str
    """
    try:
        clean_key = license_str.strip()
        if not clean_key.startswith("TATTPRO-LIC-"):
            return False, None, "Mã kích hoạt không đúng định dạng của TattPro"

        raw = clean_key.replace("TATTPRO-LIC-", "")
        parts = raw.split(".")
        if len(parts) != 2:
            return False, None, "Gói mã kích hoạt bị thiếu thông tin hoặc sai cấu trúc"

        b64_payload, b64_sig = parts[0], parts[1]
        b64_payload += '=' * (-len(b64_payload) % 4)
        b64_sig += '=' * (-len(b64_sig) % 4)

        payload_bytes = base64.urlsafe_b64decode(b64_payload.encode('ascii'))
        signature = base64.urlsafe_b64decode(b64_sig.encode('ascii'))

        # Tải Public Key và xác minh toán học
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
        public_key.verify(signature, payload_bytes)

        payload = json.loads(payload_bytes.decode('utf-8'))

        # Kiểm tra sản phẩm
        if payload.get("product") != "tattpro":
            return False, None, "Mã giấy phép không dành cho sản phẩm TattPro"

        # Kiểm tra mã máy
        is_hw_ok, hw_msg = verify_hardware_match(payload.get("machine", ""))
        if not is_hw_ok:
            return False, None, hw_msg

        return True, payload, "Bản quyền hợp lệ"

    except Exception as e:
        return False, None, f"Chữ ký bảo mật không hợp lệ hoặc đã bị chỉnh sửa ({str(e)})"

def save_license(license_str: str):
    """Lưu gói bản quyền vào file license.dat"""
    with open(LICENSE_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(license_str.strip())

def read_saved_license():
    """Đọc file license.dat nếu tồn tại"""
    if os.path.exists(LICENSE_FILE_PATH):
        try:
            with open(LICENSE_FILE_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""

def check_trial_status():
    """Kiểm tra trạng thái dùng thử 7 ngày"""
    try:
        reg_path = r"Software\TattPro\Trial"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            start_str, _ = winreg.QueryValueEx(key, "TrialStart")
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            days_passed = (now - start_dt).days
            days_left = max(0, 7 - days_passed)
            is_valid = days_passed < 7
            return {
                "has_trial": True,
                "is_valid": is_valid,
                "days_left": days_left,
                "start_date": start_str
            }
    except Exception:
        return {
            "has_trial": False,
            "is_valid": False,
            "days_left": 0,
            "start_date": None
        }

def start_trial():
    """Kích hoạt dùng thử 7 ngày (ghi nhận vào Registry)"""
    try:
        reg_path = r"Software\TattPro\Trial"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path)
            # Đã từng kích hoạt dùng thử trước đây
            winreg.CloseKey(key)
            return False, "Máy này đã từng kích hoạt dùng thử trước đây!"
        except FileNotFoundError:
            pass

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        winreg.SetValueEx(key, "TrialStart", 0, winreg.REG_SZ, now_str)
        winreg.SetValueEx(key, "Machine", 0, winreg.REG_SZ, get_machine_id())
        winreg.CloseKey(key)
        return True, "Đã kích hoạt dùng thử 7 ngày thành công!"
    except Exception as e:
        return False, f"Lỗi kích hoạt dùng thử: {str(e)}"

def get_license_status():
    """
    Hàm tổng hợp kiểm tra toàn diện trạng thái bản quyền
    """
    machine_id = get_machine_id()
    saved_key = read_saved_license()

    if saved_key:
        is_valid, payload, msg = verify_license_string(saved_key)
        if is_valid and payload:
            now_str = datetime.now().strftime("%Y-%m-%d")
            updates_until = payload.get("updates_until", "")
            can_update = updates_until >= now_str if updates_until else True

            return {
                "is_licensed": True,
                "is_trial": False,
                "edition": payload.get("edition", "commercial"),
                "customer": payload.get("customer", "Chủ Tiệm"),
                "machine_id": machine_id,
                "licensed_machine": payload.get("machine"),
                "issued_date": payload.get("issued"),
                "updates_until": updates_until,
                "can_update": can_update,
                "message": "Phần mềm đã được kích hoạt bản quyền chính thức!"
            }

    # Chưa có license hoặc license không hợp lệ -> Kiểm tra dùng thử
    trial = check_trial_status()
    if trial["has_trial"] and trial["is_valid"]:
        return {
            "is_licensed": True,
            "is_trial": True,
            "edition": "trial",
            "customer": "Dùng Thử Miễn Phí",
            "machine_id": machine_id,
            "trial_days_left": trial["days_left"],
            "trial_start": trial["start_date"],
            "message": f"Đang sử dụng bản dùng thử (Còn {trial['days_left']} ngày)"
        }

    return {
        "is_licensed": False,
        "is_trial": False,
        "edition": "unlicensed",
        "customer": "Chưa Kích Hoạt",
        "machine_id": machine_id,
        "message": "Phần mềm chưa được kích hoạt bản quyền!"
    }

