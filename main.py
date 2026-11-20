import os
import sys
import time
import threading
import json
import sqlite3
import uuid
import shutil
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, File, UploadFile, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def verify_owner_pin(pin: str) -> bool:
    if not pin:
        return False
    pin_str = str(pin).strip()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_config WHERE key = 'owner_pin'")
        row = cursor.fetchone()
        conn.close()
        saved = row["value"] if row else None
        if not saved:
            return pin_str == "6868"
        if saved == pin_str or saved == hash_password(pin_str):
            return True
        if saved == hash_password("6868") and pin_str == "6868":
            return True
        return False
    except Exception:
        return pin_str == "6868"

def add_audit_log(cursor, action: str, target_type: str, target_id: int, summary: str, old_value: str = "", new_value: str = "", performer: str = "Chủ Tiệm (Mã PIN)"):
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO audit_logs (action, target_type, target_id, summary, old_value, new_value, performer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (action, target_type, target_id, summary, str(old_value or ""), str(new_value or ""), performer, now_str))
    except Exception as e:
        print(f"Error logging audit: {e}")

ACTIVE_SESSIONS = {}

for stream in (sys.stdout, sys.stderr):
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Thiết lập đường dẫn tương thích cả chế độ chạy trực tiếp và đóng gói PyInstaller (.exe)
if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, '_MEIPASS', os.path.join(EXE_DIR, "_internal"))
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = EXE_DIR

