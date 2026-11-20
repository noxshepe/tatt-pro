import os
import sys
import json
import base64
import hashlib
import winreg
import subprocess
import threading
from datetime import datetime
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAlHzZbYO0OeH6u048mqOkZPRY8JjWCiHqZQUwV4vKRII=
-----END PUBLIC KEY-----"""

APP_SALT = "TattPro_V2_Commercial_Salt_2026_Protect"
REG_LICENSE_PATH = r"Software\TattPro\License"
REG_TRIAL_PATH = r"Software\TattPro\Trial"

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LICENSE_FILE_PATH = os.path.join(APP_DIR, "license.dat")

_HW_LOCK = threading.Lock()
_CACHED_HW_INFO = None

def get_registry_guid():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid).strip()
    except Exception:
        return "UNKNOWN_REG_GUID"

def _read_reg_val(val_name: str, reg_path: str = REG_LICENSE_PATH):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
            val, _ = winreg.QueryValueEx(k, val_name)
            return str(val).strip() if val else ""
    except Exception:
        return ""

def _write_reg_val(val_name: str, val_str: str, reg_path: str = REG_LICENSE_PATH):
    try:
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path)
        winreg.SetValueEx(k, val_name, 0, winreg.REG_SZ, str(val_str).strip())
        winreg.CloseKey(k)
        return True
    except Exception:
        return False

def _run_silent_cmd(cmd_args, timeout=6):
    """Thực thi lệnh hệ thống ẩn 100% không bao giờ hiện popup console"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0 # SW_HIDE
        res = subprocess.check_output(
            cmd_args,
            startupinfo=si,
            creationflags=0x08000000,
            text=True,
            timeout=timeout
        ).strip()
        return res
    except Exception:
        return ""

def get_baseboard_serial():
    # 1. Kiểm tra cache trong Registry để tốc độ 0.001ms
    cached = _read_reg_val("CachedBoard")
    if cached and cached != "UNKNOWN_BOARD_SERIAL":
        return cached

    # 2. Đọc từ hệ thống
    try:
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', '(Get-CimInstance Win32_BaseBoard).SerialNumber']
        res = _run_silent_cmd(cmd, timeout=6)
        if res:
            _write_reg_val("CachedBoard", res)
            return res
    except Exception:
        pass
    return "UNKNOWN_BOARD_SERIAL"

def get_disk_serial():
    # 1. Kiểm tra cache trong Registry để tốc độ 0.001ms
    cached = _read_reg_val("CachedDisk")
    if cached and cached != "UNKNOWN_DISK_SERIAL":
        return cached

    # 2. Ưu tiên ổ đĩa cố định (Fixed Drive) của hệ điều hành để tránh bị đổi khi cắm USB
    try:
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', 
               '(Get-CimInstance Win32_DiskDrive | Where-Object { $_.MediaType -like "*Fixed*" -or $_.MediaType -eq $null } | Select-Object -First 1).SerialNumber']
        res = _run_silent_cmd(cmd, timeout=6)
        if not res:
            cmd_fallback = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', '(Get-CimInstance Win32_DiskDrive)[0].SerialNumber']
            res = _run_silent_cmd(cmd_fallback, timeout=6)
        if res:
            _write_reg_val("CachedDisk", res)
            return res
    except Exception:
        pass
    return "UNKNOWN_DISK_SERIAL"

def generate_hardware_hashes():
    """Thu thập 3 yếu tố phần cứng và băm độc lập, có Registry Cache"""
    # Nếu đã có DisplayId trong Registry và các linh kiện hợp lệ, dùng ngay
    reg_id = _read_reg_val("DisplayId")
    reg_full = _read_reg_val("FullHash")
    reg_guid = get_registry_guid()
    cached_board = _read_reg_val("CachedBoard")
    cached_disk = _read_reg_val("CachedDisk")

    if reg_id and reg_full and cached_board and cached_disk:
        h_guid = hashlib.sha256(f"{reg_guid}_{APP_SALT}".encode()).hexdigest()[:12]
        h_board = hashlib.sha256(f"{cached_board}_{APP_SALT}".encode()).hexdigest()[:12]
        h_disk = hashlib.sha256(f"{cached_disk}_{APP_SALT}".encode()).hexdigest()[:12]
        return {
            "display_id": reg_id,
            "hash_guid": h_guid,
            "hash_board": h_board,
            "hash_disk": h_disk,
            "full_hash": reg_full
        }

    board = get_baseboard_serial()
    disk = get_disk_serial()

    h_guid = hashlib.sha256(f"{reg_guid}_{APP_SALT}".encode()).hexdigest()[:12]
    h_board = hashlib.sha256(f"{board}_{APP_SALT}".encode()).hexdigest()[:12]
    h_disk = hashlib.sha256(f"{disk}_{APP_SALT}".encode()).hexdigest()[:12]

    combined = f"{reg_guid}|{board}|{disk}|{APP_SALT}"
    comb_hash = hashlib.sha256(combined.encode()).hexdigest().upper()
    display_id = f"{comb_hash[:4]}-{comb_hash[4:8]}-{comb_hash[8:12]}-{comb_hash[12:16]}"

    # Lưu cache vào Registry để những lần khởi động sau chạy tức thì 0.001ms
    _write_reg_val("DisplayId", display_id)
    _write_reg_val("FullHash", comb_hash)

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
    return get_machine_info()["display_id"]