DB_PATH = os.path.join(EXE_DIR, "tattoo_studio.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
    except Exception:
        pass
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Bảng Lịch hẹn / Khách hàng / Cọc / Thanh toán
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL,
        artist_name TEXT NOT NULL,
        service_desc TEXT,
        appointment_date TEXT NOT NULL,
        total_price REAL DEFAULT 0,
        deposit_amount REAL DEFAULT 0,
        deposit_status TEXT DEFAULT 'CHUA_COC', -- CHUA_COC, DA_COC
        deposit_date TEXT,
        payment_status TEXT DEFAULT 'CHUA_THANH_TOAN', -- CHUA_THANH_TOAN, MOT_PHAN, DA_TAT_TOAN
        amount_paid REAL DEFAULT 0,
        payment_method TEXT DEFAULT 'TIEN_MAT', -- TIEN_MAT, CHUYEN_KHOAN, THE
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng Kho hàng (Tồn kho vật tư)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        category TEXT NOT NULL, -- MỰC XĂM, KIM XĂM, VẬT TƯ TIÊU HAO, THIẾT BỊ
        unit TEXT NOT NULL,
        stock_quantity REAL DEFAULT 0,
        min_threshold REAL DEFAULT 5,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng Các Đợt Nhập Hàng & Chi Phí Vật Tư Cá Nhân
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_name TEXT DEFAULT '',
        payer_name TEXT DEFAULT 'QUỸ TIỆM', -- ANH BẮP, ANH HÍU, QUỸ TIỆM
        supplier_name TEXT NOT NULL,
        purchase_date TEXT NOT NULL,
        total_cost REAL DEFAULT 0,
        payment_status TEXT DEFAULT 'DA_THANH_TOAN', -- DA_THANH_TOAN, CON_NO
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    try:
        cursor.execute("ALTER TABLE purchase_orders ADD COLUMN batch_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE purchase_orders ADD COLUMN payer_name TEXT DEFAULT 'QUỸ TIỆM'")
    except sqlite3.OperationalError:
        pass

    # Bảng Chi tiết phiếu nhập
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        total_item_cost REAL NOT NULL,
        FOREIGN KEY (order_id) REFERENCES purchase_orders (id) ON DELETE CASCADE,
        FOREIGN KEY (item_id) REFERENCES inventory_items (id)
    )
    """)

    # Bảng Nhật ký khách xăm (Tattoo Log) & Chính sách bảo hành
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tattoo_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_phone TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        booking_id INTEGER,
        session_id INTEGER,
        tattoo_title TEXT NOT NULL,
        completed_date TEXT NOT NULL,
        base_touchup_price REAL DEFAULT 0,
        warranty_months INTEGER DEFAULT 5,
        overdue_penalty_rate REAL DEFAULT 0.20,
        status TEXT DEFAULT 'COMPLETED',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng Hồ sơ Khách hàng mở rộng (Health notes, style, sinh nhật)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS customer_profiles (
        phone TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        dob TEXT,
        health_notes TEXT,
        style_preference TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng Các đợt thanh toán (Payment Installments)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payment_installments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        session_id INTEGER,
        artist_name TEXT,
        installment_name TEXT NOT NULL, -- Ví dụ: Đợt 1 (Cọc), Đợt 2 (Xăm buổi 1), Đợt 3 (Hoàn thiện)
        amount REAL NOT NULL,
        payment_method TEXT DEFAULT 'CHUYEN_KHOAN', -- CHUYEN_KHOAN, TIEN_MAT, THE
        payment_date TEXT NOT NULL,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        staff_payout_status TEXT DEFAULT 'CHUA_CHI',
        staff_payout_date TEXT,
        staff_paid_to_owner_status TEXT DEFAULT 'CHUA_NOP',
        staff_paid_to_owner_date TEXT,
        staff_remit_type TEXT DEFAULT '30',
        FOREIGN KEY (booking_id) REFERENCES bookings (id) ON DELETE CASCADE
    )
    """)

    # Bảng Lịch Các Đợt Xăm (Tattoo Sessions / Stages)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tattoo_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL,
        artist_name TEXT NOT NULL,
        service_desc TEXT,
        session_number INTEGER DEFAULT 1,
        stage_name TEXT NOT NULL, -- Đợt 1: Đi khung nét viền, Đợt 2: Đánh bóng, Đợt 3: Vào màu...
        appointment_date TEXT NOT NULL,
        session_price REAL DEFAULT 0,
        payment_status TEXT DEFAULT 'CHUA_THANH_TOAN', -- DA_THANH_TOAN, CHUA_THANH_TOAN
        session_status TEXT DEFAULT 'CHO_XAM', -- CHO_XAM, DANG_XAM, HOAN_THANH
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (booking_id) REFERENCES bookings (id) ON DELETE CASCADE
    )
    """)

    # Bảng Nhân viên / Thợ xăm (Staff & Artists)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        phone TEXT,
        role TEXT DEFAULT 'Artist chính', -- Artist chính, Thợ phụ, Học viên
        commission_rate REAL DEFAULT 70.0, -- Tỷ lệ % thợ nhận (30% hoặc 70%)
        is_active BOOLEAN DEFAULT 1,
        avatar_url TEXT DEFAULT '',
        theme_color TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    try:
        cursor.execute("ALTER TABLE staff ADD COLUMN avatar_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE staff ADD COLUMN theme_color TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Bảng Danh mục hình xăm & Bảng giá chuẩn của Studio cho Nhân viên
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tattoo_services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,         -- Kín tay, Lưng, Lửng tay, Chân
        color_type TEXT NOT NULL,       -- Màu, Đen trắng
        price REAL NOT NULL,            -- 8000000, 7000000, etc.
        description TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Đảm bảo cột client_source trong bảng bookings
    try:
        cursor.execute("ALTER TABLE bookings ADD COLUMN client_source TEXT DEFAULT 'TIEM'")
    except sqlite3.OperationalError:
        pass

    # Đảm bảo cột staff_payout_status và staff_payout_date trong payment_installments
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN staff_payout_status TEXT DEFAULT 'CHUA_CHI'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN staff_payout_date TEXT")
    except sqlite3.OperationalError:
        pass
    # Đảm bảo các cột thợ thanh toán / nộp tiền cho chủ tiệm
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN staff_paid_to_owner_status TEXT DEFAULT 'CHUA_NOP'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN staff_paid_to_owner_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN staff_remit_type TEXT DEFAULT '30'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN commission_rate REAL DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN session_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN artist_name TEXT")
    except sqlite3.OperationalError:
        pass

    # Tự động đồng bộ session_id và artist_name cho các installment đã có từ các đợt xăm
    try:
        cursor.execute("""
        UPDATE payment_installments
        SET session_id = (
            SELECT s.id FROM tattoo_sessions s 
            WHERE s.booking_id = payment_installments.booking_id 
              AND s.stage_name = payment_installments.installment_name 
            LIMIT 1
        ),
        artist_name = COALESCE(
            (SELECT s.artist_name FROM tattoo_sessions s 
             WHERE s.booking_id = payment_installments.booking_id 
               AND s.stage_name = payment_installments.installment_name 
             LIMIT 1),
            (SELECT b.artist_name FROM bookings b 
             WHERE b.id = payment_installments.booking_id 
             LIMIT 1)
        )
        WHERE session_id IS NULL OR artist_name IS NULL
        """)
    except Exception:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # Bảng Tài Khoản Người Dùng & Phân Quyền (Admin & Thợ Xăm)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin', -- 'admin' hoặc 'staff'
        staff_id INTEGER DEFAULT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE SET NULL
    )
    """)

    # Tự động tạo tài khoản Chủ Tiệm (Admin) mặc định nếu chưa có
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO users (username, password_hash, full_name, role, is_active)
        VALUES ('admin', ?, 'Chủ Tiệm', 'admin', 1)
        """, (hash_password("123456"),))

    # Đảm bảo cột user_id trong bảng staff
    try:
        cursor.execute("ALTER TABLE staff ADD COLUMN user_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

    # Bảng Nhật Ký Biến Động Số Liệu & Chống Gian Lận (Audit Log)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        summary TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        performer TEXT DEFAULT 'Chủ Tiệm (Mã PIN)',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Khởi tạo Mã PIN bảo mật Chủ Tiệm mặc định (6868)
    cursor.execute("SELECT value FROM app_config WHERE key = 'owner_pin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO app_config (key, value) VALUES ('owner_pin', ?)", (hash_password("6868"),))

    # Bảng Lịch sử ứng tiền / Chi trả lương thợ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payout_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        artist_name TEXT NOT NULL,
        amount REAL NOT NULL,
        payout_type TEXT DEFAULT 'SETTLEMENT', -- ADVANCE hoặc SETTLEMENT
        note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng giao dịch thu trả nợ và ứng tiền tự nhập của thợ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS staff_debt_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        artist_name TEXT NOT NULL,
        transaction_type TEXT NOT NULL, -- STAFF_PAY_OWNER, OWNER_PAY_STAFF, ADD_DEBT, ADD_BONUS
        amount REAL NOT NULL,
        payment_method TEXT DEFAULT 'TIEN_MAT',
        note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Bảng Ghi nhận các lần thợ nộp tiền cho chủ tiệm theo từng đợt
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS staff_remit_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        installment_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        payment_method TEXT DEFAULT 'TIEN_MAT', -- TIEN_MAT, CHUYEN_KHOAN
        payment_date TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (installment_id) REFERENCES payment_installments (id) ON DELETE CASCADE
    )
    """)

    # Đảm bảo các cột tự nhập số tiền đợt xăm
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN custom_remit_to_owner REAL DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE payment_installments ADD COLUMN custom_staff_share REAL DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

    # Đảm bảo cột wallet_balance trong staff
    try:
        cursor.execute("ALTER TABLE staff ADD COLUMN wallet_balance REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE tattoo_sessions ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    cursor.execute("SELECT value FROM app_config WHERE key = 'is_clean_db'")
    clean_row = cursor.fetchone()
    is_clean = (clean_row is not None and clean_row[0] == '1')

    # Tự động đồng bộ các khoản đã trả hoặc cọc sang bảng installments nếu chưa có (chỉ khi không phải clean_db)
    if not is_clean:
        cursor.execute("SELECT COUNT(*) FROM payment_installments")
        if cursor.fetchone()[0] == 0:
            sync_initial_installments(cursor)

        # Tự động khởi tạo các đợt xăm mẫu nếu chưa có
        cursor.execute("SELECT COUNT(*) FROM tattoo_sessions")
        if cursor.fetchone()[0] == 0:
            sync_initial_sessions(cursor)

    # Tự động khởi tạo danh sách nhân viên nếu chưa có
    cursor.execute("SELECT COUNT(*) FROM staff")
    if cursor.fetchone()[0] == 0:
        sync_initial_staff(cursor)

    # Tự động khởi tạo bảng giá hình xăm chuẩn nếu chưa có
    cursor.execute("SELECT COUNT(*) FROM tattoo_services")
    if cursor.fetchone()[0] == 0:
        sync_initial_services(cursor)

    if not is_clean:
        # Tự động khởi tạo vật tư nếu chưa có
        cursor.execute("SELECT COUNT(*) FROM inventory_items")
        if cursor.fetchone()[0] == 0:
            items = [
                ("Mực Dynamic Black (240ml)", "MỰC XĂM", "chai", 15, 3),
                ("Mực Eternal Solid Red (30ml)", "MỰC XĂM", "chai", 8, 2),
                ("Kim đạn Cheyenne 3RL", "KIM XĂM", "hộp (20 kim)", 12, 5),
                ("Kim đạn Kwadron 7RM", "KIM XĂM", "hộp (20 kim)", 10, 4),
                ("Găng tay đen Nitrile Nitrylex", "VẬT TƯ TIÊU HAO", "hộp", 25, 6),
                ("Màng dán SecondSkin Dermalize", "VẬT TƯ TIÊU HAO", "cuộn", 14, 3),
                ("Vaseline TKTX dưỡng da", "VẬT TƯ TIÊU HAO", "hũ", 18, 5),
                ("Cồn sát khuẩn 70 độ (500ml)", "VẬT TƯ TIÊU HAO", "chai", 30, 8),
            ]
            cursor.executemany("""
            INSERT INTO inventory_items (item_name, category, unit, stock_quantity, min_threshold)
            VALUES (?, ?, ?, ?, ?)
            """, items)

        # Kiểm tra xem đã từng seed dữ liệu lần đầu chưa (chống việc tự ý chèn lại dữ liệu khi người dùng đã xóa trắng)
        cursor.execute("SELECT value FROM app_config WHERE key = 'has_seeded'")
        seeded_row = cursor.fetchone()
        if not seeded_row:
            cursor.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES ('has_seeded', '1')")
            cursor.execute("SELECT COUNT(*) FROM bookings")
            if cursor.fetchone()[0] == 0:
                seed_sample_data(cursor)
        else:
            # Tự động dọn sạch các bản ghi thanh toán hoặc đợt xăm mồ côi nếu đơn chính đã bị xóa
            cursor.execute("DELETE FROM payment_installments WHERE booking_id NOT IN (SELECT id FROM bookings) AND (session_id IS NULL OR session_id NOT IN (SELECT id FROM tattoo_sessions))")
            cursor.execute("DELETE FROM tattoo_logs WHERE booking_id NOT IN (SELECT id FROM bookings)")
            cursor.execute("DELETE FROM tattoo_sessions WHERE booking_id NOT IN (SELECT id FROM bookings)")

    conn.commit()
    conn.close()

def sync_initial_installments(cursor):
    today = date.today().strftime("%Y-%m-%d")
    cursor.execute("SELECT id, deposit_amount, deposit_status, amount_paid, payment_method, notes FROM bookings")
    bookings = cursor.fetchall()
    for b in bookings:
        b_id, dep_amt, dep_status, amt_paid, method, notes = b
        if dep_status == "DA_COC" and dep_amt > 0:
            cursor.execute("""
            INSERT INTO payment_installments (booking_id, installment_name, amount, payment_method, payment_date, notes)
            VALUES (?, 'Đợt 1 (Tiền cọc)', ?, ?, ?, 'Cọc giữ lịch')
            """, (b_id, dep_amt, method or 'CHUYEN_KHOAN', today))
            
            # Nếu khách đã trả nhiều hơn tiền cọc (ví dụ buổi 1 trả thêm)
            remaining_paid = amt_paid - dep_amt
            if remaining_paid > 0:
                cursor.execute("""
                INSERT INTO payment_installments (booking_id, installment_name, amount, payment_method, payment_date, notes)
                VALUES (?, 'Đợt 2 (Xăm buổi 1)', ?, ?, ?, 'Thanh toán buổi 1')
                """, (b_id, remaining_paid, method or 'CHUYEN_KHOAN', today))
        elif amt_paid > 0:
            cursor.execute("""
            INSERT INTO payment_installments (booking_id, installment_name, amount, payment_method, payment_date, notes)
            VALUES (?, 'Đợt 1', ?, ?, ?, 'Thanh toán trực tiếp')
            """, (b_id, amt_paid, method or 'TIEN_MAT', today))

def sync_initial_sessions(cursor):
    today = date.today().strftime("%Y-%m-%d")
    cursor.execute("SELECT id, customer_name, customer_phone, artist_name, service_desc, total_price FROM bookings")
    bookings = cursor.fetchall()
    
    sample_sessions = [
        # Khách Hải Nam - Kín lưng Rồng Châu Á (4 đợt hoàn thiện)
        (1, "Nguyễn Hải Nam", "0912345678", "Artist Long Rồng", "Kín lưng Rồng Châu Á", 1, "Đợt 1: Đi khung nét viền lưng", f"{today} 10:00", 3000000, "DA_THANH_TOAN", "HOAN_THANH", "Đã đi nét viền xong toàn bộ lưng"),
        (1, "Nguyễn Hải Nam", "0912345678", "Artist Long Rồng", "Kín lưng Rồng Châu Á", 2, "Đợt 2: Tả khối & đánh bóng mây rồng", f"{today} 14:00", 3000000, "DA_THANH_TOAN", "HOAN_THANH", "Đã xong khối mây và vảy chính"),
        (1, "Nguyễn Hải Nam", "0912345678", "Artist Long Rồng", "Kín lưng Rồng Châu Á", 3, "Đợt 3: Vào màu vảy & đầu rồng", "2026-11-20 10:00", 3000000, "CHUA_THANH_TOAN", "CHO_XAM", "Buổi vào màu xanh ngọc"),
        (1, "Nguyễn Hải Nam", "0912345678", "Artist Long Rồng", "Kín lưng Rồng Châu Á", 4, "Đợt 4: Hoàn thiện chi tiết mắt & hoa văn", "2026-11-28 10:00", 3000000, "CHUA_THANH_TOAN", "CHO_XAM", "Buổi cuối cùng tổng kết"),

        # Khách Phương Thảo - Hoa mẫu đơn (2 đợt hoàn thiện)
        (2, "Trần Phương Thảo", "0987654321", "Artist Thư Blackwork", "Hoa mẫu đơn cánh tay", 1, "Đợt 1: Đi nét khung hoa & lá", f"{today} 14:30", 2000000, "DA_THANH_TOAN", "HOAN_THANH", "Khung nét hoa mẫu đơn"),
        (2, "Trần Phương Thảo", "0987654321", "Artist Thư Blackwork", "Hoa mẫu đơn cánh tay", 2, "Đợt 2: Đánh bóng & vào màu hoàn thiện", f"{today} 16:30", 1500000, "DA_THANH_TOAN", "HOAN_THANH", "Hoàn thành 100%"),

        # Khách Quốc Hưng - Chân dung cún (2 đợt)
        (3, "Lê Quốc Hưng", "0905112233", "Artist Kiên Realism", "Chân dung cún cưng", 1, "Đợt 1: Lên nét & dựng hình tả chi tiết", f"{today} 16:00", 2500000, "CHUA_THANH_TOAN", "CHO_XAM", "Chờ khách qua cọc & làm"),
        (3, "Lê Quốc Hưng", "0905112233", "Artist Kiên Realism", "Chân dung cún cưng", 2, "Đợt 2: Tả lông chi tiết tả thực & râu", "2026-11-22 14:00", 2000000, "CHUA_THANH_TOAN", "CHO_XAM", "Hoàn thiện chân dung cún"),

        # Khách Minh Quân - Old school hổ ngực (2 đợt)
        (4, "Đỗ Minh Quân", "0933445566", "Artist Long Rồng", "Old school hổ ngực", 1, "Đợt 1: Đi khung đầu hổ & cọc", f"{today} 18:30", 2500000, "DA_THANH_TOAN", "DANG_XAM", "Đang đi khung nét dày"),
        (4, "Đỗ Minh Quân", "0933445566", "Artist Long Rồng", "Old school hổ ngực", 2, "Đợt 2: Đổ màu truyền thống & viền", "2026-11-21 15:00", 2500000, "CHUA_THANH_TOAN", "CHO_XAM", "Vào màu vàng cam")
    ]
    cursor.executemany("""
    INSERT INTO tattoo_sessions (
        booking_id, customer_name, customer_phone, artist_name, service_desc,
        session_number, stage_name, appointment_date, session_price,
        payment_status, session_status, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_sessions)

def sync_initial_staff(cursor):
    sample_staff = [
        ("Chủ Tiệm", "0901112233", "Chủ tiệm", 100.0),
        ("Nhân Viên", "0902223344", "Nhân viên xăm", 70.0)
    ]
    cursor.executemany("""
    INSERT OR IGNORE INTO staff (name, phone, role, commission_rate)
    VALUES (?, ?, ?, ?)
    """, sample_staff)

def sync_initial_services(cursor):
    # Danh sách hình của nhân viên theo định mức studio (Kín tay, Lưng, Lửng tay, Chân - Màu / Đen trắng)
    sample_services = [
        ("Kín tay", "Màu", 8000000.0, "Kín tay vào màu trọn gói"),
        ("Kín tay", "Đen trắng", 7000000.0, "Kín tay đen trắng / tả thực"),
        ("Lưng", "Màu", 16000000.0, "Kín lưng vào màu phong cách Nhật cổ / Á - Âu"),
        ("Lưng", "Đen trắng", 14000000.0, "Kín lưng đen trắng / shading tả thực"),
        ("Lửng tay", "Màu", 7000000.0, "Lửng tay bắp / cẳng tay vào màu"),
        ("Lửng tay", "Đen trắng", 6000000.0, "Lửng tay bắp / cẳng tay đen trắng"),
        ("Chân", "Màu", 19000000.0, "Kín chân vào màu trọn gói"),
        ("Chân", "Đen trắng", 17000000.0, "Kín chân đen trắng / tả thực"),
    ]
    cursor.executemany("""
    INSERT INTO tattoo_services (category, color_type, price, description)
    VALUES (?, ?, ?, ?)
    """, sample_services)

def seed_sample_data(cursor):
    today = date.today().strftime("%Y-%m-%d")
    
    # Seed vật tư
    items = [
        ("Mực Dynamic Black (240ml)", "MỰC XĂM", "chai", 15, 3),
        ("Mực Eternal Solid Red (30ml)", "MỰC XĂM", "chai", 8, 2),
        ("Kim đạn Cheyenne 3RL", "KIM XĂM", "hộp (20 kim)", 12, 5),
        ("Kim đạn Kwadron 7RM", "KIM XĂM", "hộp (20 kim)", 10, 4),
        ("Găng tay đen Nitrile Nitrylex", "VẬT TƯ TIÊU HAO", "hộp", 25, 6),
        ("Màng dán SecondSkin Dermalize", "VẬT TƯ TIÊU HAO", "cuộn", 14, 3),
        ("Vaseline TKTX dưỡng da", "VẬT TƯ TIÊU HAO", "hũ", 18, 5),
        ("Cồn sát khuẩn 70 độ (500ml)", "VẬT TƯ TIÊU HAO", "chai", 30, 8),
    ]
    cursor.executemany("""
    INSERT INTO inventory_items (item_name, category, unit, stock_quantity, min_threshold)
    VALUES (?, ?, ?, ?, ?)
    """, items)

    # Seed phiếu nhập hàng mẫu
    cursor.execute("""
    INSERT INTO purchase_orders (supplier_name, purchase_date, total_cost, payment_status, notes)
    VALUES ('Đại lý Thiết bị Xăm Sài Gòn', ?, 6250000, 'DA_THANH_TOAN', 'Nhập đợt kim và găng tay tháng')
    """, (today,))
    order_id = cursor.lastrowid

    # Seed chi tiết phiếu nhập
    cursor.execute("""
    INSERT INTO purchase_order_items (order_id, item_id, item_name, quantity, unit_price, total_item_cost)
    VALUES (?, 1, 'Mực Dynamic Black (240ml)', 5, 450000, 2250000),
           (?, 3, 'Kim đạn Cheyenne 3RL', 10, 250000, 2500000),
           (?, 5, 'Găng tay đen Nitrile Nitrylex', 10, 150000, 1500000)
    """, (order_id, order_id, order_id))

    # Seed khách hàng & cọc & thanh toán mẫu
    sample_bookings = [
        ("Nguyễn Hải Nam", "0912345678", "Artist Long Rồng", "Kín lưng Rồng Châu Á", f"{today} 10:00", 12000000, 3000000, "DA_COC", f"{today}", "MOT_PHAN", 6000000, "CHUYEN_KHOAN", "Buổi 1 khung xong, còn nợ 6tr"),
        ("Trần Phương Thảo", "0987654321", "Artist Thư Blackwork", "Hoa mẫu đơn cánh tay", f"{today} 14:30", 3500000, 500000, "DA_COC", f"{today}", "DA_TAT_TOAN", 3500000, "CHUYEN_KHOAN", "Đã thanh toán đủ cả cọc lẫn số còn lại"),
        ("Lê Quốc Hưng", "0905112233", "Artist Kiên Realism", "Chân dung cún cưng", f"{today} 16:00", 4500000, 0, "CHUA_COC", None, "CHUA_THANH_TOAN", 0, "TIEN_MAT", "Hẹn chiều nay cọc hoặc qua tiệm cọc trực tiếp"),
        ("Đỗ Minh Quân", "0933445566", "Artist Long Rồng", "Old school hổ ngực", f"{today} 18:30", 5000000, 1000000, "DA_COC", f"{today}", "CHUA_THANH_TOAN", 1000000, "CHUYEN_KHOAN", "Đã cọc 1tr, còn thiếu 4tr sau khi xăm"),
        ("Vũ Hoàng My", "0977889900", "Artist Thư Blackwork", "Mini tattoo cổ tay", f"{today} 19:30", 800000, 0, "CHUA_COC", None, "CHUA_THANH_TOAN", 0, "TIEN_MAT", "Khách vãng lai book lịch gấp")
    ]
    cursor.executemany("""
    INSERT INTO bookings (customer_name, customer_phone, artist_name, service_desc, appointment_date, total_price, deposit_amount, deposit_status, deposit_date, payment_status, amount_paid, payment_method, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_bookings)

app = FastAPI(title="Tattoo Studio Manager")

import license_manager
from fastapi import Request

# ==========================================
# HỆ THỐNG KIỂM TRA BẢN QUYỀN CHỮ KÝ SỐ ED25519
# ==========================================
@app.middleware("http")
async def license_check_middleware(request: Request, call_next):
    path = request.url.path
    whitelist_prefixes = [
        "/static",
        "/api/license",
        "/api/system",
        "/favicon.ico"
    ]
    if path == "/" or any(path.startswith(prefix) for prefix in whitelist_prefixes):
        return await call_next(request)

    # Kiểm tra bản quyền đối với mọi route nghiệp vụ
    try:
        status = license_manager.get_license_status()
        if not status["is_licensed"]:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=402,
                content={
                    "detail": "Phần mềm chưa kích hoạt bản quyền!",
                    "error_code": "LICENSE_REQUIRED",
                    "machine_id": status.get("machine_id", "")
                }
            )
    except Exception as e:
        print("Lỗi kiểm tra bản quyền trong middleware:", e)

    return await call_next(request)

class LicenseActivationRequest(BaseModel):
    license_key: str

@app.get("/api/license/status")
def api_get_license_status():
    return license_manager.get_license_status()

@app.post("/api/license/activate")
def api_activate_license(data: LicenseActivationRequest):
    key = data.license_key.strip()
    is_valid, payload, msg = license_manager.verify_license_string(key)
    if not is_valid:
        raise HTTPException(status_code=400, detail=msg)
    
    license_manager.save_license(key)
    return {
        "success": True,
        "message": f"Kích hoạt bản quyền thành công cho {payload.get('customer')}!",
        "license": payload
    }

@app.post("/api/license/trial")
def api_activate_trial():
    ok, msg = license_manager.start_trial()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


# Models Pydantic
class BookingCreate(BaseModel):
    customer_name: str
    customer_phone: Optional[str] = ""
    artist_name: str
    service_desc: Optional[str] = ""
    appointment_date: str
    total_price: float = 0
    deposit_amount: float = 0
    deposit_status: str = "CHUA_COC" # CHUA_COC, DA_COC
    deposit_date: Optional[str] = None
    payment_status: str = "CHUA_THANH_TOAN" # CHUA_THANH_TOAN, MOT_PHAN, DA_TAT_TOAN
    amount_paid: float = 0
    payment_method: str = "CHUYEN_KHOAN"
    notes: Optional[str] = ""
    client_source: Optional[str] = "TIEM" # TIEM (Tiệm tự chốt & ăn tất 100%), NHAN_VIEN (70% nhân viên - 30% tiệm)

class DepositUpdate(BaseModel):
    deposit_status: str
    deposit_amount: float
    deposit_date: Optional[str] = None

class PaymentUpdate(BaseModel):
    payment_status: str
    amount_paid: float
    payment_method: str
    notes: Optional[str] = None

class InstallmentCreate(BaseModel):
    installment_name: str
    amount: float
    payment_method: str = "CHUYEN_KHOAN"
    payment_date: Optional[str] = None
    notes: Optional[str] = ""

class TattooSessionCreate(BaseModel):
    booking_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    service_desc: Optional[str] = None
    session_number: int = 1
    stage_name: str
    appointment_date: str
    session_price: float = 0
    payment_status: str = "CHUA_THANH_TOAN"
    session_status: str = "CHO_XAM"
    notes: Optional[str] = ""
    artist_name: Optional[str] = None

class SessionPaymentUpdate(BaseModel):
    payment_status: str
    payment_method: str = "CHUYEN_KHOAN"

class SessionStatusUpdate(BaseModel):
    session_status: str

class NoteUpdate(BaseModel):
    notes: str

class PinVerifyRequest(BaseModel):
    pin: str

class PinChangeRequest(BaseModel):
    current_pin: str
    new_pin: str

class StaffTransactionCreate(BaseModel):
    artist_name: str
    transaction_type: str  # 'STAFF_PAY_OWNER', 'OWNER_PAY_STAFF', 'ADD_DEBT', 'ADD_BONUS'
    amount: float
    payment_method: Optional[str] = "TIEN_MAT"
    note: Optional[str] = ""

class CustomInstallmentAmountUpdate(BaseModel):
    custom_remit_to_owner: Optional[float] = None
    custom_staff_share: Optional[float] = None

class InstallmentRemitPaymentCreate(BaseModel):
    amount: float
    payment_method: Optional[str] = "TIEN_MAT"
    payment_date: Optional[str] = None
    note: Optional[str] = ""

class InstallmentSplitRatioUpdate(BaseModel):
    commission_rate: Optional[float] = None # % thợ nhận (40, 50, 60, 70, 80...)
    staff_remit_type: Optional[str] = None # "PERCENT" hoặc "100"

class ApplyUpdateRequest(BaseModel):
    download_url: str
    latest_version: Optional[str] = None

class UpdateUrlConfig(BaseModel):
    update_url: str

class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: str = "staff" # admin hoặc staff
    staff_id: Optional[int] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    staff_id: Optional[int] = None
    is_active: Optional[int] = None

class StaffCreate(BaseModel):
    name: str
    phone: Optional[str] = ""
    role: str = "Artist"
    commission_rate: Optional[float] = 70.0
    is_active: bool = True
    avatar_url: Optional[str] = ""
    theme_color: Optional[str] = ""
    username: Optional[str] = None
    password: Optional[str] = None

class StaffUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    commission_rate: Optional[float] = None
    is_active: Optional[bool] = None
    avatar_url: Optional[str] = None
    theme_color: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class ServiceCreate(BaseModel):
    category: str
    color_type: str
    price: float
    description: Optional[str] = ""

class ServiceUpdate(BaseModel):
    category: Optional[str] = None
    color_type: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None

class InventoryItemCreate(BaseModel):
    item_name: str
    category: str
    unit: str
    stock_quantity: float = 0
    min_threshold: float = 5

class PurchaseOrderItem(BaseModel):
    item_id: Optional[int] = None
    item_name: Optional[str] = None
    quantity: float = 1
    unit_price: float = 0

class PurchaseOrderCreate(BaseModel):
    batch_name: Optional[str] = ""
    payer_name: Optional[str] = "QUỸ TIỆM"
    supplier_name: Optional[str] = "Tiệm Đồ Xăm"
    purchase_date: str
    payment_status: str = "DA_THANH_TOAN"
    notes: Optional[str] = ""
    items: List[PurchaseOrderItem]

class BookingUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    artist_name: Optional[str] = None
    service_desc: Optional[str] = None
    appointment_date: Optional[str] = None
    total_price: Optional[float] = None
    deposit_amount: Optional[float] = None
    deposit_status: Optional[str] = None
    notes: Optional[str] = None
    client_source: Optional[str] = None

class TattooSessionUpdate(BaseModel):
    session_number: Optional[int] = None
    stage_name: Optional[str] = None
    appointment_date: Optional[str] = None
    session_price: Optional[float] = None
    payment_status: Optional[str] = None
    session_status: Optional[str] = None
    notes: Optional[str] = None
    artist_name: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    service_desc: Optional[str] = None

class SessionReorderRequest(BaseModel):
    session_id: int
    target_artist: Optional[str] = None
    target_date: Optional[str] = None
    ordered_ids: Optional[List[int]] = None

class InventoryItemUpdate(BaseModel):
    item_name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    stock_quantity: Optional[float] = None
    min_threshold: Optional[float] = None

class InstallmentUpdate(BaseModel):
    installment_name: Optional[str] = None
    amount: Optional[float] = None
    payment_method: Optional[str] = None
    payment_date: Optional[str] = None
    notes: Optional[str] = None

class CustomerCreate(BaseModel):
    phone: str
    name: str
    dob: Optional[str] = ""
    health_notes: Optional[str] = ""
    style_preference: Optional[str] = ""
    fav_artist: Optional[str] = ""
    notes: Optional[str] = ""

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None
    health_notes: Optional[str] = None
    style_preference: Optional[str] = None
    fav_artist: Optional[str] = None
    notes: Optional[str] = None

# API BOOKINGS
@app.get("/api/bookings")
def get_bookings(deposit_status: Optional[str] = None, payment_status: Optional[str] = None, search: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT * FROM bookings WHERE 1=1"
    params = []
    
    if deposit_status:
        query += " AND deposit_status = ?"
        params.append(deposit_status)
    if payment_status:
        query += " AND payment_status = ?"
        params.append(payment_status)
    if search:
        query += " AND (customer_name LIKE ? OR customer_phone LIKE ? OR artist_name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        
    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/bookings")
def create_booking(data: BookingCreate):
    conn = get_db()
    cursor = conn.cursor()

    # Tự động lưu thợ vào danh sách nhân viên nếu là tên mới do chủ nhập
    if data.artist_name and data.artist_name.strip():
        art = data.artist_name.strip()
        cursor.execute("SELECT id FROM staff WHERE name = ?", (art,))
        if not cursor.fetchone():
            cursor.execute("INSERT OR IGNORE INTO staff (name, role, is_active) VALUES (?, 'Thợ xăm', 1)", (art,))
    
    # Tự động đồng bộ số tiền đã trả ban đầu
    amount_paid = data.amount_paid
    if data.deposit_status == "DA_COC" and amount_paid < data.deposit_amount:
        amount_paid = data.deposit_amount
        
    client_source = data.client_source if data.client_source in ["TIEM", "NHAN_VIEN"] else "TIEM"

    cursor.execute("""
    INSERT INTO bookings (
        customer_name, customer_phone, artist_name, service_desc,
        appointment_date, total_price, deposit_amount, deposit_status,
        deposit_date, payment_status, amount_paid, payment_method, notes, client_source
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.customer_name, data.customer_phone, data.artist_name, data.service_desc,
        data.appointment_date, data.total_price, data.deposit_amount, data.deposit_status,
        data.deposit_date or (datetime.now().strftime("%Y-%m-%d") if data.deposit_status == "DA_COC" else None),
        data.payment_status, amount_paid, data.payment_method, data.notes, client_source
    ))
    booking_id = cursor.lastrowid

    # Nếu có tiền nộp ngay (cọc hoặc thanh toán), tự động tạo đợt thu đầu tiên
    if amount_paid > 0:
        cursor.execute("""
        INSERT INTO payment_installments (
            booking_id, installment_name, amount, payment_method, payment_date, notes, staff_payout_status
        ) VALUES (?, ?, ?, ?, ?, ?, 'CHUA_CHI')
        """, (
            booking_id,
            "Đợt 1 (Cọc)" if data.deposit_status == "DA_COC" else "Thanh toán đợt 1",
            amount_paid,
            data.payment_method,
            datetime.now().strftime("%Y-%m-%d"),
            "Khoản thu ban đầu khi tạo lịch"
        ))

    conn.commit()
    conn.close()
    return {"message": "Tạo lịch hẹn thành công", "id": booking_id}

@app.put("/api/bookings/{booking_id}/deposit")
def update_deposit(booking_id: int, data: DepositUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()
    if not booking:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch")
    
    dep_date = data.deposit_date or (datetime.now().strftime("%Y-%m-%d") if data.deposit_status == "DA_COC" else None)
    dep_amount = float(data.deposit_amount or 0)

    # 1. Đồng bộ sang payment_installments cho khoản Cọc
    cursor.execute("SELECT id FROM payment_installments WHERE booking_id = ? AND installment_name LIKE '%Cọc%'", (booking_id,))
    inst_row = cursor.fetchone()
    
    if data.deposit_status == "DA_COC" and dep_amount > 0:
        if inst_row:
            cursor.execute("""
            UPDATE payment_installments 
            SET amount = ?, payment_date = COALESCE(?, payment_date)
            WHERE id = ?
            """, (dep_amount, dep_date, inst_row["id"]))
        else:
            cursor.execute("""
            INSERT INTO payment_installments (booking_id, installment_name, amount, payment_method, payment_date, notes, staff_payout_status)
            VALUES (?, 'Đợt 1 (Cọc)', ?, 'CHUYEN_KHOAN', ?, 'Cập nhật tiền cọc', 'CHUA_CHI')
            """, (booking_id, dep_amount, dep_date))
    elif data.deposit_status == "CHUA_COC" or dep_amount <= 0:
        if inst_row:
            cursor.execute("DELETE FROM payment_installments WHERE id = ?", (inst_row["id"],))

    # 2. Tính lại chính xác tổng tiền thực tế từ tất cả các đợt thanh toán (installments)
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_amount_paid = cursor.fetchone()[0]

    # 3. Xác định trạng thái thanh toán
    tot_price = float(booking["total_price"] or 0)
    if tot_price > 0 and new_amount_paid >= tot_price:
        new_payment_status = "DA_TAT_TOAN"
    elif new_amount_paid > 0:
        new_payment_status = "MOT_PHAN"
    else:
        new_payment_status = "CHUA_THANH_TOAN"

    cursor.execute("""
    UPDATE bookings SET 
        deposit_status = ?,
        deposit_amount = ?,
        deposit_date = ?,
        amount_paid = ?,
        payment_status = ?
    WHERE id = ?
    """, (data.deposit_status, dep_amount, dep_date, new_amount_paid, new_payment_status, booking_id))
    
    conn.commit()
    conn.close()
    return {"message": "Cập nhật cọc thành công", "amount_paid": new_amount_paid, "payment_status": new_payment_status}

@app.put("/api/bookings/{booking_id}/payment")
def update_payment(booking_id: int, data: PaymentUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE bookings SET
        payment_status = ?,
        amount_paid = ?,
        payment_method = ?,
        notes = COALESCE(?, notes)
    WHERE id = ?
    """, (data.payment_status, data.amount_paid, data.payment_method, data.notes, booking_id))
    conn.commit()
    conn.close()
    return {"message": "Cập nhật thanh toán thành công"}

# API CÁC ĐỢT THANH TOÁN (INSTALLMENTS)
@app.get("/api/bookings/{booking_id}/installments")
def get_booking_installments(booking_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM payment_installments 
    WHERE booking_id = ? 
    ORDER BY id ASC
    """, (booking_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/bookings/{booking_id}/installments")
def add_booking_installment(booking_id: int, data: InstallmentCreate):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()
    if not booking:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch hẹn")

    p_date = data.payment_date or datetime.now().strftime("%Y-%m-%d %H:%M")

    # Thêm đợt thanh toán mới
    cursor.execute("""
    INSERT INTO payment_installments (
        booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, notes, staff_payout_status
    )
    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'CHUA_CHI')
    """, (booking_id, booking["artist_name"], data.installment_name, data.amount, data.payment_method, p_date, data.notes))

    # Tính lại tổng số tiền đã trả từ tất cả các đợt
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_total_paid = cursor.fetchone()[0]

    # Cập nhật trạng thái thanh toán của booking
    total_price = booking["total_price"]
    if new_total_paid >= total_price and total_price > 0:
        new_status = "DA_TAT_TOAN"
    elif new_total_paid > 0:
        new_status = "MOT_PHAN"
    else:
        new_status = "CHUA_THANH_TOAN"

    cursor.execute("""
    UPDATE bookings 
    SET amount_paid = ?, payment_status = ?, payment_method = ?
    WHERE id = ?
    """, (new_total_paid, new_status, data.payment_method, booking_id))

    conn.commit()
    conn.close()
    return {
        "message": "Ghi nhận đợt thanh toán thành công",
        "new_total_paid": new_total_paid,
        "payment_status": new_status
    }

@app.delete("/api/installments/{installment_id}")
def delete_installment(installment_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pi.*, b.customer_name 
        FROM payment_installments pi 
        LEFT JOIN bookings b ON pi.booking_id = b.id 
        WHERE pi.id = ?
    """, (installment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    booking_id = row["booking_id"]
    cust_name = row["customer_name"] or "Không rõ"
    inst_name = row["installment_name"] or "Đợt thu"
    del_amt = float(row["amount"] or 0)
    artist = row["artist_name"] or ""

    # Ghi nhận Audit Log chống gian lận
    add_audit_log(
        cursor,
        action="DELETE_INSTALLMENT",
        target_type="installment",
        target_id=installment_id,
        summary=f"Xóa đợt thu '{inst_name}' của khách '{cust_name}' (Thợ: {artist}, Số tiền: {del_amt:,.0f}đ)",
        old_value=f"{del_amt:,.0f}đ",
        new_value="0đ"
    )

    cursor.execute("DELETE FROM payment_installments WHERE id = ?", (installment_id,))

    # Tính lại tổng tiền sau khi xóa
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_total_paid = cursor.fetchone()[0]

    cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (booking_id,))
    total_price = cursor.fetchone()[0]

    if new_total_paid >= total_price and total_price > 0:
        new_status = "DA_TAT_TOAN"
    elif new_total_paid > 0:
        new_status = "MOT_PHAN"
    else:
        new_status = "CHUA_THANH_TOAN"

    cursor.execute("""
    UPDATE bookings 
    SET amount_paid = ?, payment_status = ?
    WHERE id = ?
    """, (new_total_paid, new_status, booking_id))

    conn.commit()
    conn.close()
    return {"message": "Đã xóa đợt thanh toán và cập nhật lại công nợ"}

@app.put("/api/installments/{installment_id}")
def update_installment(installment_id: int, data: InstallmentUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT pi.*, b.customer_name FROM payment_installments pi LEFT JOIN bookings b ON pi.booking_id = b.id WHERE pi.id = ?", (installment_id,))
    inst = cursor.fetchone()
    if not inst:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    booking_id = inst["booking_id"]
    new_name = data.installment_name if data.installment_name is not None else inst["installment_name"]
    new_amount = data.amount if data.amount is not None else inst["amount"]
    new_method = data.payment_method if data.payment_method is not None else inst["payment_method"]
    new_date = data.payment_date if data.payment_date is not None else inst["payment_date"]
    new_notes = data.notes if data.notes is not None else inst["notes"]
    
    # Ghi nhận Audit Log nếu số tiền thay đổi
    old_amt = float(inst["amount"] or 0)
    new_amt_val = float(new_amount or 0)
    if old_amt != new_amt_val:
        cust_name = inst["customer_name"] or "Không rõ"
        add_audit_log(
            cursor,
            action="UPDATE_PRICE_INSTALLMENT",
            target_type="installment",
            target_id=installment_id,
            summary=f"Sửa số tiền đợt thu '{new_name}' khách '{cust_name}' (Thợ: {inst['artist_name']}): {old_amt:,.0f}đ -> {new_amt_val:,.0f}đ",
            old_value=f"{old_amt:,.0f}đ",
            new_value=f"{new_amt_val:,.0f}đ"
        )

    cursor.execute("""
    UPDATE payment_installments SET
        installment_name = ?,
        amount = ?,
        payment_method = ?,
        payment_date = ?,
        notes = ?
    WHERE id = ?
    """, (new_name, new_amount, new_method, new_date, new_notes, installment_id))
    
    # Recalculate booking's amount_paid and payment_status
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_total_paid = cursor.fetchone()[0]
    cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (booking_id,))
    total_price = cursor.fetchone()[0]
    
    if new_total_paid >= total_price and total_price > 0:
        new_status = "DA_TAT_TOAN"
    elif new_total_paid > 0:
        new_status = "MOT_PHAN"
    else:
        new_status = "CHUA_THANH_TOAN"
        
    cursor.execute("UPDATE bookings SET amount_paid = ?, payment_status = ? WHERE id = ?", (new_total_paid, new_status, booking_id))
    conn.commit()
    conn.close()
    return {"message": "Cập nhật đợt thanh toán thành công", "new_total_paid": new_total_paid, "payment_status": new_status}

@app.put("/api/bookings/{booking_id}")
def update_booking(booking_id: int, data: BookingUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    bk = cursor.fetchone()
    if not bk:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch")
    
    new_cust_name = data.customer_name if data.customer_name is not None else bk["customer_name"]
    new_cust_phone = data.customer_phone if data.customer_phone is not None else bk["customer_phone"]
    new_artist = data.artist_name if data.artist_name is not None else bk["artist_name"]
    new_service = data.service_desc if data.service_desc is not None else bk["service_desc"]
    new_date = data.appointment_date if data.appointment_date is not None else bk["appointment_date"]
    new_total_price = data.total_price if data.total_price is not None else bk["total_price"]
    new_deposit_amt = data.deposit_amount if data.deposit_amount is not None else bk["deposit_amount"]
    new_deposit_status = data.deposit_status if data.deposit_status is not None else bk["deposit_status"]
    new_notes = data.notes if data.notes is not None else bk["notes"]
    new_client_source = data.client_source if data.client_source is not None else bk["client_source"]
    
    # Đồng bộ sang payment_installments nếu thông tin cọc được sửa trong form sửa đơn
    cursor.execute("SELECT id FROM payment_installments WHERE booking_id = ? AND installment_name LIKE '%Cọc%'", (booking_id,))
    deposit_inst = cursor.fetchone()
    float_dep = float(new_deposit_amt or 0)
    if new_deposit_status == "DA_COC" and float_dep > 0:
        if deposit_inst:
            cursor.execute("UPDATE payment_installments SET amount = ? WHERE id = ?", (float_dep, deposit_inst["id"]))
        else:
            cursor.execute("""
            INSERT INTO payment_installments (booking_id, installment_name, amount, payment_method, payment_date, notes, staff_payout_status)
            VALUES (?, 'Đợt 1 (Cọc)', ?, 'CHUYEN_KHOAN', ?, 'Cập nhật tiền cọc', 'CHUA_CHI')
            """, (booking_id, float_dep, datetime.now().strftime("%Y-%m-%d")))
    elif new_deposit_status == "CHUA_COC" or float_dep <= 0:
        if deposit_inst:
            cursor.execute("DELETE FROM payment_installments WHERE id = ?", (deposit_inst["id"],))

    # Recalculate amount_paid from all installments
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    recalculated_paid = cursor.fetchone()[0]

    # Recalculate payment_status based on recalculated_paid vs new_total_price
    if new_total_price > 0 and recalculated_paid >= new_total_price:
        new_pay_status = "DA_TAT_TOAN"
    elif recalculated_paid > 0:
        new_pay_status = "MOT_PHAN"
    else:
        new_pay_status = "CHUA_THANH_TOAN"

    # Ghi nhận Audit Log nếu thay đổi Tổng giá hoặc Tiền cọc hoặc Nguồn khách
    old_price = float(bk["total_price"] or 0)
    new_price = float(new_total_price or 0)
    old_dep = float(bk["deposit_amount"] or 0)
    new_dep = float(new_deposit_amt or 0)
    old_src = bk["client_source"] or "TIEM"
    changes = []
    if old_price != new_price:
        changes.append(f"Tổng giá: {old_price:,.0f}đ -> {new_price:,.0f}đ")
    if old_dep != new_dep:
        changes.append(f"Cọc: {old_dep:,.0f}đ -> {new_dep:,.0f}đ")
    if old_src != new_client_source:
        changes.append(f"Nguồn: {old_src} -> {new_client_source}")
    if changes:
        add_audit_log(
            cursor,
            action="UPDATE_PRICE_BOOKING",
            target_type="booking",
            target_id=booking_id,
            summary=f"Sửa thông tin tài chính khách '{new_cust_name}': {'; '.join(changes)}",
            old_value=f"{old_price:,.0f}đ",
            new_value=f"{new_price:,.0f}đ"
        )

    cursor.execute("""
    UPDATE bookings SET
        customer_name = ?,
        customer_phone = ?,
        artist_name = ?,
        service_desc = ?,
        appointment_date = ?,
        total_price = ?,
        deposit_amount = ?,
        deposit_status = ?,
        amount_paid = ?,
        payment_status = ?,
        notes = ?,
        client_source = ?
    WHERE id = ?
    """, (
        new_cust_name, new_cust_phone, new_artist, new_service,
        new_date, new_total_price, new_deposit_amt, new_deposit_status,
        recalculated_paid, new_pay_status, new_notes, new_client_source, booking_id
    ))
    
    # Đồng bộ sang các đợt xăm nếu có
    cursor.execute("""
    UPDATE tattoo_sessions SET
        customer_name = ?,
        customer_phone = ?
    WHERE booking_id = ?
    """, (new_cust_name, new_cust_phone, booking_id))
    
    conn.commit()
    conn.close()
    return {"message": "Cập nhật lịch thành công"}

@app.delete("/api/bookings/{booking_id}")
def delete_booking(booking_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    bk = cursor.fetchone()
    if bk:
        cust_name = bk["customer_name"]
        total_p = float(bk["total_price"] or 0)
        artist = bk["artist_name"] or ""
        add_audit_log(
            cursor,
            action="DELETE_BOOKING",
            target_type="booking",
            target_id=booking_id,
            summary=f"Xóa toàn bộ đơn khách '{cust_name}' (Thợ: {artist}, Trị giá: {total_p:,.0f}đ)",
            old_value=f"{total_p:,.0f}đ",
            new_value="Đã xóa"
        )
    # Xóa liên hoàn (cascade) toàn bộ đợt xăm, thanh toán và nhật ký liên quan
    cursor.execute("DELETE FROM payment_installments WHERE booking_id = ?", (booking_id,))
    cursor.execute("DELETE FROM tattoo_sessions WHERE booking_id = ?", (booking_id,))
    cursor.execute("DELETE FROM tattoo_logs WHERE booking_id = ?", (booking_id,))
    cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa lịch"}

# API BẢNG LỊCH CÁC ĐỢT XĂM (MULTI-SESSION TATTOO SCHEDULE)
@app.get("/api/sessions")
def get_tattoo_sessions(
    payment_status: Optional[str] = None,
    session_status: Optional[str] = None,
    artist_name: Optional[str] = None,
    search: Optional[str] = None
):
    conn = get_db()
    cursor = conn.cursor()
    query = """
    SELECT s.*, b.total_price as project_total_price, b.amount_paid as project_amount_paid
    FROM tattoo_sessions s
    LEFT JOIN bookings b ON s.booking_id = b.id
    WHERE 1=1
    """
    params = []
    if payment_status:
        query += " AND s.payment_status = ?"
        params.append(payment_status)
    if session_status:
        query += " AND s.session_status = ?"
        params.append(session_status)
    if artist_name and artist_name != 'ALL':
        query += " AND s.artist_name = ?"
        params.append(artist_name)
    if search:
        query += " AND (s.customer_name LIKE ? OR s.customer_phone LIKE ? OR s.artist_name LIKE ? OR s.stage_name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])

    query += " ORDER BY COALESCE(s.sort_order, 0) ASC, s.appointment_date ASC, s.session_number ASC"
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.get("/api/sessions/check-conflict")
def check_session_conflict(artist_name: str, appointment_date: str, exclude_id: Optional[int] = None):
    """
    Kiểm tra đợt xăm có bị trùng giờ với cùng thợ xăm hay không.
    appointment_date: 'YYYY-MM-DD HH:MM' hoặc 'YYYY-MM-DDTHH:MM'
    """
    if not artist_name or not appointment_date:
        return {"conflict": False, "message": "Thông tin không đủ để kiểm tra"}

    clean_artist = artist_name.strip()
    clean_date = appointment_date.strip().replace("T", " ")
    
    # Lấy tiền tố phút: 'YYYY-MM-DD HH:MM'
    clean_date_minute = clean_date[:16] if len(clean_date) >= 16 else clean_date

    conn = get_db()
    cursor = conn.cursor()
    
    query = """
    SELECT id, stage_name, customer_name, artist_name, appointment_date, session_price, session_status 
    FROM tattoo_sessions 
    WHERE LOWER(TRIM(artist_name)) = LOWER(?) 
      AND (
          appointment_date = ? 
          OR appointment_date LIKE ? 
          OR REPLACE(appointment_date, 'T', ' ') LIKE ?
      )
      AND session_status != 'CANCELLED'
    """
    params = [clean_artist, clean_date, f"{clean_date_minute}%", f"{clean_date_minute}%"]
    if exclude_id and exclude_id > 0:
        query += " AND id != ?"
        params.append(exclude_id)
        
    query += " LIMIT 1"
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()

    if row:
        conflict_session = dict(row)
        return {
            "conflict": True,
            "message": f"Thợ {clean_artist} đã có lịch đợt xăm '{conflict_session['stage_name']}' cho khách '{conflict_session['customer_name']}' vào lúc {conflict_session['appointment_date']}!",
            "session": conflict_session
        }
    return {"conflict": False, "message": "Không bị trùng lịch"}

@app.post("/api/sessions")
def create_tattoo_session(data: TattooSessionCreate):
    import re
    conn = get_db()
    cursor = conn.cursor()
    
    target_booking_id = data.booking_id
    raw_cust_name = (data.customer_name or "").strip()
    raw_cust_phone = (data.customer_phone or "").strip()
    raw_service_desc = (data.service_desc or "").strip()

    # Chuẩn hóa tên khách: loại bỏ chuỗi ghép thừa nếu có (vd: "cọp - Chân (Đen trắng) - Đợt 1 (1.400.000 đ)")
    if raw_cust_name and " - " in raw_cust_name and ("₫" in raw_cust_name or "đ" in raw_cust_name or "Đợt" in raw_cust_name):
        parts = re.split(r"\s+-\s+", raw_cust_name)
        if parts and parts[0].strip():
            raw_cust_name = parts[0].strip()

    booking = None
    if target_booking_id and target_booking_id > 0:
        cursor.execute("SELECT * FROM bookings WHERE id = ?", (target_booking_id,))
        booking = cursor.fetchone()

    # Nếu booking đã tồn tại hợp lệ
    if booking:
        # Giữ vững tên sạch của booking nếu tên truyền lên rỗng, trùng hoặc bắt đầu bằng tên booking
        b_name = (booking["customer_name"] or "").strip()
        if not raw_cust_name or raw_cust_name == b_name or raw_cust_name.startswith(b_name) or b_name.startswith(raw_cust_name):
            raw_cust_name = b_name
        if not raw_cust_phone and booking["customer_phone"]:
            raw_cust_phone = booking["customer_phone"]
        if not raw_service_desc and booking["service_desc"]:
            raw_service_desc = booking["service_desc"]
    elif raw_cust_name:
        # Nếu chưa có booking_id, tìm xem đã có booking nào trùng tên khách này chưa
        cursor.execute("SELECT * FROM bookings WHERE customer_name = ? ORDER BY id DESC LIMIT 1", (raw_cust_name,))
        existing_b = cursor.fetchone()
        if existing_b:
            booking = existing_b
            target_booking_id = existing_b["id"]
            if not raw_cust_phone and existing_b["customer_phone"]:
                raw_cust_phone = existing_b["customer_phone"]
            if not raw_service_desc and existing_b["service_desc"]:
                raw_service_desc = existing_b["service_desc"]
        else:
            # Tự động tạo một dự án/booking mới chuẩn chỉnh cho khách hàng mới này
            artist_for_b = (data.artist_name.strip() if data.artist_name and data.artist_name.strip() else "Chủ Tiệm")
            service_for_b = raw_service_desc or data.stage_name or "Hình xăm nghệ thuật"
            phone_for_b = raw_cust_phone or ""
            cursor.execute("""
            INSERT INTO bookings (
                customer_name, customer_phone, artist_name, service_desc,
                appointment_date, total_price, deposit_amount, deposit_status,
                payment_status, amount_paid, payment_method, notes, client_source
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 'CHUA_COC', 'CHUA_THANH_TOAN', 0, 'TIEN_MAT', 'Tạo tự động từ thêm đợt xăm', 'TIEM')
            """, (
                raw_cust_name, phone_for_b, artist_for_b, service_for_b,
                data.appointment_date, data.session_price or 1000000
            ))
            target_booking_id = cursor.lastrowid
            cursor.execute("SELECT * FROM bookings WHERE id = ?", (target_booking_id,))
            booking = cursor.fetchone()

    if not booking:
        conn.close()
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên khách hàng hoặc chọn khách hàng/dự án từ danh sách")

    final_customer_name = raw_cust_name if raw_cust_name else booking["customer_name"]
    final_customer_phone = raw_cust_phone if raw_cust_phone else (booking["customer_phone"] or "")
    final_service_desc = raw_service_desc if raw_service_desc else (booking["service_desc"] or data.stage_name or "Hình xăm")

    # Đếm số session hiện có để gợi ý session_number
    cursor.execute("SELECT COALESCE(MAX(session_number), 0) FROM tattoo_sessions WHERE booking_id = ?", (target_booking_id,))
    max_num = cursor.fetchone()[0]
    session_num = data.session_number if data.session_number > 0 else (max_num + 1)

    artist = (data.artist_name.strip() if data.artist_name and data.artist_name.strip() else booking["artist_name"])

    # Tự động lưu thợ vào danh sách nhân viên nếu là tên mới do chủ nhập
    if artist:
        cursor.execute("SELECT id FROM staff WHERE name = ?", (artist,))
        if not cursor.fetchone():
            cursor.execute("INSERT OR IGNORE INTO staff (name, role, is_active) VALUES (?, 'Thợ xăm', 1)", (artist,))

    cursor.execute("""
    INSERT INTO tattoo_sessions (
        booking_id, customer_name, customer_phone, artist_name, service_desc,
        session_number, stage_name, appointment_date, session_price,
        payment_status, session_status, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        target_booking_id, final_customer_name, final_customer_phone,
        artist, final_service_desc,
        session_num, data.stage_name, data.appointment_date,
        data.session_price, data.payment_status, data.session_status, data.notes
    ))
    session_id = cursor.lastrowid

    # Nếu đợt này đã thanh toán ngay lúc tạo -> Ghi nhận vào payment_installments & cập nhật bookings
    if data.payment_status == "DA_THANH_TOAN" and data.session_price > 0:
        cursor.execute("""
        INSERT INTO payment_installments (
            booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, notes,
            staff_payout_status, staff_paid_to_owner_status, staff_remit_type
        )
        VALUES (?, ?, ?, ?, ?, 'CHUYEN_KHOAN', ?, ?, 'CHUA_CHI', 'CHUA_NOP', '30')
        """, (target_booking_id, session_id, artist, data.stage_name, data.session_price, data.appointment_date, data.notes or "Thanh toán đợt xăm"))
        
        # Cập nhật tổng tiền đã trả của booking
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (target_booking_id,))
        new_paid = cursor.fetchone()[0]
        cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (target_booking_id,))
        tot_row = cursor.fetchone()
        tot_price = tot_row[0] if tot_row else 0
        new_pay_status = "DA_TAT_TOAN" if (new_paid >= tot_price and tot_price > 0) else "MOT_PHAN"
        cursor.execute("UPDATE bookings SET amount_paid = ?, payment_status = ? WHERE id = ?", (new_paid, new_pay_status, target_booking_id))

    conn.commit()
    conn.close()
    return {"message": "Tạo đợt xăm mới thành công", "id": session_id}

@app.put("/api/sessions/{session_id}/payment")
def update_session_payment(session_id: int, data: SessionPaymentUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tattoo_sessions WHERE id = ?", (session_id,))
    session = cursor.fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt xăm")

    old_status = session["payment_status"]
    new_status = data.payment_status
    booking_id = session["booking_id"]
    price = session["session_price"]
    session_artist = session["artist_name"]

    cursor.execute("UPDATE tattoo_sessions SET payment_status = ? WHERE id = ?", (new_status, session_id))

    # Nếu chuyển sang ĐÃ THANH TOÁN -> Ghi nhận vào payment_installments
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if new_status == "DA_THANH_TOAN" and old_status != "DA_THANH_TOAN" and price > 0:
        cursor.execute("SELECT id FROM payment_installments WHERE session_id = ?", (session_id,))
        existing_inst = cursor.fetchone()
        if existing_inst:
            cursor.execute("""
            UPDATE payment_installments 
            SET amount = ?, installment_name = ?, payment_method = ?, artist_name = ?
            WHERE id = ?
            """, (price, session["stage_name"], data.payment_method or "CHUYEN_KHOAN", session_artist, existing_inst["id"]))
        else:
            cursor.execute("""
            INSERT INTO payment_installments (
                booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, notes,
                staff_payout_status, staff_paid_to_owner_status, staff_remit_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CHUA_CHI', 'CHUA_NOP', '30')
            """, (booking_id, session_id, session_artist, session["stage_name"], price, data.payment_method or "CHUYEN_KHOAN", today_str, f"Thanh toán cho {session['stage_name']}"))
    elif new_status == "CHUA_THANH_TOAN" and old_status == "DA_THANH_TOAN":
        cursor.execute("DELETE FROM payment_installments WHERE session_id = ?", (session_id,))
    
    # Cập nhật lại tổng tiền đã trả của booking
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_paid = cursor.fetchone()[0]
    cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (booking_id,))
    tot_row = cursor.fetchone()
    tot_price = tot_row[0] if tot_row else 0
    
    new_pay_status = "DA_TAT_TOAN" if (new_paid >= tot_price and tot_price > 0) else ("MOT_PHAN" if new_paid > 0 else "CHUA_THANH_TOAN")
    cursor.execute("UPDATE bookings SET amount_paid = ?, payment_status = ? WHERE id = ?", (new_paid, new_pay_status, booking_id))

    conn.commit()
    conn.close()
    return {"message": "Cập nhật thanh toán đợt xăm thành công"}

@app.put("/api/sessions/{session_id}/status")
def update_session_status(session_id: int, data: SessionStatusUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE tattoo_sessions SET session_status = ? WHERE id = ?", (data.session_status, session_id))
    if data.session_status == "HOAN_THANH":
        sync_session_to_tattoo_log(cursor, session_id)
    conn.commit()
    conn.close()
    return {"message": "Cập nhật tiến độ đợt xăm thành công"}

@app.put("/api/sessions/{session_id}")
def update_session(session_id: int, data: TattooSessionUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tattoo_sessions WHERE id = ?", (session_id,))
    s = cursor.fetchone()
    if not s:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt xăm")
    
    new_num = data.session_number if data.session_number is not None else s["session_number"]
    new_stage = data.stage_name if data.stage_name is not None else s["stage_name"]
    new_date = data.appointment_date if data.appointment_date is not None else s["appointment_date"]
    new_price = data.session_price if data.session_price is not None else s["session_price"]
    new_pay_status = data.payment_status if data.payment_status is not None else s["payment_status"]
    new_sess_status = data.session_status if data.session_status is not None else s["session_status"]
    new_notes = data.notes if data.notes is not None else s["notes"]
    new_artist = data.artist_name if data.artist_name is not None else s["artist_name"]
    new_cust_name = data.customer_name.strip() if (data.customer_name and data.customer_name.strip()) else s["customer_name"]
    new_cust_phone = data.customer_phone.strip() if data.customer_phone is not None else s["customer_phone"]
    new_service_desc = data.service_desc.strip() if (data.service_desc and data.service_desc.strip()) else s["service_desc"]
    booking_id = s["booking_id"]
    
    # Ghi nhận Audit Log nếu sửa giá đợt xăm
    old_sess_price = float(s["session_price"] or 0)
    new_sess_price = float(new_price or 0)
    if old_sess_price != new_sess_price:
        add_audit_log(
            cursor,
            action="UPDATE_PRICE_SESSION",
            target_type="session",
            target_id=session_id,
            summary=f"Sửa giá đợt xăm '{new_stage}' khách '{new_cust_name}' (Thợ: {new_artist}): {old_sess_price:,.0f}đ -> {new_sess_price:,.0f}đ",
            old_value=f"{old_sess_price:,.0f}đ",
            new_value=f"{new_sess_price:,.0f}đ"
        )

    cursor.execute("""
    UPDATE tattoo_sessions SET
        session_number = ?,
        stage_name = ?,
        appointment_date = ?,
        session_price = ?,
        payment_status = ?,
        session_status = ?,
        notes = ?,
        artist_name = ?,
        customer_name = ?,
        customer_phone = ?,
        service_desc = ?
    WHERE id = ?
    """, (new_num, new_stage, new_date, new_price, new_pay_status, new_sess_status, new_notes, new_artist, new_cust_name, new_cust_phone, new_service_desc, session_id))

    # Nếu sửa tên khách và booking này chỉ có đợt này hoặc booking là khách test, cập nhật luôn booking
    if booking_id and data.customer_name and data.customer_name.strip():
        cursor.execute("SELECT customer_name FROM bookings WHERE id = ?", (booking_id,))
        b_row = cursor.fetchone()
        if b_row and ("test" in (b_row["customer_name"] or "").lower() or b_row["customer_name"] != new_cust_name):
            cursor.execute("UPDATE bookings SET customer_name = ?, customer_phone = ? WHERE id = ?", (new_cust_name, new_cust_phone or '', booking_id))

    # Đồng bộ với payment_installments
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT id FROM payment_installments WHERE session_id = ?", (session_id,))
    existing_inst = cursor.fetchone()
    if new_pay_status == "DA_THANH_TOAN" and new_price > 0:
        if existing_inst:
            cursor.execute("""
            UPDATE payment_installments 
            SET amount = ?, installment_name = ?, artist_name = ?
            WHERE id = ?
            """, (new_price, new_stage, new_artist, existing_inst["id"]))
        else:
            cursor.execute("""
            INSERT INTO payment_installments (
                booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, notes,
                staff_payout_status, staff_paid_to_owner_status, staff_remit_type
            )
            VALUES (?, ?, ?, ?, ?, 'CHUYEN_KHOAN', ?, ?, 'CHUA_CHI', 'CHUA_NOP', '30')
            """, (booking_id, session_id, new_artist, new_stage, new_price, new_date or today_str, new_notes or "Thanh toán đợt xăm"))
    elif new_pay_status == "CHUA_THANH_TOAN" and existing_inst:
        cursor.execute("DELETE FROM payment_installments WHERE session_id = ?", (session_id,))

    # Cập nhật tổng tiền đã trả của booking
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
    new_paid = cursor.fetchone()[0]
    cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (booking_id,))
    tot_row = cursor.fetchone()
    tot_price = tot_row[0] if tot_row else 0
    new_b_status = "DA_TAT_TOAN" if (new_paid >= tot_price and tot_price > 0) else ("MOT_PHAN" if new_paid > 0 else "CHUA_THANH_TOAN")
    cursor.execute("UPDATE bookings SET amount_paid = ?, payment_status = ? WHERE id = ?", (new_paid, new_b_status, booking_id))
    
    conn.commit()
    conn.close()
    return {"message": "Cập nhật đợt xăm thành công"}

@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tattoo_sessions WHERE id = ?", (session_id,))
    s = cursor.fetchone()
    if s:
        b_id = s["booking_id"]
        sess_name = s["stage_name"] or "Đợt xăm"
        cust_name = s["customer_name"] or "Không rõ"
        price = float(s["session_price"] or 0)
        artist = s["artist_name"] or ""
        add_audit_log(
            cursor,
            action="DELETE_SESSION",
            target_type="session",
            target_id=session_id,
            summary=f"Xóa đợt xăm '{sess_name}' khách '{cust_name}' (Thợ: {artist}, Giá đợt: {price:,.0f}đ)",
            old_value=f"{price:,.0f}đ",
            new_value="Đã xóa"
        )
        cursor.execute("DELETE FROM payment_installments WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM tattoo_sessions WHERE id = ?", (session_id,))
        # Cập nhật lại booking
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (b_id,))
        new_paid = cursor.fetchone()[0]
        cursor.execute("SELECT total_price FROM bookings WHERE id = ?", (b_id,))
        tot_row = cursor.fetchone()
        tot_price = tot_row[0] if tot_row else 0
        new_b_status = "DA_TAT_TOAN" if (new_paid >= tot_price and tot_price > 0) else ("MOT_PHAN" if new_paid > 0 else "CHUA_THANH_TOAN")
        cursor.execute("UPDATE bookings SET amount_paid = ?, payment_status = ? WHERE id = ?", (new_paid, new_b_status, b_id))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa đợt xăm"}

@app.post("/api/sessions/reorder")
def reorder_sessions(data: SessionReorderRequest):
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Nếu có chuyển thợ (target_artist)
    if data.target_artist and data.target_artist.strip():
        new_artist = data.target_artist.strip()
        cursor.execute("UPDATE tattoo_sessions SET artist_name = ? WHERE id = ?", (new_artist, data.session_id))
        cursor.execute("UPDATE payment_installments SET artist_name = ? WHERE session_id = ?", (new_artist, data.session_id))
        
        # Cập nhật booking_id tương ứng để nhất quán
        cursor.execute("SELECT booking_id FROM tattoo_sessions WHERE id = ?", (data.session_id,))
        b_row = cursor.fetchone()
        if b_row and b_row["booking_id"]:
            cursor.execute("UPDATE bookings SET artist_name = ? WHERE id = ?", (new_artist, b_row["booking_id"]))

        # Đảm bảo thợ có trong bảng staff
        cursor.execute("SELECT id FROM staff WHERE name = ?", (new_artist,))
        if not cursor.fetchone():
            cursor.execute("INSERT OR IGNORE INTO staff (name, role, is_active) VALUES (?, 'Thợ xăm', 1)", (new_artist,))

    # 1b. Cập nhật thời gian hẹn nếu kéo thả đổi giờ (target_date)
    if data.target_date and data.target_date.strip():
        new_date = data.target_date.strip().replace("T", " ")
        cursor.execute("UPDATE tattoo_sessions SET appointment_date = ? WHERE id = ?", (new_date, data.session_id))

    # 2. Cập nhật thứ tự sắp xếp trong cột
    if data.ordered_ids and len(data.ordered_ids) > 0:
        for idx, s_id in enumerate(data.ordered_ids):
            cursor.execute("UPDATE tattoo_sessions SET sort_order = ? WHERE id = ?", (idx, s_id))

    conn.commit()
    conn.close()
    return {"message": "Cập nhật kéo thả thành công", "session_id": data.session_id, "target_artist": data.target_artist}

# API NHÂN VIÊN (STAFF MANAGEMENT - THÊM / SỬA / XÓA)
@app.get("/api/staff")
def get_staff_list():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.*, u.username, u.id as user_id
    FROM staff s
    LEFT JOIN users u ON u.staff_id = s.id
    ORDER BY s.id ASC
    """)
    staff_members = [dict(row) for row in cursor.fetchall()]

    for s in staff_members:
        name = s["name"]
        cursor.execute("SELECT COUNT(id) FROM bookings WHERE artist_name = ?", (name,))
        s["total_jobs"] = cursor.fetchone()[0]

    conn.close()
    return staff_members

@app.post("/api/staff")
def create_staff(data: StaffCreate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        is_owner = 'chủ' in data.role.lower() or 'owner' in data.role.lower()
        if data.commission_rate is not None and float(data.commission_rate) > 0:
            comm_rate = float(data.commission_rate)
        else:
            comm_rate = 100.0 if is_owner else 70.0
        cursor.execute("""
        INSERT INTO staff (name, phone, role, commission_rate, is_active, avatar_url, theme_color)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data.name.strip(), data.phone.strip() if data.phone else "", data.role.strip(), comm_rate, 1 if data.is_active else 0, data.avatar_url or "", data.theme_color or ""))
        s_id = cursor.lastrowid

        # Nếu có cung cấp tài khoản & mật khẩu cho nhân viên
        if data.username and data.username.strip():
            u_name = data.username.strip().lower()
            pwd = data.password.strip() if (data.password and len(data.password.strip()) >= 4) else "123456"
            cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, staff_id, is_active)
            VALUES (?, ?, ?, 'staff', ?, ?)
            """, (u_name, hash_password(pwd), data.name.strip(), s_id, 1 if data.is_active else 0))

        conn.commit()
        conn.close()
        return {"message": "Thêm nhân viên thành công", "id": s_id}
    except sqlite3.IntegrityError as e:
        conn.close()
        err_msg = str(e).lower()
        if 'users.username' in err_msg:
            raise HTTPException(status_code=400, detail="Tên đăng nhập này đã được sử dụng!")
        raise HTTPException(status_code=400, detail="Tên nhân viên này đã tồn tại")

@app.put("/api/staff/{staff_id}")
def update_staff(staff_id: int, data: StaffUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM staff WHERE id = ?", (staff_id,))
    staff = cursor.fetchone()
    if not staff:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")

    new_name = data.name.strip() if data.name is not None else staff["name"]
    new_phone = data.phone.strip() if data.phone is not None else staff["phone"]
    new_role = data.role.strip() if data.role is not None else staff["role"]
    new_active = (1 if data.is_active else 0) if data.is_active is not None else staff["is_active"]
    new_avatar = data.avatar_url if data.avatar_url is not None else (staff["avatar_url"] if "avatar_url" in staff.keys() else "")
    new_theme = data.theme_color if data.theme_color is not None else (staff["theme_color"] if "theme_color" in staff.keys() else "")

    try:
        is_owner = 'chủ' in new_role.lower() or 'owner' in new_role.lower()
        if data.commission_rate is not None and float(data.commission_rate) > 0:
            new_comm = float(data.commission_rate)
        elif staff["commission_rate"] is not None:
            new_comm = float(staff["commission_rate"])
        else:
            new_comm = 100.0 if is_owner else 70.0

        cursor.execute("""
        UPDATE staff SET 
            name = ?,
            phone = ?,
            role = ?,
            commission_rate = ?,
            is_active = ?,
            avatar_url = ?,
            theme_color = ?
        WHERE id = ?
        """, (new_name, new_phone, new_role, new_comm, new_active, new_avatar, new_theme, staff_id))

        # Đồng bộ đổi tên sang toàn bộ lịch hẹn và các đợt xăm
        if new_name != staff["name"]:
            cursor.execute("UPDATE bookings SET artist_name = ? WHERE artist_name = ?", (new_name, staff["name"]))
            cursor.execute("UPDATE tattoo_sessions SET artist_name = ? WHERE artist_name = ?", (new_name, staff["name"]))

        # Quản lý tài khoản đăng nhập của nhân viên
        if data.username and data.username.strip():
            u_name = data.username.strip().lower()
            cursor.execute("SELECT id FROM users WHERE staff_id = ?", (staff_id,))
            exist_u = cursor.fetchone()
            if exist_u:
                if data.password and len(data.password.strip()) >= 4:
                    cursor.execute("""
                    UPDATE users SET username = ?, full_name = ?, password_hash = ?, is_active = ? WHERE id = ?
                    """, (u_name, new_name, hash_password(data.password.strip()), new_active, exist_u["id"]))
                else:
                    cursor.execute("""
                    UPDATE users SET username = ?, full_name = ?, is_active = ? WHERE id = ?
                    """, (u_name, new_name, new_active, exist_u["id"]))
            else:
                pwd = data.password.strip() if (data.password and len(data.password.strip()) >= 4) else "123456"
                cursor.execute("""
                INSERT INTO users (username, password_hash, full_name, role, staff_id, is_active)
                VALUES (?, ?, ?, 'staff', ?, ?)
                """, (u_name, hash_password(pwd), new_name, staff_id, new_active))

        conn.commit()
        conn.close()
        return {"message": "Cập nhật nhân viên thành công"}
    except sqlite3.IntegrityError as e:
        conn.close()
        err_msg = str(e).lower()
        if 'users.username' in err_msg:
            raise HTTPException(status_code=400, detail="Tên đăng nhập này đã được sử dụng bởi người khác!")
        raise HTTPException(status_code=400, detail="Tên nhân viên bị trùng lặp")

@app.delete("/api/staff/{staff_id}")
def delete_staff(staff_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM staff WHERE id = ?", (staff_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")
    
    cursor.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa nhân viên thành công"}

# API BẢNG GIÁ HÌNH XĂM / ĐỊNH MỨC CHO NHÂN VIÊN
@app.get("/api/services")
def get_services():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tattoo_services ORDER BY id ASC")
    services = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return services

@app.post("/api/services")
def create_service(data: ServiceCreate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tattoo_services (category, color_type, price, description)
    VALUES (?, ?, ?, ?)
    """, (data.category.strip(), data.color_type.strip(), data.price, data.description.strip() if data.description else ""))
    conn.commit()
    s_id = cursor.lastrowid
    conn.close()
    return {"message": "Thêm loại hình bảng giá thành công", "id": s_id}

@app.put("/api/services/{service_id}")
def update_service(service_id: int, data: ServiceUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tattoo_services WHERE id = ?", (service_id,))
    item = cursor.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy mục bảng giá")
    
    new_cat = data.category.strip() if data.category is not None else item["category"]
    new_color = data.color_type.strip() if data.color_type is not None else item["color_type"]
    new_price = data.price if data.price is not None else item["price"]
    new_desc = data.description.strip() if data.description is not None else item["description"]

    cursor.execute("""
    UPDATE tattoo_services SET category = ?, color_type = ?, price = ?, description = ?
    WHERE id = ?
    """, (new_cat, new_color, new_price, new_desc, service_id))
    conn.commit()
    conn.close()
    return {"message": "Cập nhật bảng giá thành công"}

@app.delete("/api/services/{service_id}")
def delete_service(service_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM tattoo_services WHERE id = ?", (service_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy mục bảng giá")
    cursor.execute("DELETE FROM tattoo_services WHERE id = ?", (service_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa mục bảng giá thành công"}

def is_owner_name(art_name: str, staff_map: dict = None) -> bool:
    name_low = (art_name or "").strip().lower()
    if not name_low:
        return False
    if "chủ" in name_low or "bắp" in name_low:
        return True
    if staff_map:
        st = staff_map.get(name_low, {})
        role = (st.get("role") or "").lower()
        if "chủ" in role or "owner" in role or float(st.get("commission_rate") or 0) == 100.0:
            return True
    return False

# API TÍNH CÔNG & LƯƠNG NHÂN VIÊN (KẾT HỢP 70/30 VÀ 100% THU HỘ GIỮA THỢ VÀ CHỦ TIỆM)
@app.get("/api/staff-payroll")
def get_staff_payroll():
    conn = get_db()
    cursor = conn.cursor()
    
    # Lấy danh sách thợ và vai trò
    cursor.execute("SELECT name, role, commission_rate FROM staff")
    staff_map = {r["name"].strip().lower(): dict(r) for r in cursor.fetchall()}

    # Lấy toàn bộ các đợt thanh toán kèm thông tin nguồn khách từ bookings và tattoo_sessions
    cursor.execute("""
    SELECT 
        pi.id,
        pi.booking_id,
        pi.session_id,
        COALESCE(NULLIF(pi.artist_name, ''), NULLIF(s.artist_name, ''), NULLIF(b.artist_name, ''), 'Thợ xăm') AS artist_name,
        pi.installment_name,
        pi.amount,
        pi.payment_method,
        pi.payment_date,
        pi.notes,
        COALESCE(pi.staff_payout_status, 'CHUA_CHI') as staff_payout_status,
        pi.staff_payout_date,
        COALESCE(pi.staff_paid_to_owner_status, 'CHUA_NOP') as staff_paid_to_owner_status,
        pi.staff_paid_to_owner_date,
        COALESCE(pi.staff_remit_type, '30') as staff_remit_type,
        pi.custom_remit_to_owner,
        pi.custom_staff_share,
        pi.commission_rate,
        b.customer_name,
        b.customer_phone,
        b.service_desc,
        b.total_price,
        COALESCE(b.client_source, 'TIEM') as client_source
    FROM payment_installments pi
    JOIN bookings b ON pi.booking_id = b.id
    LEFT JOIN tattoo_sessions s ON pi.session_id = s.id
    ORDER BY pi.id DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    
    # Lấy toàn bộ lịch sử các lần thợ nộp tiền theo từng đợt
    cursor.execute("""
    SELECT id, installment_id, amount, payment_method, payment_date, note, created_at
    FROM staff_remit_payments
    ORDER BY id ASC
    """)
    all_remit_payments = [dict(p) for p in cursor.fetchall()]
    remit_map = {}
    for p in all_remit_payments:
        iid = p["installment_id"]
        if iid not in remit_map:
            remit_map[iid] = []
        remit_map[iid].append(p)

    staff_client_installments = []
    # 1. Chiều Tiệm chi trả công cho thợ (70%)
    total_staff_earned = 0.0      # 70% của các đợt khách nhân viên
    total_staff_paid = 0.0        # Đã chi cho nhân viên
    total_staff_pending = 0.0     # Còn nợ nhân viên
    total_studio_from_staff = 0.0 # 30% tiệm giữ từ khách nhân viên
    total_studio_own_clients = 0.0# 100% tiệm tự ăn tất từ khách của chủ tiệm

    # 2. Chiều Thợ thanh toán cho chủ tiệm (Kết hợp phương án A: 30% và B: 100% thu hộ)
    total_staff_to_owner_required = 0.0 # Tổng tiền thợ phải nộp chủ
    total_staff_to_owner_paid = 0.0     # Thợ đã thanh toán cho chủ tiệm
    total_staff_to_owner_pending = 0.0  # Thợ còn nợ chủ tiệm

    for r in rows:
        amount = float(r["amount"] or 0)
        source = r["client_source"]
        art_name = r.get("artist_name") or ""
        is_owner = is_owner_name(art_name, staff_map)
        
        # Đợt thu này thuộc về bảng tính công nhân viên nếu:
        # Thợ thực hiện là Nhân Viên (không phải Chủ tiệm), HOẶC nguồn khách được gắn là NHAN_VIEN
        is_staff_case = (not is_owner) or (source == "NHAN_VIEN")
        
        if is_staff_case and not is_owner:
            # 1. Tỷ lệ % thợ nhận và % tiệm giữ (ví dụ: 70/30, 60/40, 50/50, 40/60, 80/20...)
            inst_comm = r.get("commission_rate")
            if inst_comm is not None and float(inst_comm) > 0:
                staff_pct = float(inst_comm)
            elif art_name.strip().lower() in staff_map and staff_map[art_name.strip().lower()].get("commission_rate") is not None:
                staff_pct = float(staff_map[art_name.strip().lower()]["commission_rate"])
            else:
                staff_pct = 70.0

            studio_pct = max(0.0, round(100.0 - staff_pct, 2))
            remit_type = str(r.get("staff_remit_type") or "PERCENT")
            if remit_type == "30":
                remit_type = "PERCENT"

            # Tiền công thợ: nếu có số tiền tự nhập (custom_staff_share) thì dùng trực tiếp
            if r.get("custom_staff_share") is not None and float(r["custom_staff_share"]) >= 0:
                staff_share = round(float(r["custom_staff_share"]), 2)
            else:
                staff_share = round(amount * (staff_pct / 100.0), 2)

            studio_share = round(amount * (studio_pct / 100.0), 2)

            # Tiền thợ nộp chủ: nếu có số tiền tự nhập (custom_remit_to_owner) thì dùng trực tiếp
            if r.get("custom_remit_to_owner") is not None and float(r["custom_remit_to_owner"]) >= 0:
                remit_to_owner = round(float(r["custom_remit_to_owner"]), 2)
            elif remit_type == "100":
                remit_to_owner = round(amount, 2) # Phương án 100% thu hộ cả đơn
            else:
                remit_to_owner = studio_share      # Phương án nộp hoa hồng theo % tiệm hưởng

            # Tính toán số tiền thợ đã nộp và số tiền CÒN NỢ cho đợt này
            r_payments = remit_map.get(r["id"], [])
            if r_payments:
                remit_paid = round(sum(float(p["amount"] or 0) for p in r_payments), 2)
            elif r["staff_paid_to_owner_status"] == "DA_NOP":
                # Dữ liệu cũ đã đánh dấu nộp
                remit_paid = remit_to_owner
            else:
                remit_paid = 0.0

            remit_remaining = max(0.0, round(remit_to_owner - remit_paid, 2))

            if remit_remaining <= 0:
                remit_status = "DA_NOP"
            elif remit_paid > 0:
                remit_status = "MOT_PHAN"
            else:
                remit_status = "CHUA_NOP"

            r["commission_rate"] = staff_pct
            r["studio_commission_rate"] = studio_pct
            r["staff_share"] = staff_share
            r["studio_share"] = studio_share
            r["remit_to_owner"] = remit_to_owner
            r["remit_paid"] = remit_paid
            r["remit_remaining"] = remit_remaining
            r["staff_paid_to_owner_status"] = remit_status
            r["staff_remit_type"] = remit_type
            r["remit_payments"] = r_payments
            
            # Tính chỉ số Tiệm chi trả thợ (70%)
            total_staff_earned += staff_share
            total_studio_from_staff += studio_share
            
            if r["staff_payout_status"] == "DA_CHI":
                total_staff_paid += staff_share

            # Tính chỉ số Thợ thanh toán cho chủ tiệm
            total_staff_to_owner_required += remit_to_owner
            total_staff_to_owner_paid += remit_paid
                
            staff_client_installments.append(r)
        else:
            total_studio_own_clients += amount

    # Lấy danh sách giao dịch nợ/trả tự nhập từ bảng staff_debt_transactions
    cursor.execute("""
    SELECT id, artist_name, transaction_type, amount, payment_method, note, created_at
    FROM staff_debt_transactions
    ORDER BY id DESC
    """)
    transactions = [dict(t) for t in cursor.fetchall()]

    conn.close()

    # Nhóm dữ liệu và công nợ theo từng thợ
    by_artist = {}
    for st_name_low, st_info in staff_map.items():
        original_name = st_info.get("name") or st_name_low.upper()
        if not is_owner_name(original_name, staff_map):
            by_artist[original_name] = {
                "artist_name": original_name,
                "total_staff_to_owner_required": 0.0,
                "total_staff_to_owner_paid": 0.0,
                "total_staff_to_owner_pending": 0.0,
                "unpaid_remit_count": 0,
                "total_staff_earned": 0.0,
                "total_staff_paid": 0.0,
                "total_staff_pending": 0.0,
                "unpaid_payout_count": 0,
                "total_installments_count": 0,
                "net_balance": 0.0,
                "manual_paid_to_owner": 0.0,
                "manual_paid_from_owner": 0.0,
                "manual_added_debt": 0.0,
                "manual_added_bonus": 0.0
            }

    for r in staff_client_installments:
        art = r.get("artist_name") or "Thợ xăm"
        # Tìm key phù hợp không phân biệt hoa thường
        matched_key = None
        for k in by_artist:
            if k.strip().lower() == art.strip().lower():
                matched_key = k
                break
        if not matched_key:
            matched_key = art
            by_artist[art] = {
                "artist_name": art,
                "total_staff_to_owner_required": 0.0,
                "total_staff_to_owner_paid": 0.0,
                "total_staff_to_owner_pending": 0.0,
                "unpaid_remit_count": 0,
                "total_staff_earned": 0.0,
                "total_staff_paid": 0.0,
                "total_staff_pending": 0.0,
                "unpaid_payout_count": 0,
                "total_installments_count": 0,
                "net_balance": 0.0,
                "manual_paid_to_owner": 0.0,
                "manual_paid_from_owner": 0.0,
                "manual_added_debt": 0.0,
                "manual_added_bonus": 0.0
            }
        st = by_artist[matched_key]
        st["total_installments_count"] += 1
        st["total_staff_earned"] += r["staff_share"]
        if r["staff_payout_status"] == "DA_CHI":
            st["total_staff_paid"] += r["staff_share"]
        else:
            st["unpaid_payout_count"] += 1

        st["total_staff_to_owner_required"] += r["remit_to_owner"]
        st["total_staff_to_owner_paid"] += r["remit_paid"]
        if r["staff_paid_to_owner_status"] != "DA_NOP":
            st["unpaid_remit_count"] += 1

    # Áp dụng các khoản giao dịch tự nhập (Nộp nợ, Trả công, Ứng nợ, Thưởng)
    for t in transactions:
        t_amt = float(t["amount"] or 0)
        t_type = t["transaction_type"]
        t_art = (t.get("artist_name") or "").strip().lower()

        # Áp dụng vào tổng thể chung tiệm
        if t_type == "STAFF_PAY_OWNER":
            total_staff_to_owner_paid += t_amt
        elif t_type == "OWNER_PAY_STAFF":
            total_staff_paid += t_amt
        elif t_type == "ADD_DEBT":
            total_staff_to_owner_required += t_amt
        elif t_type == "ADD_BONUS":
            total_staff_earned += t_amt

        # Áp dụng vào từng thợ
        for k, st in by_artist.items():
            if k.strip().lower() == t_art:
                if t_type == "STAFF_PAY_OWNER":
                    st["total_staff_to_owner_paid"] += t_amt
                    st["manual_paid_to_owner"] += t_amt
                elif t_type == "OWNER_PAY_STAFF":
                    st["total_staff_paid"] += t_amt
                    st["manual_paid_from_owner"] += t_amt
                elif t_type == "ADD_DEBT":
                    st["total_staff_to_owner_required"] += t_amt
                    st["manual_added_debt"] += t_amt
                elif t_type == "ADD_BONUS":
                    st["total_staff_earned"] += t_amt
                    st["manual_added_bonus"] += t_amt

    # Tính toán nợ tồn đọng sau cấn trừ giao dịch cho từng thợ
    for art, st in by_artist.items():
        st["total_staff_to_owner_required"] = round(st["total_staff_to_owner_required"], 2)
        st["total_staff_to_owner_paid"] = round(st["total_staff_to_owner_paid"], 2)
        st["total_staff_to_owner_pending"] = max(0.0, round(st["total_staff_to_owner_required"] - st["total_staff_to_owner_paid"], 2))
        st["total_staff_earned"] = round(st["total_staff_earned"], 2)
        st["total_staff_paid"] = round(st["total_staff_paid"], 2)
        st["total_staff_pending"] = max(0.0, round(st["total_staff_earned"] - st["total_staff_paid"], 2))
        st["net_balance"] = round(st["total_staff_to_owner_pending"] - st["total_staff_pending"], 2)

    # Tính chỉ số nợ tồn đọng tổng quan tiệm
    total_staff_to_owner_pending = max(0.0, round(total_staff_to_owner_required - total_staff_to_owner_paid, 2))
    total_staff_pending = max(0.0, round(total_staff_earned - total_staff_paid, 2))
    net_balance = round(total_staff_to_owner_pending - total_staff_pending, 2)

    return {
        "summary": {
            # Chiều thợ thanh toán cho chủ
            "total_staff_to_owner_required": round(total_staff_to_owner_required, 2),
            "total_staff_to_owner_paid": round(total_staff_to_owner_paid, 2),
            "total_staff_to_owner_pending": total_staff_to_owner_pending,
            # Chiều tiệm trả cho thợ
            "total_staff_earned": round(total_staff_earned, 2),
            "total_staff_paid": round(total_staff_paid, 2),
            "total_staff_pending": total_staff_pending,
            # Bù trừ hai bên
            "net_balance": net_balance,
            # Doanh thu chung
            "total_studio_from_staff": round(total_studio_from_staff, 2),
            "total_studio_own_clients": round(total_studio_own_clients, 2),
            "total_studio_revenue": round(total_studio_own_clients + total_studio_from_staff, 2)
        },
        "by_artist": by_artist,
        "installments": staff_client_installments,
        "transactions": transactions
    }

@app.put("/api/staff-payroll/{installment_id}/payout")
def toggle_payout_status(installment_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT staff_payout_status FROM payment_installments WHERE id = ?", (installment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    current = row[0]
    new_status = "CHUA_CHI" if current == "DA_CHI" else "DA_CHI"
    payout_date = datetime.now().strftime("%Y-%m-%d %H:%M") if new_status == "DA_CHI" else None
    
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_payout_status = ?, staff_payout_date = ?
    WHERE id = ?
    """, (new_status, payout_date, installment_id))
    conn.commit()
    conn.close()
    return {
        "message": "Cập nhật trạng thái chi trả thành công",
        "new_status": new_status,
        "payout_date": payout_date
    }

@app.post("/api/staff-payroll/{installment_id}/remit-payment")
def add_installment_remit_payment(installment_id: int, data: InstallmentRemitPaymentCreate):
    """Ghi nhận một lần thợ nộp tiền cho chủ tiệm (cho phép nộp nhiều lần/trả góp)"""
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền nộp phải lớn hơn 0")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT pi.id, pi.amount, pi.custom_remit_to_owner, COALESCE(pi.staff_remit_type, 'PERCENT'), pi.commission_rate, pi.artist_name 
    FROM payment_installments pi 
    WHERE pi.id = ?
    """, (installment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    inst_amount = float(row[1] or 0)
    custom_remit = row[2]
    remit_type = str(row[3] or "PERCENT")
    inst_comm = row[4]
    art_name = str(row[5] or "")

    if custom_remit is not None and float(custom_remit) >= 0:
        total_required = round(float(custom_remit), 2)
    elif remit_type == "100":
        total_required = round(inst_amount, 2)
    else:
        if inst_comm is not None and float(inst_comm) > 0:
            staff_pct = float(inst_comm)
        elif art_name:
            cursor.execute("SELECT commission_rate FROM staff WHERE LOWER(name) = LOWER(?)", (art_name.strip(),))
            srow = cursor.fetchone()
            staff_pct = float(srow[0]) if (srow and srow[0]) else 70.0
        else:
            staff_pct = 70.0
        studio_pct = max(0.0, round(100.0 - staff_pct, 2))
        total_required = round(inst_amount * (studio_pct / 100.0), 2)
    
    p_date = data.payment_date or datetime.now().strftime("%Y-%m-%d %H:%M")
    p_method = data.payment_method or "TIEN_MAT"
    p_note = data.note or ""

    cursor.execute("""
    INSERT INTO staff_remit_payments (installment_id, amount, payment_method, payment_date, note)
    VALUES (?, ?, ?, ?, ?)
    """, (installment_id, data.amount, p_method, p_date, p_note))
    payment_id = cursor.lastrowid

    # Tính tổng tiền thợ đã nộp sau lần này
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM staff_remit_payments WHERE installment_id = ?", (installment_id,))
    new_total_paid = round(float(cursor.fetchone()[0] or 0), 2)
    remit_remaining = max(0.0, round(total_required - new_total_paid, 2))

    if remit_remaining <= 0:
        new_status = "DA_NOP"
    elif new_total_paid > 0:
        new_status = "MOT_PHAN"
    else:
        new_status = "CHUA_NOP"

    cursor.execute("""
    UPDATE payment_installments
    SET staff_paid_to_owner_status = ?, staff_paid_to_owner_date = ?
    WHERE id = ?
    """, (new_status, p_date if new_status == "DA_NOP" else None, installment_id))

    conn.commit()
    conn.close()

    return {
        "message": f"Đã ghi nhận thợ nộp thành công {int(data.amount):,} đ",
        "payment_id": payment_id,
        "total_required": total_required,
        "total_paid": new_total_paid,
        "remit_remaining": remit_remaining,
        "new_status": new_status
    }

@app.delete("/api/staff-payroll/remit-payment/{payment_id}")
def delete_installment_remit_payment(payment_id: int):
    """Xóa một lần thợ nộp tiền (nếu nhập nhầm)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT installment_id, amount FROM staff_remit_payments WHERE id = ?", (payment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử nộp tiền này")
    
    installment_id = row[0]
    cursor.execute("DELETE FROM staff_remit_payments WHERE id = ?", (payment_id,))

    # Lấy thông tin đợt để tính lại trạng thái
    cursor.execute("""
    SELECT pi.amount, pi.custom_remit_to_owner, COALESCE(pi.staff_remit_type, 'PERCENT'), pi.commission_rate, pi.artist_name 
    FROM payment_installments pi 
    WHERE pi.id = ?
    """, (installment_id,))
    inst_row = cursor.fetchone()
    if inst_row:
        inst_amount = float(inst_row[0] or 0)
        custom_remit = inst_row[1]
        remit_type = str(inst_row[2] or "PERCENT")
        inst_comm = inst_row[3]
        art_name = str(inst_row[4] or "")

        if custom_remit is not None and float(custom_remit) >= 0:
            total_required = round(float(custom_remit), 2)
        elif remit_type == "100":
            total_required = round(inst_amount, 2)
        else:
            if inst_comm is not None and float(inst_comm) > 0:
                staff_pct = float(inst_comm)
            elif art_name:
                cursor.execute("SELECT commission_rate FROM staff WHERE LOWER(name) = LOWER(?)", (art_name.strip(),))
                srow = cursor.fetchone()
                staff_pct = float(srow[0]) if (srow and srow[0]) else 70.0
            else:
                staff_pct = 70.0
            studio_pct = max(0.0, round(100.0 - staff_pct, 2))
            total_required = round(inst_amount * (studio_pct / 100.0), 2)

        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM staff_remit_payments WHERE installment_id = ?", (installment_id,))
        new_total_paid = round(float(cursor.fetchone()[0] or 0), 2)
        remit_remaining = max(0.0, round(total_required - new_total_paid, 2))

        if remit_remaining <= 0:
            new_status = "DA_NOP"
        elif new_total_paid > 0:
            new_status = "MOT_PHAN"
        else:
            new_status = "CHUA_NOP"

        cursor.execute("""
        UPDATE payment_installments
        SET staff_paid_to_owner_status = ?, staff_paid_to_owner_date = ?
        WHERE id = ?
        """, (new_status, None if new_status != "DA_NOP" else datetime.now().strftime("%Y-%m-%d %H:%M"), installment_id))

    conn.commit()
    conn.close()
    return {"message": "Đã xóa lịch sử nộp tiền và cập nhật lại số dư còn nợ"}

@app.put("/api/staff-payroll/{installment_id}/remit")
def toggle_remit_status(installment_id: int):
    """Cập nhật nhanh trạng thái thợ đã thanh toán hết / hủy nộp cho chủ tiệm"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT pi.staff_paid_to_owner_status, pi.amount, pi.custom_remit_to_owner, COALESCE(pi.staff_remit_type, 'PERCENT'), pi.commission_rate, pi.artist_name 
    FROM payment_installments pi 
    WHERE pi.id = ?
    """, (installment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    current_status = row[0]
    inst_amount = float(row[1] or 0)
    custom_remit = row[2]
    remit_type = str(row[3] or "PERCENT")
    inst_comm = row[4]
    art_name = str(row[5] or "")

    if custom_remit is not None and float(custom_remit) >= 0:
        total_required = round(float(custom_remit), 2)
    elif remit_type == "100":
        total_required = round(inst_amount, 2)
    else:
        if inst_comm is not None and float(inst_comm) > 0:
            staff_pct = float(inst_comm)
        elif art_name:
            cursor.execute("SELECT commission_rate FROM staff WHERE LOWER(name) = LOWER(?)", (art_name.strip(),))
            srow = cursor.fetchone()
            staff_pct = float(srow[0]) if (srow and srow[0]) else 70.0
        else:
            staff_pct = 70.0
        studio_pct = max(0.0, round(100.0 - staff_pct, 2))
        total_required = round(inst_amount * (studio_pct / 100.0), 2)

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM staff_remit_payments WHERE installment_id = ?", (installment_id,))
    current_paid = round(float(cursor.fetchone()[0] or 0), 2)
    remit_remaining = max(0.0, round(total_required - current_paid, 2))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if remit_remaining > 0:
        # Nếu chưa nộp đủ -> Bấm nút này sẽ ghi nhận nộp đủ số tiền còn lại
        cursor.execute("""
        INSERT INTO staff_remit_payments (installment_id, amount, payment_method, payment_date, note)
        VALUES (?, ?, 'TIEN_MAT', ?, 'Thanh toán toàn bộ số còn nợ')
        """, (installment_id, remit_remaining, now_str))
        new_status = "DA_NOP"
        cursor.execute("UPDATE payment_installments SET staff_paid_to_owner_status = 'DA_NOP', staff_paid_to_owner_date = ? WHERE id = ?", (now_str, installment_id))
        msg = f"Đã xác nhận thợ nộp đủ số còn lại {int(remit_remaining):,} đ"
    else:
        # Nếu đã nộp đủ -> Bấm nút này sẽ hoàn tác
        cursor.execute("DELETE FROM staff_remit_payments WHERE installment_id = ?", (installment_id,))
        new_status = "CHUA_NOP"
        cursor.execute("UPDATE payment_installments SET staff_paid_to_owner_status = 'CHUA_NOP', staff_paid_to_owner_date = NULL WHERE id = ?", (installment_id,))
        msg = "Đã hoàn tác trạng thái thợ nộp về Chưa Trả"

    conn.commit()
    conn.close()
    return {
        "message": msg,
        "new_status": new_status,
        "remit_date": now_str if new_status == "DA_NOP" else None
    }

@app.put("/api/staff-payroll/{installment_id}/split-ratio")
def update_installment_split_ratio(installment_id: int, data: InstallmentSplitRatioUpdate):
    """Cập nhật tỷ lệ ăn chia (ví dụ: 70/30, 60/40, 50/50, 40/60, 80/20) hoặc chế độ thu hộ cho từng đợt xăm"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM payment_installments WHERE id = ?", (installment_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    updates = []
    params = []
    if data.commission_rate is not None:
        updates.append("commission_rate = ?")
        params.append(float(data.commission_rate))
    if data.staff_remit_type is not None:
        updates.append("staff_remit_type = ?")
        params.append(str(data.staff_remit_type))
    
    if updates:
        params.append(installment_id)
        cursor.execute(f"UPDATE payment_installments SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()
    return {"message": "Cập nhật tỷ lệ ăn chia thành công"}

@app.put("/api/staff-payroll/{installment_id}/remit-type")
def toggle_remit_type(installment_id: int):
    """Chuyển đổi nhanh giữa Phương án Nộp hoa hồng và Phương án 100% thu hộ cả đơn"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(staff_remit_type, 'PERCENT') FROM payment_installments WHERE id = ?", (installment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt thanh toán")
    
    current = str(row[0])
    new_type = "100" if current != "100" else "PERCENT"
    
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_remit_type = ?
    WHERE id = ?
    """, (new_type, installment_id))
    conn.commit()
    conn.close()
    return {
        "message": f"Đã chuyển sang: {'100% Thu hộ cả đơn' if new_type == '100' else 'Nộp theo tỷ lệ hoa hồng'}",
        "new_type": new_type
    }

@app.post("/api/staff-payroll/payout-all")
def payout_all_pending():
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_payout_status = 'DA_CHI', staff_payout_date = ?
    WHERE COALESCE(staff_payout_status, 'CHUA_CHI') = 'CHUA_CHI'
      AND (
          (artist_name IS NOT NULL AND LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE client_source = 'NHAN_VIEN' OR (LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')))
      )
    """, (now_str,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return {"message": f"Đã thanh toán toàn bộ {count} khoản công cho nhân viên"}

@app.post("/api/staff-payroll/remit-all")
def remit_all_pending():
    """Xác nhận thợ đã nộp hết toàn bộ các khoản còn nợ cho chủ tiệm"""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_paid_to_owner_status = 'DA_NOP', staff_paid_to_owner_date = ?
    WHERE COALESCE(staff_paid_to_owner_status, 'CHUA_NOP') = 'CHUA_NOP'
      AND (
          (artist_name IS NOT NULL AND LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE client_source = 'NHAN_VIEN' OR (LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')))
      )
    """, (now_str,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return {"message": f"Đã xác nhận thợ thanh toán toàn bộ {count} khoản nộp cho chủ tiệm"}

@app.post("/api/staff-payroll/artist/{artist_name}/remit-all")
def remit_artist_all(artist_name: str):
    """Xác nhận riêng một thợ đã nộp toàn bộ các khoản nợ cho chủ tiệm"""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    art_clean = artist_name.strip()
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_paid_to_owner_status = 'DA_NOP', staff_paid_to_owner_date = ?
    WHERE COALESCE(staff_paid_to_owner_status, 'CHUA_NOP') = 'CHUA_NOP'
      AND (
          LOWER(artist_name) = LOWER(?)
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE LOWER(artist_name) = LOWER(?)))
      )
    """, (now_str, art_clean, art_clean))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return {"message": f"Đã xác nhận thợ {art_clean} nộp toàn bộ {count} khoản tiền cho chủ tiệm"}

@app.post("/api/staff-payroll/artist/{artist_name}/payout-all")
def payout_artist_all(artist_name: str):
    """Xác nhận tiệm đã chi trả toàn bộ tiền công cho riêng một thợ"""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    art_clean = artist_name.strip()
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_payout_status = 'DA_CHI', staff_payout_date = ?
    WHERE COALESCE(staff_payout_status, 'CHUA_CHI') = 'CHUA_CHI'
      AND (
          LOWER(artist_name) = LOWER(?)
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE LOWER(artist_name) = LOWER(?)))
      )
    """, (now_str, art_clean, art_clean))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return {"message": f"Đã xác nhận tiệm chi trả toàn bộ {count} khoản công cho thợ {art_clean}"}

@app.post("/api/staff-payroll/artist/{artist_name}/auto-settle")
def auto_settle_artist(artist_name: str):
    """Cấn trừ tự động công nợ hai chiều giữa chủ và thợ:
    Đánh dấu đã chi công và thợ đã nộp đủ phần tiệm để sòng phẳng hoàn toàn"""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    art_clean = artist_name.strip()
    
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_payout_status = 'DA_CHI', staff_payout_date = ?,
        staff_paid_to_owner_status = 'DA_NOP', staff_paid_to_owner_date = ?
    WHERE (COALESCE(staff_payout_status, 'CHUA_CHI') = 'CHUA_CHI' OR COALESCE(staff_paid_to_owner_status, 'CHUA_NOP') = 'CHUA_NOP')
      AND (
          LOWER(artist_name) = LOWER(?)
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE LOWER(artist_name) = LOWER(?)))
      )
    """, (now_str, now_str, art_clean, art_clean))
    payout_count = cursor.rowcount
    
    conn.commit()
    conn.close()
    return {
        "message": f"Đã quyết toán và đối soát sòng phẳng cho thợ {art_clean} ({payout_count} khoản)",
        "payout_count": payout_count
    }

@app.post("/api/staff-payroll/auto-settle-all")
def auto_settle_all():
    """Quyết toán và đối soát sòng phẳng công nợ toàn bộ nhân viên trong 1 chạm"""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    UPDATE payment_installments 
    SET staff_payout_status = 'DA_CHI', staff_payout_date = ?,
        staff_paid_to_owner_status = 'DA_NOP', staff_paid_to_owner_date = ?
    WHERE (COALESCE(staff_payout_status, 'CHUA_CHI') = 'CHUA_CHI' OR COALESCE(staff_paid_to_owner_status, 'CHUA_NOP') = 'CHUA_NOP')
      AND (
          (artist_name IS NOT NULL AND LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')
          OR (artist_name IS NULL AND booking_id IN (SELECT id FROM bookings WHERE client_source = 'NHAN_VIEN' OR (LOWER(artist_name) NOT LIKE '%bắp%' AND LOWER(artist_name) NOT LIKE '%chủ%')))
      )
    """, (now_str, now_str))
    payout_count = cursor.rowcount
    conn.commit()
    conn.close()
    return {
        "message": f"Đã quyết toán sòng phẳng cho toàn bộ nhân viên ({payout_count} khoản)",
        "payout_count": payout_count
    }

@app.put("/api/staff-payroll/{installment_id}/notes")
def update_installment_notes(installment_id: int, data: NoteUpdate):
    """Cập nhật ghi chú cho đợt thanh toán và đồng bộ sang tattoo_sessions nếu có"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE payment_installments SET notes = ? WHERE id = ?", (data.notes, installment_id))
    cursor.execute("SELECT session_id FROM payment_installments WHERE id = ?", (installment_id,))
    row = cursor.fetchone()
    if row and row[0]:
        cursor.execute("UPDATE tattoo_sessions SET notes = ? WHERE id = ?", (data.notes, row[0]))
    conn.commit()
    conn.close()
    return {"message": "Cập nhật ghi chú thành công", "notes": data.notes}

@app.post("/api/staff-payroll/transactions")
def create_staff_transaction(data: StaffTransactionCreate):
    """Ghi nhận giao dịch tự nhập: Thợ nộp tiền, Tiệm trả công, Thợ ứng tiền/Ghi nợ, Thưởng thêm"""
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền giao dịch phải lớn hơn 0")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO staff_debt_transactions (artist_name, transaction_type, amount, payment_method, note)
    VALUES (?, ?, ?, ?, ?)
    """, (data.artist_name.strip(), data.transaction_type, data.amount, data.payment_method or "TIEN_MAT", data.note or ""))
    txn_id = cursor.lastrowid
    conn.commit()
    conn.close()

    type_labels = {
        "STAFF_PAY_OWNER": "Thợ nộp tiền trả chủ",
        "OWNER_PAY_STAFF": "Tiệm trả tiền công cho thợ",
        "ADD_DEBT": "Ghi thêm nợ / Ứng tiền cho thợ",
        "ADD_BONUS": "Cộng thêm công / Tiền thưởng cho thợ"
    }
    label = type_labels.get(data.transaction_type, "Giao dịch")
    return {"message": f"Đã ghi nhận thành công: {label} số tiền {int(data.amount):,} đ", "transaction_id": txn_id}

@app.delete("/api/staff-payroll/transactions/{transaction_id}")
def delete_staff_transaction(transaction_id: int):
    """Xóa một giao dịch tự nhập nếu nhập sai hoặc nhầm lẫn"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM staff_debt_transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa giao dịch thành công"}

@app.put("/api/staff-payroll/{installment_id}/custom-amounts")
def update_installment_custom_amounts(installment_id: int, data: CustomInstallmentAmountUpdate):
    """Tự nhập và lưu số tiền thợ nộp chủ hoặc tiền công thợ trực tiếp trên từng đợt xăm"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE payment_installments 
    SET custom_remit_to_owner = ?, custom_staff_share = ?
    WHERE id = ?
    """, (data.custom_remit_to_owner, data.custom_staff_share, installment_id))
    conn.commit()
    conn.close()
    return {"message": "Đã cập nhật số tiền tự nhập cho đợt xăm thành công"}

# API KHO & NHẬP HÀNG
@app.get("/api/inventory")
def get_inventory():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_items ORDER BY category, item_name")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/inventory")
def create_inventory_item(data: InventoryItemCreate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO inventory_items (item_name, category, unit, stock_quantity, min_threshold)
    VALUES (?, ?, ?, ?, ?)
    """, (data.item_name, data.category, data.unit, data.stock_quantity, data.min_threshold))
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    return {"message": "Thêm vật tư thành công", "id": item_id}

@app.put("/api/inventory/{item_id}")
def update_inventory_item(item_id: int, data: InventoryItemUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy vật tư")
    
    new_name = data.item_name if data.item_name is not None else item["item_name"]
    new_category = data.category if data.category is not None else item["category"]
    new_unit = data.unit if data.unit is not None else item["unit"]
    new_stock = data.stock_quantity if data.stock_quantity is not None else item["stock_quantity"]
    new_min = data.min_threshold if data.min_threshold is not None else item["min_threshold"]
    
    cursor.execute("""
    UPDATE inventory_items SET
        item_name = ?,
        category = ?,
        unit = ?,
        stock_quantity = ?,
        min_threshold = ?
    WHERE id = ?
    """, (new_name, new_category, new_unit, new_stock, new_min, item_id))
    conn.commit()
    conn.close()
    return {"message": "Cập nhật vật tư thành công"}

@app.delete("/api/inventory/{item_id}")
def delete_inventory_item(item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa vật tư thành công"}

@app.get("/api/purchases")
def get_purchases(payer: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    query = """
    SELECT p.*, 
           (SELECT COUNT(*) FROM purchase_order_items WHERE order_id = p.id) as item_count
    FROM purchase_orders p 
    """
    params = []
    if payer and payer != "ALL":
        query += " WHERE p.payer_name = ?"
        params.append(payer)
    query += " ORDER BY p.purchase_date DESC, p.id DESC"
    cursor.execute(query, params)
    orders = [dict(row) for row in cursor.fetchall()]
    
    # Lấy kèm chi tiết items
    for order in orders:
        cursor.execute("SELECT * FROM purchase_order_items WHERE order_id = ?", (order["id"],))
        order["items"] = [dict(r) for r in cursor.fetchall()]
        
    conn.close()
    return orders

@app.get("/api/purchases/stats")
def get_purchase_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(total_cost), 0) FROM purchase_orders")
    total_cost = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT COUNT(*) FROM purchase_orders")
    batch_count = cursor.fetchone()[0] or 0

    cursor.execute("SELECT payer_name, COALESCE(SUM(total_cost), 0) as total FROM purchase_orders GROUP BY payer_name")
    by_payer = {row["payer_name"]: row["total"] for row in cursor.fetchall()}

    conn.close()
    return {
        "total_cost": total_cost,
        "batch_count": batch_count,
        "by_payer": by_payer
    }

@app.post("/api/purchases")
def create_purchase(data: PurchaseOrderCreate):
    conn = get_db()
    cursor = conn.cursor()
    
    total_cost = 0
    order_items_to_insert = []
    
    for item in data.items:
        item_id = item.item_id
        item_name = (item.item_name or "").strip()
        
        if item_id:
            cursor.execute("SELECT item_name FROM inventory_items WHERE id = ?", (item_id,))
            inv_item = cursor.fetchone()
            if inv_item:
                item_name = inv_item["item_name"]
        
        if not item_name and not item_id:
            continue
            
        if not item_id and item_name:
            # Tự động thêm vào danh mục vật tư nếu chưa có
            cursor.execute("SELECT id FROM inventory_items WHERE LOWER(item_name) = LOWER(?)", (item_name,))
            existing = cursor.fetchone()
            if existing:
                item_id = existing[0]
            else:
                cursor.execute("""
                INSERT INTO inventory_items (item_name, category, unit, stock_quantity, min_threshold)
                VALUES (?, 'VẬT TƯ TIÊU HAO', 'cái/hộp', 0, 5)
                """, (item_name,))
                item_id = cursor.lastrowid

        qty = max(1.0, float(item.quantity or 1))
        price = max(0.0, float(item.unit_price or 0))
        item_total = qty * price
        total_cost += item_total
        order_items_to_insert.append((item_id, item_name, qty, price, item_total))
        
    batch_name = data.batch_name.strip() if data.batch_name else f"Đợt nhập ngày {data.purchase_date}"
    payer_name = data.payer_name.strip() if data.payer_name else "QUỸ TIỆM"
    supplier = data.supplier_name.strip() if data.supplier_name else "Tiệm Đồ Xăm"

    # Tạo phiếu / đợt nhập
    cursor.execute("""
    INSERT INTO purchase_orders (batch_name, payer_name, supplier_name, purchase_date, total_cost, payment_status, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (batch_name, payer_name, supplier, data.purchase_date, total_cost, data.payment_status, data.notes or ""))
    order_id = cursor.lastrowid
    
    # Thêm chi tiết và cộng kho
    for o_item in order_items_to_insert:
        cursor.execute("""
        INSERT INTO purchase_order_items (order_id, item_id, item_name, quantity, unit_price, total_item_cost)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, o_item[0], o_item[1], o_item[2], o_item[3], o_item[4]))
        
        if o_item[0]:
            cursor.execute("""
            UPDATE inventory_items 
            SET stock_quantity = stock_quantity + ?
            WHERE id = ?
            """, (o_item[2], o_item[0]))
        
    conn.commit()
    conn.close()
    return {"message": "Tạo đợt nhập hàng thành công", "order_id": order_id, "total_cost": total_cost}

@app.get("/api/purchases/{order_id}")
def get_purchase_order(order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt nhập hàng")
    res = dict(order)
    cursor.execute("SELECT * FROM purchase_order_items WHERE order_id = ?", (order_id,))
    res["items"] = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return res

@app.put("/api/purchases/{order_id}")
def update_purchase_order(order_id: int, data: PurchaseOrderCreate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt nhập hàng")

    total_cost = 0
    order_items_to_insert = []
    
    for item in data.items:
        item_id = item.item_id
        item_name = (item.item_name or "").strip()
        
        if item_id:
            cursor.execute("SELECT item_name FROM inventory_items WHERE id = ?", (item_id,))
            inv_item = cursor.fetchone()
            if inv_item:
                item_name = inv_item["item_name"]
        
        if not item_name and not item_id:
            continue
            
        if not item_id and item_name:
            cursor.execute("SELECT id FROM inventory_items WHERE LOWER(item_name) = LOWER(?)", (item_name,))
            existing = cursor.fetchone()
            if existing:
                item_id = existing[0]
            else:
                cursor.execute("""
                INSERT INTO inventory_items (item_name, category, unit, stock_quantity, min_threshold)
                VALUES (?, 'VẬT TƯ TIÊU HAO', 'cái/hộp', 0, 5)
                """, (item_name,))
                item_id = cursor.lastrowid

        qty = max(1.0, float(item.quantity or 1))
        price = max(0.0, float(item.unit_price or 0))
        item_total = qty * price
        total_cost += item_total
        order_items_to_insert.append((item_id, item_name, qty, price, item_total))

    batch_name = data.batch_name.strip() if data.batch_name else f"Đợt nhập ngày {data.purchase_date}"
    payer_name = data.payer_name.strip() if data.payer_name else "QUỸ TIỆM"
    supplier = data.supplier_name.strip() if data.supplier_name else "Tiệm Đồ Xăm"

    cursor.execute("""
    UPDATE purchase_orders SET
        batch_name = ?,
        payer_name = ?,
        supplier_name = ?,
        purchase_date = ?,
        total_cost = ?,
        payment_status = ?,
        notes = ?
    WHERE id = ?
    """, (batch_name, payer_name, supplier, data.purchase_date, total_cost, data.payment_status, data.notes or "", order_id))

    cursor.execute("DELETE FROM purchase_order_items WHERE order_id = ?", (order_id,))
    for o_item in order_items_to_insert:
        cursor.execute("""
        INSERT INTO purchase_order_items (order_id, item_id, item_name, quantity, unit_price, total_item_cost)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, o_item[0], o_item[1], o_item[2], o_item[3], o_item[4]))

    conn.commit()
    conn.close()
    return {"message": "Cập nhật đợt nhập hàng thành công", "order_id": order_id, "total_cost": total_cost}

@app.delete("/api/purchases/{order_id}")
def delete_purchase_order(order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu nhập")
        
    # Hoàn nguyên tồn kho của các mặt hàng trong phiếu nhập
    cursor.execute("SELECT item_id, quantity FROM purchase_order_items WHERE order_id = ?", (order_id,))
    items = cursor.fetchall()
    for it in items:
        cursor.execute("UPDATE inventory_items SET stock_quantity = MAX(0, stock_quantity - ?) WHERE id = ?", (it["quantity"], it["item_id"]))
        
    cursor.execute("DELETE FROM purchase_order_items WHERE order_id = ?", (order_id,))
    cursor.execute("DELETE FROM purchase_orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa phiếu nhập và hoàn nguyên tồn kho"}

# API KHÁCH HÀNG (CRM)
class CustomerProfileUpdate(BaseModel):
    phone: str
    name: str
    dob: Optional[str] = ""
    health_notes: Optional[str] = ""
    style_preference: Optional[str] = ""
    notes: Optional[str] = ""

@app.get("/api/customers")
def get_customers(search: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Lấy thông tin thống kê từ bookings: Phân biệt rõ khách có số điện thoại vs khách không có SĐT
    cursor.execute("""
    SELECT 
        CASE 
            WHEN TRIM(COALESCE(b.customer_phone, '')) != '' THEN TRIM(b.customer_phone)
            ELSE 'KH_NAME_' || LOWER(TRIM(b.customer_name))
        END as customer_key,
        COALESCE(NULLIF(TRIM(b.customer_phone), ''), '') as phone,
        b.customer_name as name,
        COUNT(b.id) as total_bookings,
        COALESCE(SUM(b.amount_paid), 0) as total_spent,
        COALESCE(SUM(b.total_price), 0) as total_value,
        COALESCE(SUM(CASE WHEN b.payment_status != 'DA_TAT_TOAN' THEN (b.total_price - b.amount_paid) ELSE 0 END), 0) as total_debt,
        MAX(b.appointment_date) as last_visit,
        MIN(b.appointment_date) as first_visit
    FROM bookings b
    GROUP BY customer_key
    """)
    booking_map = {row["customer_key"]: dict(row) for row in cursor.fetchall()}
    
    # Lấy thông tin profile phụ
    cursor.execute("SELECT * FROM customer_profiles")
    profiles = {row["phone"]: dict(row) for row in cursor.fetchall()}
    
    # Gom danh sách tất cả các key duy nhất từ cả 2 nguồn
    all_keys = set(list(booking_map.keys()))
    for p_phone in profiles.keys():
        if p_phone and p_phone.strip():
            all_keys.add(p_phone.strip())
            
    result = []
    for key in all_keys:
        b_info = booking_map.get(key, {})
        phone = b_info.get("phone", "")
        prof = profiles.get(phone, {}) if phone else profiles.get(key, {})
        
        name = prof.get("name") or b_info.get("name") or "Khách hàng"
        total_bookings = b_info.get("total_bookings", 0)
        total_spent = b_info.get("total_spent", 0.0)
        total_value = b_info.get("total_value", 0.0)
        total_debt = b_info.get("total_debt", 0.0)
        last_visit = b_info.get("last_visit", "")
        first_visit = b_info.get("first_visit", "")

        # Tìm thợ quen & Lịch sử xăm chuẩn xác theo SĐT hoặc theo Tên nếu không có SĐT
        if phone:
            cursor.execute("""
            SELECT artist_name, COUNT(*) as cnt 
            FROM bookings 
            WHERE customer_phone = ? 
            GROUP BY artist_name 
            ORDER BY cnt DESC LIMIT 1
            """, (phone,))
            fav_artist_row = cursor.fetchone()
            
            cursor.execute("""
            SELECT id, service_desc, artist_name, appointment_date, total_price, deposit_amount, deposit_status, amount_paid, payment_status 
            FROM bookings 
            WHERE customer_phone = ? 
            ORDER BY id DESC
            """, (phone,))
            history = [dict(r) for r in cursor.fetchall()]
        else:
            cursor.execute("""
            SELECT artist_name, COUNT(*) as cnt 
            FROM bookings 
            WHERE (customer_phone IS NULL OR TRIM(customer_phone) = '') AND LOWER(TRIM(customer_name)) = LOWER(TRIM(?))
            GROUP BY artist_name 
            ORDER BY cnt DESC LIMIT 1
            """, (name,))
            fav_artist_row = cursor.fetchone()
            
            cursor.execute("""
            SELECT id, service_desc, artist_name, appointment_date, total_price, deposit_amount, deposit_status, amount_paid, payment_status 
            FROM bookings 
            WHERE (customer_phone IS NULL OR TRIM(customer_phone) = '') AND LOWER(TRIM(customer_name)) = LOWER(TRIM(?))
            ORDER BY id DESC
            """, (name,))
            history = [dict(r) for r in cursor.fetchall()]

        fav_artist = fav_artist_row[0] if fav_artist_row else "Chưa rõ"

        for h in history:
            cursor.execute("SELECT * FROM payment_installments WHERE booking_id = ? ORDER BY id ASC", (h["id"],))
            h["installments"] = [dict(ins) for ins in cursor.fetchall()]

        # Phân hạng khách hàng (Segment)
        if total_spent >= 10000000 or total_bookings >= 3:
            segment = "VIP"
        elif total_bookings >= 2:
            segment = "THAN_THIET"
        else:
            segment = "KHACH_MOI"

        item = {
            "customer_key": key,
            "phone": phone,
            "name": name,
            "dob": prof.get("dob", ""),
            "health_notes": prof.get("health_notes", ""),
            "style_preference": prof.get("style_preference", ""),
            "notes": prof.get("notes", ""),
            "total_bookings": total_bookings,
            "total_spent": total_spent,
            "total_value": total_value,
            "total_debt": total_debt,
            "last_visit": last_visit,
            "first_visit": first_visit,
            "fav_artist": fav_artist,
            "segment": segment,
            "history": history
        }

        if search:
            s = search.lower()
            if s in item["name"].lower() or s in item["phone"].lower() or s in item["fav_artist"].lower():
                result.append(item)
        else:
            result.append(item)

    # Sắp xếp theo tổng tiền chi tiêu giảm dần
    result.sort(key=lambda x: x["total_spent"], reverse=True)
    conn.close()
    return result

@app.post("/api/customers")
def create_customer(data: CustomerCreate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT phone FROM customer_profiles WHERE phone = ?", (data.phone.strip(),))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Số điện thoại khách hàng này đã tồn tại")
    
    cursor.execute("""
    INSERT INTO customer_profiles (phone, name, dob, health_notes, style_preference, notes)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (data.phone.strip(), data.name.strip(), data.dob or "", data.health_notes or "", data.style_preference or "", data.notes or ""))
    conn.commit()
    conn.close()
    return {"message": "Thêm khách hàng thành công"}

@app.put("/api/customers/{phone}")
def update_customer(phone: str, data: CustomerUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customer_profiles WHERE phone = ?", (phone,))
    prof = cursor.fetchone()
    
    if not prof:
        cursor.execute("SELECT customer_name FROM bookings WHERE customer_phone = ? LIMIT 1", (phone,))
        bk = cursor.fetchone()
        curr_name = bk[0] if bk else ""
        cursor.execute("INSERT OR IGNORE INTO customer_profiles (phone, name) VALUES (?, ?)", (phone, curr_name))
        cursor.execute("SELECT * FROM customer_profiles WHERE phone = ?", (phone,))
        prof = cursor.fetchone()

    new_name = data.name.strip() if data.name is not None else (prof["name"] if prof else "")
    new_dob = data.dob if data.dob is not None else (prof["dob"] if prof else "")
    new_health = data.health_notes if data.health_notes is not None else (prof["health_notes"] if prof else "")
    new_style = data.style_preference if data.style_preference is not None else (prof["style_preference"] if prof else "")
    new_notes = data.notes if data.notes is not None else (prof["notes"] if prof else "")

    cursor.execute("""
    UPDATE customer_profiles SET
        name = ?,
        dob = ?,
        health_notes = ?,
        style_preference = ?,
        notes = ?
    WHERE phone = ?
    """, (new_name, new_dob, new_health, new_style, new_notes, phone))

    if new_name:
        cursor.execute("UPDATE bookings SET customer_name = ? WHERE customer_phone = ?", (new_name, phone))
        cursor.execute("UPDATE tattoo_sessions SET customer_name = ? WHERE customer_phone = ?", (new_name, phone))

    conn.commit()
    conn.close()
    return {"message": "Cập nhật thông tin khách hàng thành công"}

@app.delete("/api/customers/{phone}")
def delete_customer(phone: str, name: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    clean_p = phone.strip() if phone else ""
    if clean_p.startswith("KH_NAME_") or clean_p == "NO_PHONE" or not clean_p:
        cust_name = name or (clean_p.replace("KH_NAME_", "") if clean_p.startswith("KH_NAME_") else "")
        if not cust_name:
            conn.close()
            raise HTTPException(status_code=400, detail="Không có thông tin xác định khách hàng")
        cursor.execute("DELETE FROM customer_profiles WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))", (cust_name,))
        cursor.execute("DELETE FROM bookings WHERE (customer_phone IS NULL OR TRIM(customer_phone) = '') AND LOWER(TRIM(customer_name)) = LOWER(TRIM(?))", (cust_name,))
        cursor.execute("DELETE FROM tattoo_sessions WHERE (customer_phone IS NULL OR TRIM(customer_phone) = '') AND LOWER(TRIM(customer_name)) = LOWER(TRIM(?))", (cust_name,))
    else:
        cursor.execute("DELETE FROM customer_profiles WHERE phone = ?", (clean_p,))
        cursor.execute("DELETE FROM bookings WHERE customer_phone = ?", (clean_p,))
        cursor.execute("DELETE FROM tattoo_sessions WHERE customer_phone = ?", (clean_p,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa khách hàng và toàn bộ lịch sử liên quan"}

@app.post("/api/customers/profile")
def update_customer_profile(data: CustomerProfileUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO customer_profiles (phone, name, dob, health_notes, style_preference, notes)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(phone) DO UPDATE SET
        name = excluded.name,
        dob = excluded.dob,
        health_notes = excluded.health_notes,
        style_preference = excluded.style_preference,
        notes = excluded.notes
    """, (data.phone, data.name, data.dob, data.health_notes, data.style_preference, data.notes))
    conn.commit()
    conn.close()
    return {"message": "Đã lưu hồ sơ khách hàng thành công"}


# ==========================================
# NHẬT KÝ KHÁCH XĂM (TATTOO LOG) & CHÍNH SÁCH BẢO HÀNH (+20% QUÁ 5 THÁNG)
# ==========================================

class TattooLogCreate(BaseModel):
    customer_phone: str
    customer_name: str
    booking_id: Optional[int] = None
    session_id: Optional[int] = None
    tattoo_title: str
    completed_date: str # YYYY-MM-DD
    base_touchup_price: Optional[float] = 0
    warranty_months: Optional[int] = 5
    overdue_penalty_rate: Optional[float] = 0.20
    status: Optional[str] = "COMPLETED"
    notes: Optional[str] = ""

class TattooLogUpdate(BaseModel):
    tattoo_title: Optional[str] = None
    completed_date: Optional[str] = None
    base_touchup_price: Optional[float] = None
    warranty_months: Optional[int] = None
    overdue_penalty_rate: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None

def compute_tattoo_warranty(completed_date_str: str, warranty_months: int = 5, overdue_penalty_rate: float = 0.20, base_price: float = 0):
    try:
        clean_date_str = str(completed_date_str).strip().split(" ")[0]
        c_date = datetime.strptime(clean_date_str, "%Y-%m-%d").date()
    except Exception:
        c_date = date.today()
        clean_date_str = c_date.strftime("%Y-%m-%d")

    today = date.today()
    days_elapsed = (today - c_date).days
    if days_elapsed < 0:
        days_elapsed = 0
    
    months_elapsed = round(days_elapsed / 30.4375, 1)
    warranty_limit_days = int(warranty_months * 30.4375) # 5 * 30.4375 = 152 ngày
    is_overdue = days_elapsed > warranty_limit_days
    days_remaining = max(0, warranty_limit_days - days_elapsed)
    
    m = int(days_elapsed // 30.4375)
    d = int(round(days_elapsed % 30.4375))
    if m > 0:
        time_desc = f"{m} tháng{f' {d} ngày' if d > 0 else ''}"
    else:
        time_desc = f"{days_elapsed} ngày"

    penalty_amount = round(base_price * overdue_penalty_rate) if is_overdue else 0
    total_suggested_price = base_price + penalty_amount

    return {
        "completed_date": clean_date_str,
        "days_elapsed": days_elapsed,
        "months_elapsed": months_elapsed,
        "time_desc": time_desc,
        "warranty_months": warranty_months,
        "warranty_limit_days": warranty_limit_days,
        "is_overdue": is_overdue,
        "days_remaining": days_remaining,
        "penalty_rate": overdue_penalty_rate,
        "penalty_amount": penalty_amount,
        "base_price": base_price,
        "total_suggested_price": total_suggested_price
    }

def sync_session_to_tattoo_log(cursor, session_id: int):
    try:
        cursor.execute("SELECT * FROM tattoo_sessions WHERE id = ?", (session_id,))
        s = cursor.fetchone()
        if not s or s["session_status"] != "HOAN_THANH":
            return
        c_date = (s["appointment_date"] or "").split(" ")[0]
        if not c_date:
            c_date = date.today().strftime("%Y-%m-%d")
        
        cursor.execute("SELECT id FROM tattoo_logs WHERE session_id = ?", (session_id,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE tattoo_logs SET
                    customer_phone = ?, customer_name = ?, tattoo_title = ?, completed_date = ?, base_touchup_price = ?
                WHERE id = ?
            """, (s["customer_phone"], s["customer_name"], s["stage_name"] or s["service_desc"] or "Hình xăm", c_date, s["session_price"], existing["id"]))
        else:
            cursor.execute("""
                INSERT INTO tattoo_logs (
                    customer_phone, customer_name, booking_id, session_id,
                    tattoo_title, completed_date, base_touchup_price,
                    warranty_months, overdue_penalty_rate, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 5, 0.20, 'COMPLETED')
            """, (s["customer_phone"], s["customer_name"], s["booking_id"], session_id, s["stage_name"] or s["service_desc"] or "Hình xăm", c_date, s["session_price"]))
    except Exception as e:
        print(f"Error syncing session to tattoo_log: {e}")

@app.get("/api/tattoo-logs")
def get_tattoo_logs(customer_phone: Optional[str] = None, search: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT * FROM tattoo_logs WHERE 1=1"
    params = []
    if customer_phone:
        query += " AND customer_phone = ?"
        params.append(customer_phone.strip())
    if search:
        query += " AND (customer_name LIKE ? OR customer_phone LIKE ? OR tattoo_title LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    query += " ORDER BY completed_date DESC, id DESC"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    result = []
    for r in rows:
        calc = compute_tattoo_warranty(
            r.get("completed_date", ""),
            r.get("warranty_months") or 5,
            r.get("overdue_penalty_rate") or 0.20,
            r.get("base_touchup_price") or 0
        )
        r.update(calc)
        result.append(r)
    return result

@app.get("/api/tattoo-logs/check-warranty")
def check_tattoo_warranty(phone: Optional[str] = None, name: Optional[str] = None, booking_id: Optional[int] = None):
    phone = (phone or "").strip()
    name = (name or "").strip()
    conn = get_db()
    cursor = conn.cursor()

    # 1. Tìm trong tattoo_logs trước
    log_row = None
    if booking_id:
        cursor.execute("SELECT * FROM tattoo_logs WHERE booking_id = ? ORDER BY completed_date DESC LIMIT 1", (booking_id,))
        log_row = cursor.fetchone()
    if not log_row and phone:
        cursor.execute("SELECT * FROM tattoo_logs WHERE customer_phone = ? ORDER BY completed_date DESC LIMIT 1", (phone,))
        log_row = cursor.fetchone()
    if not log_row and name:
        cursor.execute("SELECT * FROM tattoo_logs WHERE LOWER(customer_name) = ? ORDER BY completed_date DESC LIMIT 1", (name.lower(),))
        log_row = cursor.fetchone()

    if log_row:
        d = dict(log_row)
        calc = compute_tattoo_warranty(
            d.get("completed_date", ""),
            d.get("warranty_months") or 5,
            d.get("overdue_penalty_rate") or 0.20,
            d.get("base_touchup_price") or 0
        )
        d.update(calc)
        d["found"] = True
        conn.close()
        return d

    # 2. Nếu chưa có trong tattoo_logs, tìm trong tattoo_sessions đã HOAN_THANH hoặc gần nhất
    sess_row = None
    if booking_id:
        cursor.execute("SELECT * FROM tattoo_sessions WHERE booking_id = ? AND session_status = 'HOAN_THANH' ORDER BY appointment_date DESC LIMIT 1", (booking_id,))
        sess_row = cursor.fetchone()
        if not sess_row:
            cursor.execute("SELECT * FROM tattoo_sessions WHERE booking_id = ? ORDER BY appointment_date DESC LIMIT 1", (booking_id,))
            sess_row = cursor.fetchone()
    if not sess_row and phone:
        cursor.execute("SELECT * FROM tattoo_sessions WHERE customer_phone = ? AND session_status = 'HOAN_THANH' ORDER BY appointment_date DESC LIMIT 1", (phone,))
        sess_row = cursor.fetchone()
        if not sess_row:
            cursor.execute("SELECT * FROM tattoo_sessions WHERE customer_phone = ? ORDER BY appointment_date DESC LIMIT 1", (phone,))
            sess_row = cursor.fetchone()
    if not sess_row and name:
        cursor.execute("SELECT * FROM tattoo_sessions WHERE LOWER(customer_name) = ? AND session_status = 'HOAN_THANH' ORDER BY appointment_date DESC LIMIT 1", (name.lower(),))
        sess_row = cursor.fetchone()
        if not sess_row:
            cursor.execute("SELECT * FROM tattoo_sessions WHERE LOWER(customer_name) = ? ORDER BY appointment_date DESC LIMIT 1", (name.lower(),))
            sess_row = cursor.fetchone()

    if sess_row:
        s = dict(sess_row)
        date_str = (s.get("appointment_date") or "").split(" ")[0]
        calc = compute_tattoo_warranty(date_str, 5, 0.20, s.get("session_price") or 0)
        res = {
            "found": True,
            "customer_phone": s.get("customer_phone", ""),
            "customer_name": s.get("customer_name", ""),
            "booking_id": s.get("booking_id"),
            "session_id": s.get("id"),
            "tattoo_title": s.get("stage_name") or s.get("service_desc") or "Hình xăm",
            "notes": s.get("notes") or ""
        }
        res.update(calc)
        conn.close()
        return res

    # 3. Tìm trong bookings
    bk_row = None
    if booking_id:
        cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
        bk_row = cursor.fetchone()
    if not bk_row and phone:
        cursor.execute("SELECT * FROM bookings WHERE customer_phone = ? ORDER BY appointment_date DESC LIMIT 1", (phone,))
        bk_row = cursor.fetchone()
    if not bk_row and name:
        cursor.execute("SELECT * FROM bookings WHERE LOWER(customer_name) = ? ORDER BY appointment_date DESC LIMIT 1", (name.lower(),))
        bk_row = cursor.fetchone()

    conn.close()
    if bk_row:
        b = dict(bk_row)
        date_str = (b.get("appointment_date") or "").split(" ")[0]
        calc = compute_tattoo_warranty(date_str, 5, 0.20, (b.get("total_price") or 0) / 3)
        res = {
            "found": True,
            "customer_phone": b.get("customer_phone", ""),
            "customer_name": b.get("customer_name", ""),
            "booking_id": b.get("id"),
            "session_id": None,
            "tattoo_title": b.get("service_desc") or "Hình xăm",
            "notes": b.get("notes") or ""
        }
        res.update(calc)
        return res

    return {"found": False, "message": "Chưa có thông tin ngày xăm trước của khách hàng này."}

@app.post("/api/tattoo-logs")
def create_or_sync_tattoo_log(data: TattooLogCreate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tattoo_logs (
        customer_phone, customer_name, booking_id, session_id,
        tattoo_title, completed_date, base_touchup_price,
        warranty_months, overdue_penalty_rate, status, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.customer_phone.strip(),
        data.customer_name.strip(),
        data.booking_id,
        data.session_id,
        data.tattoo_title.strip(),
        data.completed_date.strip().split(" ")[0],
        data.base_touchup_price or 0,
        data.warranty_months or 5,
        data.overdue_penalty_rate or 0.20,
        data.status or "COMPLETED",
        data.notes or ""
    ))
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return {"message": "Đã ghi nhận nhật ký xăm thành công", "id": log_id}

@app.delete("/api/tattoo-logs/{log_id}")
def delete_tattoo_log(log_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tattoo_logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa nhật ký hình xăm"}


# API DOANH THU & BÁO CÁO TỔNG QUAN NÂNG CẤP (BỘ LỌC THỜI GIAN, XEM LỊCH THEO NGÀY, ĂN CHIA 70/30)
@app.get("/api/dashboard")
def get_dashboard(
    period: Optional[str] = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    conn = get_db()
    cursor = conn.cursor()
    
    # 0. Lấy toàn bộ lịch sử thanh toán (payment_installments) để xây dựng bản đồ Lịch Doanh Thu Tổng Thể (Calendar Summary)
    cursor.execute("""
    SELECT 
        p.id as installment_id,
        p.booking_id,
        p.session_id,
        p.amount,
        p.payment_date,
        p.payment_method,
        p.installment_name,
        COALESCE(p.artist_name, b.artist_name, 'Chủ Tiệm') as artist_name,
        COALESCE(b.customer_name, 'Khách hàng') as customer_name,
        COALESCE(b.customer_phone, '') as customer_phone,
        COALESCE(b.service_desc, 'Hình xăm') as service_desc,
        COALESCE(b.total_price, p.amount) as total_price,
        COALESCE(b.client_source, 'TIEM') as client_source,
        b.payment_status as booking_payment_status
    FROM payment_installments p
    INNER JOIN bookings b ON p.booking_id = b.id
    ORDER BY p.payment_date ASC, p.id ASC
    """)
    all_raw_payments = [dict(row) for row in cursor.fetchall()]

    daily_calendar = {}
    for p in all_raw_payments:
        raw_date = (p.get("payment_date") or "").strip()
        day_key = raw_date[:10] if len(raw_date) >= 10 else datetime.now().strftime("%Y-%m-%d")
        if not day_key:
            continue
        
        amt = float(p.get("amount") or 0)
        art = (p.get("artist_name") or "Chủ Tiệm").strip()
        
        if day_key not in daily_calendar:
            daily_calendar[day_key] = {
                "date": day_key,
                "revenue": 0.0,
                "jobs_count": 0,
                "artists": set(),
                "bookings": []
            }
        
        daily_calendar[day_key]["revenue"] += amt
        daily_calendar[day_key]["jobs_count"] += 1
        daily_calendar[day_key]["artists"].add(art)
        daily_calendar[day_key]["bookings"].append({
            "id": p.get("booking_id") or p["installment_id"],
            "installment_id": p["installment_id"],
            "customer_name": p.get("customer_name", ""),
            "customer_phone": p.get("customer_phone", ""),
            "service_desc": f"{p.get('service_desc', '')} ({p.get('installment_name', '')})".strip(),
            "artist_name": art,
            "appointment_date": p.get("payment_date", ""),
            "amount_paid": amt,
            "total_price": float(p.get("total_price") or 0),
            "payment_status": "DA_THANH_TOAN"
        })

    # Chuyển sets thành list cho JSON serialization
    calendar_export = {}
    for d, item in daily_calendar.items():
        calendar_export[d] = {
            "date": item["date"],
            "revenue": item["revenue"],
            "jobs_count": item["jobs_count"],
            "artists": list(item["artists"]),
            "bookings": item["bookings"]
        }

    # 1. Xác định mốc thời gian lọc cho Dashboard theo ngày thực thu
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=(6 - now.weekday()))).strftime("%Y-%m-%d")
    month_start = now.strftime("%Y-%m-01")
    year_start = now.strftime("%Y-01-01")
    
    date_where_inst = ""
    date_where_purchase = ""
    params_inst = []
    params_purchase = []
    
    if start_date and end_date:
        date_where_inst = "WHERE substr(p.payment_date, 1, 10) BETWEEN ? AND ?"
        params_inst = [start_date, end_date]
        date_where_purchase = "WHERE substr(created_at, 1, 10) BETWEEN ? AND ?"
        params_purchase = [start_date, end_date]
    elif period == "today":
        date_where_inst = "WHERE substr(p.payment_date, 1, 10) = ?"
        params_inst = [today_str]
        date_where_purchase = "WHERE substr(created_at, 1, 10) = ?"
        params_purchase = [today_str]
    elif period == "week":
        date_where_inst = "WHERE substr(p.payment_date, 1, 10) BETWEEN ? AND ?"
        params_inst = [week_start, week_end]
        date_where_purchase = "WHERE substr(created_at, 1, 10) BETWEEN ? AND ?"
        params_purchase = [week_start, week_end]
    elif period == "month":
        date_where_inst = "WHERE substr(p.payment_date, 1, 7) = ?"
        params_inst = [now.strftime("%Y-%m")]
        date_where_purchase = "WHERE substr(created_at, 1, 7) = ?"
        params_purchase = [now.strftime("%Y-%m")]
    elif period == "year":
        date_where_inst = "WHERE substr(p.payment_date, 1, 4) = ?"
        params_inst = [now.strftime("%Y")]
        date_where_purchase = "WHERE substr(created_at, 1, 4) = ?"
        params_purchase = [now.strftime("%Y")]

    # 2. Lấy danh sách các khoản thanh toán thực tế thỏa mãn kỳ lọc
    query_payments = f"""
    SELECT 
        p.id as installment_id,
        p.booking_id,
        p.session_id,
        p.amount,
        p.payment_date,
        p.payment_method,
        p.installment_name,
        COALESCE(p.artist_name, b.artist_name, 'Chủ Tiệm') as artist_name,
        COALESCE(b.customer_name, 'Khách hàng') as customer_name,
        COALESCE(b.customer_phone, '') as customer_phone,
        COALESCE(b.service_desc, 'Hình xăm') as service_desc,
        COALESCE(b.total_price, p.amount) as total_price,
        COALESCE(b.client_source, 'TIEM') as client_source
    FROM payment_installments p
    INNER JOIN bookings b ON p.booking_id = b.id
    {date_where_inst}
    ORDER BY p.payment_date DESC, p.id DESC
    """
    cursor.execute(query_payments, params_inst)
    filtered_payments = [dict(row) for row in cursor.fetchall()]
    
    # 3. Chi nhập hàng trong kỳ
    query_purchases = f"SELECT COALESCE(SUM(total_cost), 0) FROM purchase_orders {date_where_purchase}"
    cursor.execute(query_purchases, params_purchase)
    total_purchase_cost = cursor.fetchone()[0]

    # 4. Lấy thông tin thợ từ staff để map avatar và theme_color
    cursor.execute("SELECT id, name, role, avatar_url, theme_color, is_active, commission_rate FROM staff")
    staff_map = {row["name"].strip().lower(): dict(row) for row in cursor.fetchall()}

    # 5. Tính toán các chỉ số tài chính từ các khoản thực thu trong kỳ
    total_revenue = 0.0          # Thực thu đã thu từ khách trong kỳ
    total_contract_value = 0.0   # Giá trị hợp đồng của các giao dịch trong kỳ
    total_deposit = 0.0          # Tổng cọc đã nhận trong kỳ
    deposit_count = 0
    
    total_staff_commission = 0.0 # Tiền công nhân viên
    total_studio_from_staff = 0.0# Tiệm giữ từ khách nhân viên
    total_studio_own_revenue = 0.0# 100% doanh thu khách của tiệm

    # Gom nhóm theo thợ
    artist_dict = {}

    # Thống kê hình thức thanh toán
    payment_methods = {"CHUYEN_KHOAN": 0.0, "TIEN_MAT": 0.0, "THE": 0.0}
    # Thống kê top dịch vụ
    service_stats = {}

    for p in filtered_payments:
        amount_paid = float(p.get("amount") or 0)
        total_price = float(p.get("total_price") or amount_paid)
        art_name = (p.get("artist_name") or "Chủ Tiệm").strip()
        method = p.get("payment_method") or "CHUYEN_KHOAN"
        service = p.get("service_desc") or "Hình xăm"
        inst_name = (p.get("installment_name") or "").lower()

        total_revenue += amount_paid
        total_contract_value += total_price

        if "cọc" in inst_name:
            total_deposit += amount_paid
            deposit_count += 1

        if method in payment_methods:
            payment_methods[method] += amount_paid
        else:
            payment_methods["CHUYEN_KHOAN"] += amount_paid

        # Top dịch vụ
        if service not in service_stats:
            service_stats[service] = {"count": 0, "revenue": 0.0}
        service_stats[service]["count"] += 1
        service_stats[service]["revenue"] += amount_paid

        # Phân chia thợ vs tiệm
        st_info = staff_map.get(art_name.lower(), {})
        is_owner = is_owner_name(art_name, staff_map)

        if not is_owner:
            staff_pct = float(st_info.get("commission_rate") or 70.0)
            studio_pct = max(0.0, round(100.0 - staff_pct, 2))
            staff_cut = round(amount_paid * (staff_pct / 100.0), 2)
            studio_cut = round(amount_paid * (studio_pct / 100.0), 2)
            total_staff_commission += staff_cut
            total_studio_from_staff += studio_cut
        else:
            staff_cut = 0.0
            studio_cut = amount_paid
            total_studio_own_revenue += amount_paid

        # Gom nhóm thợ
        if art_name not in artist_dict:
            artist_dict[art_name] = {
                "artist_name": art_name,
                "role": st_info.get("role", "Thợ xăm"),
                "avatar_url": st_info.get("avatar_url", ""),
                "theme_color": st_info.get("theme_color", "amber" if is_owner else "emerald"),
                "total_jobs": 0,
                "total_value": 0.0,
                "artist_revenue": 0.0,
                "artist_debt": 0.0,
                "staff_earnings": 0.0,
                "studio_earnings": 0.0,
                "is_owner": is_owner
            }
        
        artist_dict[art_name]["total_jobs"] += 1
        artist_dict[art_name]["total_value"] += total_price
        artist_dict[art_name]["artist_revenue"] += amount_paid
        artist_dict[art_name]["staff_earnings"] += staff_cut
        artist_dict[art_name]["studio_earnings"] += studio_cut

    # Tổng công nợ hiện tại của toàn tiệm (các khách còn thiếu tiền)
    cursor.execute("""
    SELECT COALESCE(SUM(total_price - amount_paid), 0)
    FROM bookings
    WHERE payment_status != 'DA_TAT_TOAN' AND total_price > amount_paid
    """)
    total_debt = cursor.fetchone()[0]

    # Chuyển artist_dict thành danh sách và tính tỷ trọng %
    artist_stats = list(artist_dict.values())
    for a in artist_stats:
        a["percentage"] = round((a["artist_revenue"] / total_revenue * 100), 1) if total_revenue > 0 else 0.0

    artist_stats.sort(key=lambda a: (0 if a["is_owner"] else 1, -a["artist_revenue"]))

    # Lợi nhuận tiệm thực giữ = (Thu từ khách tiệm 100% + Thu 30% khách thợ) - Chi nhập hàng
    total_studio_net_retained = (total_studio_own_revenue + total_studio_from_staff) - total_purchase_cost
    net_profit = total_revenue - total_purchase_cost

    # Cảnh báo kho sắp hết
    cursor.execute("SELECT * FROM inventory_items WHERE stock_quantity <= min_threshold")
    low_stock_items = [dict(row) for row in cursor.fetchall()]

    # Danh sách Top dịch vụ
    top_services = [
        {"service_name": k, "count": v["count"], "revenue": v["revenue"]}
        for k, v in sorted(service_stats.items(), key=lambda x: x[1]["revenue"], reverse=True)[:5]
    ]

    conn.close()
    return {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_contract_value": total_contract_value,
        "total_deposit": total_deposit,
        "deposit_count": deposit_count,
        "total_purchase_cost": total_purchase_cost,
        "total_debt": total_debt,
        "net_profit": net_profit,
        "total_staff_commission": total_staff_commission,
        "total_studio_from_staff": total_studio_from_staff,
        "total_studio_own_revenue": total_studio_own_revenue,
        "total_studio_net_retained": total_studio_net_retained,
        "artist_stats": artist_stats,
        "payment_methods": payment_methods,
        "top_services": top_services,
        "low_stock_items": low_stock_items,
        "daily_calendar": calendar_export,
        "transactions": filtered_payments
    }

# Phục vụ file giao diện Frontend và Tải ảnh
if os.path.exists(os.path.join(EXE_DIR, "static", "index.html")):
    STATIC_DIR = os.path.join(EXE_DIR, "static")
elif os.path.exists(os.path.join(BUNDLE_DIR, "static")):
    STATIC_DIR = os.path.join(BUNDLE_DIR, "static")
elif os.path.exists(os.path.join(EXE_DIR, "_internal", "static")):
    STATIC_DIR = os.path.join(EXE_DIR, "_internal", "static")
else:
    STATIC_DIR = os.path.join(EXE_DIR, "static")
UPLOAD_DIR = os.path.join(EXE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

if os.path.exists(UPLOAD_DIR):
    app.mount("/static/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """API tải ảnh trực tiếp từ máy tính lên server (dành cho avatar thợ, thiết kế xăm...)"""
    try:
        filename = file.filename or "image.jpg"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp"]:
            ext = ".jpg"
            
        unique_name = f"avatar_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_name)
        
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
            
        return {
            "success": True,
            "url": f"/static/uploads/{unique_name}",
            "filename": unique_name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu ảnh tải lên: {str(e)}")

# =====================================================================
# API TRỢ LÝ AI DRON (GEMINI 2.5 FLASH FUNCTION CALLING)
# =====================================================================

class AIChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = None

class AIConfigUpdate(BaseModel):
    api_key: str

@app.post("/api/ai/chat")
def ai_chat_endpoint(data: AIChatRequest):
    try:
        import ai_assistant
        res = ai_assistant.chat_with_gemini(data.message, data.history)
        return {
            "success": True,
            "reply": res.get("reply", ""),
            "tool_calls": res.get("tool_calls", []),
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    except Exception as e:
        return {
            "success": False,
            "reply": f"Có lỗi xảy ra khi xử lý: {str(e)}",
            "tool_calls": [],
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }

@app.get("/api/ai/config")
def get_ai_config():
    try:
        import ai_assistant
        key = ai_assistant.get_gemini_api_key()
        masked = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("Đã cấu hình" if key else "Chưa cấu hình")
        return {
            "has_key": bool(key),
            "key_masked": masked,
            "model": "gemini-2.5-flash"
        }
    except Exception as e:
        return {"has_key": False, "key_masked": "Lỗi kiểm tra", "model": "gemini-2.5-flash"}

@app.post("/api/ai/config")
def update_ai_config(data: AIConfigUpdate):
    try:
        import ai_assistant
        ai_assistant.set_gemini_api_key(data.api_key)
        return {"success": True, "message": "Đã lưu Gemini API Key thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi lưu key: {str(e)}")

# ==========================================
# AUTHENTICATION & USER MANAGEMENT API
# ==========================================

def get_current_user_from_token(token: Optional[str]):
    if not token:
        return None
    if token in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[token]
    try:
        user_id = int(token.split("_")[0])
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, full_name, role, staff_id, is_active FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row["is_active"] == 1:
            u = {
                "id": row["id"],
                "username": row["username"],
                "full_name": row["full_name"],
                "role": row["role"],
                "staff_id": row["staff_id"]
            }
            ACTIVE_SESSIONS[token] = u
            return u
    except Exception:
        pass
    return None

@app.post("/api/auth/login")
def auth_login(data: LoginRequest):
    username = data.username.strip()
    password = data.password.strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu!")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, full_name, role, staff_id, is_active FROM users WHERE LOWER(username) = LOWER(?)", (username,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="Tên đăng nhập hoặc mật khẩu không chính xác!")

    if user["is_active"] != 1:
        raise HTTPException(status_code=403, detail="Tài khoản này đã bị khóa hoặc vô hiệu hóa!")

    if user["password_hash"] != hash_password(password):
        raise HTTPException(status_code=400, detail="Tên đăng nhập hoặc mật khẩu không chính xác!")

    token = f"{user['id']}_{uuid.uuid4().hex}"
    user_info = {
        "id": user["id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "role": user["role"],
        "staff_id": user["staff_id"]
    }
    ACTIVE_SESSIONS[token] = user_info

    return {
        "success": True,
        "message": f"Xin chào {user['full_name']}!",
        "token": token,
        "user": user_info
    }

@app.get("/api/auth/me")
def auth_me(token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    auth_token = token
    if not auth_token and authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ")[1]

    user = get_current_user_from_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn hoặc không hợp lệ!")
    return {"success": True, "user": user}

@app.post("/api/auth/logout")
def auth_logout(token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    auth_token = token
    if not auth_token and authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ")[1]
    if auth_token and auth_token in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[auth_token]
    return {"success": True, "message": "Đã đăng xuất"}

@app.post("/api/auth/change-password")
def auth_change_password(data: ChangePasswordRequest, token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    auth_token = token
    if not auth_token and authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split(" ")[1]

    user = get_current_user_from_token(auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập trước khi đổi mật khẩu!")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
    row = cursor.fetchone()
    if not row or row["password_hash"] != hash_password(data.old_password.strip()):
        conn.close()
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng!")

    if len(data.new_password.strip()) < 4:
        conn.close()
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 4 ký tự!")

    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(data.new_password.strip()), user["id"]))
    conn.commit()
    conn.close()

    return {"success": True, "message": "Đổi mật khẩu thành công!"}

@app.get("/api/users")
def get_user_list(token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT u.id, u.username, u.full_name, u.role, u.staff_id, u.is_active, u.created_at, s.name as staff_name
    FROM users u
    LEFT JOIN staff s ON u.staff_id = s.id
    ORDER BY u.id ASC
    """)
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

@app.post("/api/users")
def create_user(data: UserCreate):
    username = data.username.strip().lower()
    if not username or not data.password.strip():
        raise HTTPException(status_code=400, detail="Vui lòng điền tên đăng nhập và mật khẩu!")
    if len(data.password.strip()) < 4:
        raise HTTPException(status_code=400, detail="Mật khẩu phải từ 4 ký tự trở lên!")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO users (username, password_hash, full_name, role, staff_id, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        """, (username, hash_password(data.password.strip()), data.full_name.strip(), data.role.strip(), data.staff_id))
        conn.commit()
        u_id = cursor.lastrowid
        conn.close()
        return {"success": True, "message": "Tạo tài khoản thành công!", "id": u_id}
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Tên đăng nhập này đã tồn tại, vui lòng chọn tên khác!")

@app.put("/api/users/{user_id}")
def update_user(user_id: int, data: UserUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    u = cursor.fetchone()
    if not u:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    new_full_name = data.full_name.strip() if data.full_name is not None else u["full_name"]
    new_role = data.role.strip() if data.role is not None else u["role"]
    new_staff_id = data.staff_id if data.staff_id is not None else u["staff_id"]
    new_active = data.is_active if data.is_active is not None else u["is_active"]
    new_hash = hash_password(data.password.strip()) if (data.password and len(data.password.strip()) >= 4) else u["password_hash"]

    cursor.execute("""
    UPDATE users SET
        full_name = ?,
        role = ?,
        staff_id = ?,
        is_active = ?,
        password_hash = ?
    WHERE id = ?
    """, (new_full_name, new_role, new_staff_id, new_active, new_hash, user_id))
    conn.commit()
    conn.close()

    # Xóa cache session cũ của user nếu có
    for k, v in list(ACTIVE_SESSIONS.items()):
        if v.get("id") == user_id:
            del ACTIVE_SESSIONS[k]

    return {"success": True, "message": "Cập nhật tài khoản thành công!"}

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    u = cursor.fetchone()
    if not u:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    if u["username"].lower() == "admin":
        conn.close()
        raise HTTPException(status_code=400, detail="Không thể xóa tài khoản Quản trị viên (admin) mặc định!")

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    for k, v in list(ACTIVE_SESSIONS.items()):
        if v.get("id") == user_id:
            del ACTIVE_SESSIONS[k]

    return {"success": True, "message": "Đã xóa tài khoản"}

# ==========================================
# HỆ THỐNG TỰ ĐỘNG CẬP NHẬT ỨNG DỤNG (AUTO-UPDATE)
# ==========================================

def get_app_version_info():
    version_file = os.path.join(EXE_DIR, "version.json")
    if not os.path.exists(version_file):
        version_file = os.path.join(BUNDLE_DIR, "version.json")
    
    default_info = {
        "version": "1.0.0",
        "build_date": "2026-11-18",
        "app_name": "Tiệm Xăm Tạm Chấp Nhận Được",
        "update_url": "https://raw.githubusercontent.com/tiemxamstudio/app-updates/main/version.json",
        "channel": "stable"
    }

    if os.path.exists(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_info.update(data)
        except Exception:
            pass

    # Đọc URL update ghi đè trong database nếu có
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_config WHERE key = 'update_url'")
        row = cursor.fetchone()
        conn.close()
        if row and row["value"]:
            default_info["update_url"] = row["value"]
    except Exception:
        pass

    return default_info

def compare_versions(v1: str, v2: str) -> int:
    try:
        import re
        c1 = re.sub(r'[^0-9.]', '', v1.strip().lstrip('v').split('-')[0].split('+')[0])
        c2 = re.sub(r'[^0-9.]', '', v2.strip().lstrip('v').split('-')[0].split('+')[0])
        p1 = [int(x) for x in c1.split('.') if x]
        p2 = [int(x) for x in c2.split('.') if x]
        while len(p1) < len(p2): p1.append(0)
        while len(p2) < len(p1): p2.append(0)
        if p1 > p2: return 1
        if p1 < p2: return -1
        return 0
    except Exception:
        return 0

@app.get("/api/system/version")
def api_get_system_version():
    return get_app_version_info()

@app.post("/api/system/update-url")
def api_set_update_url(data: UpdateUrlConfig):
    url = data.update_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Vui lòng nhập đường link URL!")
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cursor.execute("INSERT INTO app_config (key, value) VALUES ('update_url', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (url,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Đã lưu đường link cập nhật!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình: {str(e)}")

@app.get("/api/system/check-update")
def api_check_update(custom_url: Optional[str] = Query(None)):
    info = get_app_version_info()
    current_version = info.get("version", "1.0.0")
    target_url = (custom_url.strip() if isinstance(custom_url, str) and custom_url else None) or info.get("update_url")

    if not target_url:
        return {
            "success": False,
            "has_update": False,
            "current_version": current_version,
            "message": "Chưa cấu hình đường link kiểm tra bản mới!"
        }

    try:
        import urllib.request
        import base64
        remote_data = None
        # Ưu tiên đọc trực tiếp từ GitHub API để tránh cache trễ của raw CDN
        if "raw.githubusercontent.com" in target_url:
            try:
                parts = target_url.replace("https://raw.githubusercontent.com/", "").split("/")
                if len(parts) >= 3:
                    owner, repo = parts[0], parts[1]
                    fpath = "/".join(parts[3:]) if len(parts) > 3 else parts[2]
                    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{fpath}"
                    api_req = urllib.request.Request(api_url, headers={"User-Agent": "TiemXamStudio-Updater/1.0"})
                    with urllib.request.urlopen(api_req, timeout=5.0) as api_resp:
                        api_content = json.loads(api_resp.read().decode("utf-8"))
                        remote_data = json.loads(base64.b64decode(api_content["content"]).decode("utf-8-sig"))
            except Exception:
                pass

        if remote_data is None:
            sep = "&" if "?" in target_url else "?"
            no_cache_url = f"{target_url}{sep}_cb={int(time.time())}"
            req = urllib.request.Request(no_cache_url, headers={"User-Agent": "TiemXamStudio-Updater/1.0", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=6.0) as response:
                remote_data = json.loads(response.read().decode("utf-8-sig"))

        remote_version = remote_data.get("version", "1.0.0")
        has_update = compare_versions(remote_version, current_version) > 0

        return {
            "success": True,
            "has_update": has_update,
            "current_version": current_version,
            "latest_version": remote_version,
            "title": remote_data.get("title", f"Bản cập nhật v{remote_version}"),
            "changelog": remote_data.get("changelog", []),
            "download_url": remote_data.get("download_url", ""),
            "release_date": remote_data.get("release_date", ""),
            "mandatory": remote_data.get("mandatory", False)
        }
    except Exception as e:
        return {
            "success": False,
            "has_update": False,
            "current_version": current_version,
            "message": f"Không thể kết nối tới máy chủ cập nhật ({str(e)})"
        }

@app.post("/api/system/apply-update")
def api_apply_update(data: ApplyUpdateRequest):
    download_url = data.download_url.strip()
    if not download_url:
        raise HTTPException(status_code=400, detail="Không tìm thấy link tải bản cập nhật!")

    try:
        import threading
        import subprocess
        import urllib.request
        import zipfile

        temp_dir = os.path.join(EXE_DIR, "_update_temp")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)

        zip_file = os.path.join(temp_dir, "update.zip")
        extract_dir = os.path.join(temp_dir, "files")

        # 1. Tải file zip về máy
        req = urllib.request.Request(download_url, headers={"User-Agent": "TiemXamStudio-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=60.0) as response, open(zip_file, "wb") as out_f:
            shutil.copyfileobj(response, out_f)

        # 2. Giải nén
        with zipfile.ZipFile(zip_file, 'r') as z:
            z.extractall(extract_dir)

        # 3. BẢO VỆ DATABASE TUYỆT ĐỐI: Xóa mọi file database có trong gói giải nén
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f.lower().endswith(".db") or "tattoo_studio" in f.lower():
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass

        # 4. Tự động sao lưu database hiện tại
        db_current = os.path.join(EXE_DIR, "tattoo_studio.db")
        if os.path.exists(db_current):
            backup_dir = os.path.join(EXE_DIR, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(db_current, os.path.join(backup_dir, f"tattoo_studio_pre_update_{now_str}.db"))
            shutil.copy2(db_current, os.path.join(EXE_DIR, "tattoo_studio.db.bak"))

        # Cập nhật version.json
        if data.latest_version:
            try:
                v_path = os.path.join(EXE_DIR, "version.json")
                v_data = get_app_version_info()
                v_data["version"] = data.latest_version.lstrip("v")
                v_data["build_date"] = date.today().strftime("%Y-%m-%d")
                with open(v_path, "w", encoding="utf-8") as vf:
                    json.dump(v_data, vf, ensure_ascii=False, indent=2)
            except Exception:
                pass

        # 5. Tạo file updater.bat để nâng cấp an toàn
        updater_bat = os.path.join(EXE_DIR, "updater.bat")
        bat_content = f"""@echo off
chcp 65001 > nul
title Cap Nhat Tiem Xam Studio
echo ========================================================
echo DANG NANG CAP PHAN MEM TIEM XAM STUDIO...
echo Vui long cho vai giay, he thong se tu dong khoi dong lai!
echo ========================================================
timeout /t 2 /nobreak > nul

:: Copy de cac file moi vao thu muc ung dung, tuyet doi bo qua database
robocopy "{extract_dir}" "{EXE_DIR}" /E /XF tattoo_studio.db *.bak /R:2 /W:1 > nul

:: Xoa thu muc tam
rmdir /s /q "{temp_dir}" > nul

:: Mo lai ung dung
echo Hoan tat cap nhat! Dang mo ung dung...
if exist "{os.path.join(EXE_DIR, 'TattPro.exe')}" (
    start "" "{os.path.join(EXE_DIR, 'TattPro.exe')}"
) else if exist "{os.path.join(EXE_DIR, 'TiemXamStudio.exe')}" (
    start "" "{os.path.join(EXE_DIR, 'TiemXamStudio.exe')}"
) else if exist "{os.path.join(EXE_DIR, 'Mo_TattPro.vbs')}" (
    start "" "{os.path.join(EXE_DIR, 'Mo_TattPro.vbs')}"
) else (
    start "" "python" desktop_app.py
)
exit
"""
        with open(updater_bat, "w", encoding="utf-8") as bf:
            bf.write(bat_content)

        # 6. Khởi chạy updater.bat và thoát app chính sau 1.2s
        def launch_updater():
            time.sleep(1.2)
            try:
                subprocess.Popen(["cmd.exe", "/c", updater_bat], cwd=EXE_DIR, creationflags=0x00000008) # DETACHED_PROCESS
            except Exception:
                pass
            os._exit(0)

        threading.Thread(target=launch_updater, daemon=True).start()

        return {
            "success": True,
            "message": "Đã tải bản cập nhật thành công! Ứng dụng sẽ tự động khởi động lại sau 2 giây..."
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật: {str(e)}")

# ====================================================
# API BẢO MẬT CHỐNG GIAN LẬN & MÃ PIN CHỦ TIỆM
# ====================================================

@app.post("/api/security/verify-pin")
def api_verify_pin(data: PinVerifyRequest):
    valid = verify_owner_pin(data.pin)
    if valid:
        return {"valid": True, "message": "Xác thực mã PIN Chủ Tiệm thành công!"}
    return {"valid": False, "message": "Mã PIN Chủ Tiệm không đúng!"}

@app.post("/api/security/change-pin")
def api_change_pin(data: PinChangeRequest):
    if not verify_owner_pin(data.current_pin):
        raise HTTPException(status_code=400, detail="Mã PIN hiện tại không chính xác!")
    new_pin = str(data.new_pin).strip()
    if len(new_pin) < 4 or len(new_pin) > 12:
        raise HTTPException(status_code=400, detail="Mã PIN mới phải từ 4 đến 12 ký tự!")
    conn = get_db()
    cursor = conn.cursor()
    hashed = hash_password(new_pin)
    cursor.execute("""
        INSERT INTO app_config (key, value) VALUES ('owner_pin', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (hashed,))
    add_audit_log(cursor, "CHANGE_PIN", "security", 0, "Chủ Tiệm đã thay đổi mã PIN bảo mật hệ thống")
    conn.commit()
    conn.close()
    return {"success": True, "message": "Đã đổi mã PIN Chủ Tiệm thành công!"}

@app.get("/api/security/audit-logs")
def api_get_audit_logs(limit: int = 100):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM audit_logs 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"success": True, "logs": rows}

# =========================================================================
# HỆ THỐNG AN TOÀN DỮ LIỆU & SAO LƯU TỰ ĐỘNG CHUẨN SQLITE WAL (PHASE A)
# =========================================================================

BACKUP_DIR = os.path.join(EXE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

class RestoreRequest(BaseModel):
    filename: str

class ExternalBackupConfig(BaseModel):
    directory: str

def safe_sqlite_backup(src_db_path: str, dest_db_path: str):
    """Sử dụng sqlite3.Connection.backup() đồng bộ an toàn 100% kể cả khi đang bật PRAGMA journal_mode=WAL"""
    os.makedirs(os.path.dirname(dest_db_path), exist_ok=True)
    src_conn = sqlite3.connect(src_db_path, timeout=60.0)
    dest_conn = sqlite3.connect(dest_db_path, timeout=60.0)
    try:
        src_conn.backup(dest_conn, pages=100, sleep=0.01)
    finally:
        dest_conn.close()
        src_conn.close()

def clean_old_backups(keep_count=None):
    """Giữ lại tối đa keep_count bản sao lưu gần nhất, tự động xóa các bản cũ hơn để tiết kiệm dung lượng"""
    try:
        if keep_count is None:
            try:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                r_row = cursor.execute("SELECT value FROM app_config WHERE key = 'backup_retention_count'").fetchone()
                conn.close()
                keep_count = int(r_row[0]) if r_row and r_row[0] else 30
            except Exception:
                keep_count = 30

        if not os.path.exists(BACKUP_DIR):
            return
        files = []
        for f in os.listdir(BACKUP_DIR):
            if f.endswith('.db'):
                full_p = os.path.join(BACKUP_DIR, f)
                files.append((full_p, os.path.getmtime(full_p)))
        files.sort(key=lambda x: x[1], reverse=True)
        if len(files) > keep_count:
            for old_p, _ in files[keep_count:]:
                try:
                    os.remove(old_p)
                except Exception:
                    pass
    except Exception as e:
        print(f"Lỗi dọn dẹp bản sao lưu cũ: {e}")

def perform_daily_backup(prefix="daily"):
    """Thực hiện sao lưu an toàn và sao chép sang thư mục ngoài (USB/Cloud) nếu có cấu hình"""
    try:
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tattoo_studio_{prefix}_{now_str}.db"
        dest_path = os.path.join(BACKUP_DIR, filename)
        safe_sqlite_backup(DB_PATH, dest_path)
        clean_old_backups(keep_count=30)

        # Kiểm tra sao lưu sang thư mục ngoại vi (USB / Google Drive) nếu có
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_config WHERE key = 'external_backup_dir'")
            row = cursor.fetchone()
            conn.close()
            if row and row["value"] and os.path.exists(row["value"]):
                ext_dest = os.path.join(row["value"], filename)
                safe_sqlite_backup(DB_PATH, ext_dest)
        except Exception:
            pass

        return dest_path
    except Exception as e:
        print(f"Lỗi tạo bản sao lưu: {e}")
        return None

def start_backup_scheduler():
    """Khởi chạy luồng ngầm kiểm tra và tự động sao lưu mỗi ngày"""
    def scheduler_loop():
        time.sleep(2)
        try:
            today_prefix = f"tattoo_studio_daily_{datetime.now().strftime('%Y%m%d')}"
            has_today = False
            if os.path.exists(BACKUP_DIR):
                for f in os.listdir(BACKUP_DIR):
                    if f.startswith(today_prefix):
                        has_today = True
                        break
            if not has_today:
                print("Chưa có bản sao lưu ngày hôm nay, đang tự động tạo bản sao lưu an toàn...")
                perform_daily_backup(prefix="daily")
        except Exception as e:
            print(f"Lỗi kiểm tra backup ngày mới: {e}")

        while True:
            time.sleep(6 * 3600)  # Kiểm tra định kỳ mỗi 6 tiếng
            try:
                today_prefix = f"tattoo_studio_daily_{datetime.now().strftime('%Y%m%d')}"
                has_today = False
                if os.path.exists(BACKUP_DIR):
                    for f in os.listdir(BACKUP_DIR):
                        if f.startswith(today_prefix):
                            has_today = True
                            break
                if not has_today:
                    perform_daily_backup(prefix="daily")
            except Exception as e:
                print(f"Lỗi trong scheduler sao lưu: {e}")

    threading.Thread(target=scheduler_loop, daemon=True).start()

@app.on_event("startup")
def app_startup_backup_trigger():
    start_backup_scheduler()

@app.get("/api/backup/status")
def api_get_backup_status():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)
    files = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith('.db'):
            full_p = os.path.join(BACKUP_DIR, f)
            files.append({
                "filename": f,
                "size_bytes": os.path.getsize(full_p),
                "mtime": os.path.getmtime(full_p)
            })
    files.sort(key=lambda x: x["mtime"], reverse=True)

    ext_dir = ""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_config WHERE key = 'external_backup_dir'")
        row = cursor.fetchone()
        conn.close()
        if row and row["value"]:
            ext_dir = row["value"]
    except Exception:
        pass

    latest = None
    if files:
        dt = datetime.fromtimestamp(files[0]["mtime"])
        latest = {
            "filename": files[0]["filename"],
            "formatted_time": dt.strftime("%d/%m/%Y %H:%M"),
            "is_today": dt.date() == date.today(),
            "size_kb": round(files[0]["size_bytes"] / 1024, 1)
        }

    return {
        "success": True,
        "total_backups": len(files),
        "latest_backup": latest,
        "external_backup_dir": ext_dir,
        "external_connected": bool(ext_dir and os.path.exists(ext_dir))
    }


class BackupActionRequest(BaseModel):
    filename: str

class BackupNoteRequest(BaseModel):
    filename: str
    note: str

class BackupRetentionRequest(BaseModel):
    keep_count: int

@app.get("/api/backup/list")
def api_get_backup_list():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    notes_map = {}
    retention_count = 30
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backup_notes (
                filename TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cursor.execute("SELECT filename, note FROM backup_notes"):
            notes_map[row[0]] = row[1]
        cursor.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        r_row = cursor.execute("SELECT value FROM app_config WHERE key = 'backup_retention_count'").fetchone()
        if r_row and r_row[0]:
            retention_count = int(r_row[0])
        conn.close()
    except Exception as e:
        print("Lỗi đọc ghi chú/retention:", e)

    files = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith('.db'):
            full_p = os.path.join(BACKUP_DIR, f)
            stat = os.stat(full_p)
            dt = datetime.fromtimestamp(stat.st_mtime)
            size_kb = round(stat.st_size / 1024, 1)
            size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 2)} MB"
            category = "Tự động mỗi ngày" if "daily" in f else ("Thủ công" if "manual" in f else "Bản cứu hộ")
            files.append({
                "filename": f,
                "category": category,
                "created_at": dt.strftime("%d/%m/%Y %H:%M:%S"),
                "size_str": size_str,
                "mtime": stat.st_mtime,
                "note": notes_map.get(f, "")
            })
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return {"success": True, "backups": files, "retention_count": retention_count}

@app.post("/api/backup/delete")
def api_delete_backup(data: BackupActionRequest):
    fname = os.path.basename(data.filename)
    if not fname.endswith('.db') or '..' in fname:
        raise HTTPException(status_code=400, detail="Tên file sao lưu không hợp lệ!")
    full_p = os.path.join(BACKUP_DIR, fname)
    if not os.path.exists(full_p):
        raise HTTPException(status_code=404, detail="File sao lưu không tồn tại hoặc đã bị xóa!")
    try:
        os.remove(full_p)
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM backup_notes WHERE filename = ?", (fname,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"success": True, "message": f"Đã xóa vĩnh viễn bản sao lưu: {fname}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể xóa file: {str(e)}")

@app.post("/api/backup/note")
def api_update_backup_note(data: BackupNoteRequest):
    fname = os.path.basename(data.filename)
    note = data.note.strip()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backup_notes (
                filename TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if note:
            cursor.execute("INSERT INTO backup_notes (filename, note) VALUES (?, ?) ON CONFLICT(filename) DO UPDATE SET note = excluded.note", (fname, note))
        else:
            cursor.execute("DELETE FROM backup_notes WHERE filename = ?", (fname,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Đã lưu tên ghi chú bản sao lưu thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu ghi chú: {str(e)}")

@app.post("/api/backup/retention")
def api_update_backup_retention(data: BackupRetentionRequest):
    keep_count = max(5, min(data.keep_count, 365))
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor.execute("INSERT INTO app_config (key, value) VALUES ('backup_retention_count', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(keep_count),))
        conn.commit()
        conn.close()
        clean_old_backups(keep_count=keep_count)
        return {"success": True, "message": f"Đã cập nhật: Tự động lưu giữ {keep_count} bản gần nhất!", "keep_count": keep_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình tự dọn dẹp: {str(e)}")

@app.post("/api/backup/cleanup-now")
def api_cleanup_backups_now():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        r_row = cursor.execute("SELECT value FROM app_config WHERE key = 'backup_retention_count'").fetchone()
        conn.close()
        keep_count = int(r_row[0]) if r_row and r_row[0] else 30
        clean_old_backups(keep_count=keep_count)
        return {"success": True, "message": f"Đã quét và dọn dẹp bản sao lưu cũ, chỉ giữ lại {keep_count} bản mới nhất!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi dọn dẹp: {str(e)}")


@app.post("/api/backup/create")
def api_create_manual_backup():
    dest = perform_daily_backup(prefix="manual")
    if dest:
        return {"success": True, "message": "Đã tạo bản sao lưu an toàn thành công!", "filename": os.path.basename(dest)}
    raise HTTPException(status_code=500, detail="Không thể tạo bản sao lưu")

@app.post("/api/backup/restore")
def api_restore_backup(data: RestoreRequest):
    fname = os.path.basename(data.filename.strip())
    backup_file = os.path.join(BACKUP_DIR, fname)
    if not os.path.exists(backup_file):
        raise HTTPException(status_code=404, detail="Không tìm thấy file sao lưu được chọn!")

    try:
        # 1. Chụp ngay một bản snapshot cứu hộ trước khi khôi phục
        safety_name = f"tattoo_studio_safety_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        safety_path = os.path.join(BACKUP_DIR, safety_name)
        safe_sqlite_backup(DB_PATH, safety_path)

        # 2. Khôi phục an toàn bằng SQLite backup API
        safe_sqlite_backup(backup_file, DB_PATH)

        # 3. Khởi tạo lại kết nối CSDL và migrations
        init_db()
        return {"success": True, "message": f"Đã khôi phục thành công từ bản '{fname}'! Bản snapshot cứu hộ '{safety_name}' đã được lưu an toàn phòng hờ."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi trong quá trình khôi phục: {str(e)}")

@app.post("/api/backup/config-external")
def api_config_external_backup(data: ExternalBackupConfig):
    dir_path = data.directory.strip()
    if dir_path:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Không thể tạo hoặc truy cập thư mục này: {str(e)}")
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor.execute("INSERT INTO app_config (key, value) VALUES ('external_backup_dir', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (dir_path,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Đã lưu đường dẫn sao lưu ngoại vi thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình: {str(e)}")


@app.get("/api/backup/drives")
def api_get_available_drives():
    import string, ctypes
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                type_label = "Ổ đĩa trong máy"
                if drive_type == 2:
                    type_label = "USB / Thẻ nhớ ngoài"
                elif drive_type == 4:
                    type_label = "Ổ mạng"
                
                # Bỏ qua ổ C hệ thống khỏi gợi ý mặc định nếu có ổ khác, nhưng vẫn liệt kê
                drives.append({
                    "path": drive_path,
                    "label": f"{letter}: ({type_label})",
                    "default_folder": f"{letter}:\\Backup_TattPro",
                    "type": drive_type
                })
            bitmask >>= 1
    except Exception as e:
        print("Lỗi quét ổ đĩa:", e)

    # Thêm gợi ý đám mây nếu có
    user_home = os.path.expanduser("~")
    for name, p in [
        ("Google Drive", "G:\\My Drive"),
        ("Google Drive", os.path.join(user_home, "Google Drive")),
        ("OneDrive", os.path.join(user_home, "OneDrive")),
    ]:
        if os.path.exists(p):
            drives.append({
                "path": p,
                "label": f"{name} ({p})",
                "default_folder": os.path.join(p, "Backup_TattPro"),
                "type": 9
            })

    return {"drives": drives}

@app.post("/api/backup/browse-folder")
def api_browse_folder_native():
    import queue, threading
    res_queue = queue.Queue()
    def _open_dialog():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            folder = filedialog.askdirectory(title="Chọn Thư Mục Sao Lưu Ngoại Vi (USB / Google Drive / Ổ Đĩa)")
            root.destroy()
            res_queue.put(folder or "")
        except Exception as err:
            res_queue.put(f"ERROR: {str(err)}")

    th = threading.Thread(target=_open_dialog)
    th.daemon = True
    th.start()
    th.join(timeout=60)

    if res_queue.empty():
        return {"success": False, "message": "Hộp thoại chọn thư mục đã đóng hoặc hết thời gian"}

    res = res_queue.get()
    if str(res).startswith("ERROR:"):
        raise HTTPException(status_code=500, detail=str(res))
    if not res:
        return {"success": False, "cancelled": True, "message": "Người dùng đã hủy chọn"}

    # Chuẩn hóa dấu gạch chéo
    normalized_path = os.path.normpath(res)
    return {"success": True, "path": normalized_path}


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_path = os.path.join(STATIC_DIR, "index.html")
    backup_path = os.path.join(STATIC_DIR, "index.html.working_perfect_backup")
    if not os.path.exists(backup_path):
        backup_path = os.path.join(EXE_DIR, "_internal", "static", "index.html")

    content = ""
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = ""

    # Cơ chế tự phục hồi tự động: Nếu phát hiện file bị cắt ngắn hoặc thiếu thẻ đóng </script>, </html>
    if not content or "</html>" not in content or "</script>" not in content or len(content) < 500000:
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as bf:
                    content = bf.read()
                with open(index_path, "w", encoding="utf-8") as out_f:
                    out_f.write(content)
            except Exception:
                pass

    if content:
        return content
    return "<h1>Giao diện đang được khởi tạo...</h1>"

if __name__ == "__main__":
    init_db()
    import uvicorn
    print("Khởi động server Tattoo Management App tại http://127.0.0.1:8688 ...")
    uvicorn.run("main:app", host="127.0.0.1", port=8688, reload=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)