def verify_hardware_match(licensed_machine: str):
    """
    Kiểm tra mã máy trong license có khớp với máy hiện tại không (đa tầng khoan dung)
    """
    licensed_machine = licensed_machine.strip().upper()
    current_info = get_machine_info()

    # 1. Khớp hoàn toàn Display ID (XXXX-XXXX-XXXX-XXXX)
    if licensed_machine == current_info["display_id"]:
        return True, "Khớp mã máy 100%"

    # 2. Khớp Full Hash
    if licensed_machine == current_info["full_hash"]:
        return True, "Khớp mã máy 100%"

    # 3. Khớp với mã máy đã được kích hoạt thành công trên máy này lưu trong Registry
    saved_lic_machine = _read_reg_val("LicensedMachine")
    if saved_lic_machine and saved_lic_machine.upper() == licensed_machine:
        return True, "Khớp mã máy đã xác thực trong Registry"

    # 4. Khoan dung phần cứng nếu 2 trong 4 khối trùng nhau
    lic_parts = licensed_machine.split("-")
    cur_parts = current_info["display_id"].split("-")
    if len(lic_parts) == 4 and len(cur_parts) == 4:
        match_count = sum(1 for i in range(4) if lic_parts[i] == cur_parts[i])
        if match_count >= 2:
            return True, "Khớp phần cứng mức độ cao"

    return False, f"Mã bản quyền này được tạo cho máy khác ({licensed_machine}), không phải máy tính này ({current_info['display_id']})"

def verify_license_string(license_str: str):
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

        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
        public_key.verify(signature, payload_bytes)

        payload = json.loads(payload_bytes.decode('utf-8'))

        if payload.get("product") != "tattpro":
            return False, None, "Mã giấy phép không dành cho sản phẩm TattPro"

        is_hw_ok, hw_msg = verify_hardware_match(payload.get("machine", ""))
        if not is_hw_ok:
            return False, None, hw_msg

        return True, payload, "Bản quyền hợp lệ"

    except Exception as e:
        return False, None, f"Chữ ký bảo mật không hợp lệ hoặc đã bị chỉnh sửa ({str(e)})"

def save_license(license_str: str):
    """Lưu gói bản quyền vĩnh viễn vào CẢ file license.dat VÀ Windows Registry"""
    clean_key = license_str.strip()
    
    # 1. Lưu vào Windows Registry (bất tử, không bị mất khi update hay đổi thư mục)
    _write_reg_val("LicenseKey", clean_key)
    try:
        # Nếu có machine trong license, lưu lại
        _, payload, _ = verify_license_string(clean_key)
        if payload and payload.get("machine"):
            _write_reg_val("LicensedMachine", payload.get("machine"))
    except Exception:
        pass

    # 2. Lưu vào tất cả các vị trí file khả dụng
    candidate_paths = [
        LICENSE_FILE_PATH,
        os.path.join(APP_DIR, "license.dat"),
        os.path.join(os.path.dirname(APP_DIR), "license.dat"),
        os.path.join(APP_DIR, "_internal", "license.dat"),
        os.path.join(os.getcwd(), "license.dat")
    ]
    for p in candidate_paths:
        try:
            d = os.path.dirname(p)
            if os.path.exists(d):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(clean_key)
        except Exception:
            pass

def read_saved_license():
    """Đọc license từ các file hoặc từ Windows Registry (tự động phục hồi đa chiều)"""
    candidate_paths = [
        LICENSE_FILE_PATH,
        os.path.join(APP_DIR, "license.dat"),
        os.path.join(APP_DIR, "_internal", "license.dat"),
        os.path.join(os.path.dirname(APP_DIR), "license.dat"),
        os.path.join(os.getcwd(), "license.dat")
    ]
    
    # 1. Thử đọc từ file
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content.startswith("TATTPRO-LIC-"):
                    # Đồng bộ lại Registry nếu chưa có
                    if not _read_reg_val("LicenseKey"):
                        _write_reg_val("LicenseKey", content)
                    return content
            except Exception:
                pass

    # 2. Nếu file bị thiếu hoặc chuyển thư mục -> Lấy từ Windows Registry
    reg_key = _read_reg_val("LicenseKey")
    if reg_key and reg_key.startswith("TATTPRO-LIC-"):
        # Tự động ghi phục hồi file license.dat ngay lập tức
        try:
            with open(LICENSE_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(reg_key)
        except Exception:
            pass
        return reg_key

    return ""

def check_trial_status():
    """Kiểm tra trạng thái dùng thử 7 ngày"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_TRIAL_PATH) as key:
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
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_TRIAL_PATH)
            winreg.CloseKey(key)
            return False, "Máy này đã từng kích hoạt dùng thử trước đây!"
        except FileNotFoundError:
            pass

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_TRIAL_PATH)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        winreg.SetValueEx(key, "TrialStart", 0, winreg.REG_SZ, now_str)
        winreg.SetValueEx(key, "Machine", 0, winreg.REG_SZ, get_machine_id())
        winreg.CloseKey(key)
        return True, "Đã kích hoạt dùng thử 7 ngày thành công!"
    except Exception as e:
        return False, f"Lỗi kích hoạt dùng thử: {str(e)}"

def get_license_status():
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
