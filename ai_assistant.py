import os
import sys
import json
import re
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional

for s in (sys.stdout, sys.stderr):
    if s and hasattr(s, "reconfigure"):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Cố gắng import google.genai
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_LIB = True
except Exception:
    HAS_GENAI_LIB = False


def get_db_path() -> str:
    from main import DB_PATH
    return DB_PATH


def get_db_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_gemini_api_key() -> str:
    # 1. Ưu tiên biến môi trường
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    # 2. Đọc từ bảng app_config trong database
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM app_config WHERE key = 'gemini_api_key'")
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return row[0].strip()
    except Exception:
        pass
    return ""


def set_gemini_api_key(api_key: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES ('gemini_api_key', ?)", (api_key.strip(),))
    conn.commit()
    conn.close()


# =====================================================================
# SYSTEM PROMPT CHUẨN KẾ TOÁN & ĐIỀU HÀNH AI CHO TIỆM XĂM
# =====================================================================
EXACT_SYSTEM_PROMPT = """# VAI TRÒ & ĐỊNH DANH (ROLE & IDENTITY)
Tên của bạn là **Trợ Lý Trường Con** - Cánh tay đắc lực, trợ lý vận hành và kế toán trưởng AI của **Tiệm Xăm Tạm Chấp Nhận Được** (Tattoo Studio Management System).
Người đang giao việc và trò chuyện trực tiếp với bạn chính là **Chủ Quán / Anh Chủ** (chủ sở hữu Tiệm Xăm Tạm Chấp Nhận Được).

QUY TẮC XƯNG HÔ VÀ THÁI ĐỘ BẮT BUỘC:
1. **Luôn luôn lễ phép, tôn trọng, dạ/vâng, trung thành và chu đáo với Chủ quán** trong 100% câu trả lời.
2. **Xưng hô chuẩn mực:** Tự xưng là "Dạ em / Em" hoặc "Trường Con", và luôn gọi người dùng là "anh Chủ / anh Chủ quán / Chủ tiệm". Tuyệt đối KHÔNG xưng "tôi" hay nói cộc lốc trống không với Chủ quán.
3. **Câu chào mở đầu chuẩn mực (Chỉ dùng chính xác câu này khi chào):**
   "Dạ em chào anh Chủ quán ạ! Em là Trợ lý Trường Con của Tiệm Xăm Tạm Chấp Nhận Được đây ạ! Hôm nay anh Chủ cần em kiểm tra sổ sách doanh thu, xem lại lịch hẹn, tính tiền thợ hay hạch toán ca nào cứ giao hết cho em nhé! Em sẵn sàng phục vụ anh Chủ 24/7 ạ!"

Bạn đóng vai trò là cầu nối trực tiếp giữa anh Chủ quán và Cơ sở dữ liệu (Database) của ứng dụng. Bạn có toàn quyền **TRUY XUẤT (READ)** toàn bộ dữ liệu hiện có và **TỰ ĐỘNG CẬP NHẬT / GHI MỚI (CREATE / UPDATE)** dữ liệu khi có phát sinh thực tế.

---

# NGUYÊN TẮC CỐT LÕI (CORE OPERATING PRINCIPLES)
1. **Dữ liệu thật là chân lý (Database-First):**
   - Tuyệt đối KHÔNG tự bịa đặt số liệu, không đoán mò lịch hẹn, số dư cọc hay tỷ lệ chia của thợ.
   - Bất cứ khi nào người dùng hỏi thông tin hoặc yêu cầu cập nhật một ca xăm/khách hàng/thợ, bạn PHẢI gọi tool `read_system_data` trước để kiểm tra trạng thái hiện tại trong hệ thống.
2. **Không tự tính nhẩm tiền bạc (Zero-Hallucination Math):**
   - Nhiệm vụ của bạn là bóc tách ngữ cảnh, chuẩn hóa các tham số tài chính và gửi vào tool `mutate_system_data`. Hệ thống Backend sẽ thực hiện phép nhân chia và lưu sổ cái. Bạn chỉ đọc lại kết quả từ Backend trả về để báo cáo.
3. **Chuẩn hóa tiền tệ & Ngôn ngữ lóng ngành xăm:**
   - Tự động quy đổi mọi cách viết tắt sang số nguyên VNĐ:
     * "5tr", "5 củ", "5m" -> 5000000
     * "500k", "5 lít", "5 xị" -> 500000
     * "1tr2", "1 củ 2" -> 1200000
     * "50k", "5 chục" -> 50000

---

# QUY TRÌNH XỬ LÝ NGHIỆP VỤ (STANDARD OPERATING PROCEDURES)

## LUỒNG 1: ĐỌC & TRA CỨU DỮ LIỆU HỆ THỐNG (READ)
Khi người dùng hỏi về doanh thu, lương thợ, lịch hẹn, tiền cọc, công nợ, hoặc tồn kho:
- **Bước 1:** Xác định phạm vi truy vấn (Theo tên thợ, tên khách, mã đơn, ngày hôm nay, tuần này, tháng này, hoặc toàn hệ thống).
- **Bước 2:** Gọi tool `read_system_data` với bộ lọc tương ứng.
- **Bước 3:** Trình bày dữ liệu mạch lạc, phân tách rõ ràng giữa: Tổng doanh thu, Tiền thợ thực nhận, Tiền tiệm thu về, Tiền cọc đang giữ, và Cảnh báo tồn kho (nếu có).

## LUỒNG 2: TỰ ĐỘNG CẬP NHẬT CA XĂM & CHIA TIỀN 70/30 (AUTO-UPDATE & SPLIT)
Khi người dùng báo có ca xăm hoàn thành, cập nhật thanh toán, hoặc thay đổi thông tin đơn hàng:
- **Bước 1 (Đối chiếu dữ liệu cũ):** Gọi `read_system_data` tìm lịch hẹn (booking) hoặc đơn hàng đang mở của khách hàng/thợ đó để lấy:
  * Số tiền khách đã đặt cọc trước đó (deposit_amount).
  * Tỷ lệ chia mặc định của thợ trong hồ sơ (default_artist_percent - thường là 70, nếu không có dữ liệu thì dùng 70).
- **Bước 2 (Phân loại tham số chia tiền):**
  * total_amount: Tổng giá trị chốt của cả hình xăm (bao gồm cả tiền cọc). Ví dụ: Khách cọc 1tr, nay trả nốt 4tr -> total_amount = 5000000.
  * material_cost: Chi phí vật tư tiêu hao riêng (kim, mực đặc biệt, thuốc tê, màng bọc...) cần khấu trừ.
  * split_mode:
    - Chọn "NET" nếu người dùng nói rõ: "trừ tiền mực/vật tư rồi chia", "khấu trừ đồ nghề trước", hoặc chính sách thợ đó là chia sau vật tư.
    - Chọn "GROSS" nếu chia thẳng trên tổng doanh thu (total_amount), không trừ vật tư.
  * artist_percent: Tỷ lệ % của thợ (Mặc định 70, tương ứng tiệm 30. Nếu người dùng ghi rõ "chia 60/40", "thợ 50%", "khách tự kéo chia 80/20" thì cập nhật theo con số mới).
  * tip_amount: Tiền khách thưởng riêng cho thợ (100% thuộc về thợ, KHÔNG đưa vào phép chia 70/30).
  * cash_holder: Xác định ai đang cầm tiền mặt/chuyển khoản của khách ("STUDIO" nếu khách chuyển khoản vào tài khoản tiệm/đưa lễ tân; "ARTIST" nếu thợ thu trực tiếp từ khách) để hệ thống tự động cấn trừ công nợ cuối ngày.
- **Bước 3 (Ghi nhận vào Database):** Gọi tool `mutate_system_data` với hành động UPSERT_TRANSACTION.
- **Bước 4 (Phản hồi xác nhận):** Hiển thị bảng tóm tắt giao dịch vừa được lưu vào hệ thống theo đúng định dạng mẫu chuẩn.

## LUỒNG 3: CẬP NHẬT DỮ LIỆU PHÁT SINH KHÁC (CỌC, LƯƠNG & CÔNG NỢ THỢ, KHO VẬT TƯ)
- Khi khách đặt cọc mới: Gọi mutate_system_data (ACTION: RECORD_DEPOSIT) để tạo lịch hẹn mới và cộng tiền cọc vào quỹ giữ hộ.
- Khi thợ ứng tiền / thanh toán lương: Gọi mutate_system_data (ACTION: ARTIST_PAYOUT) để trừ vào số dư hoa hồng tích lũy của thợ.
- Khi thợ nộp lại tiền mặt thu hộ cho chủ: Gọi mutate_system_data (ACTION: ARTIST_REMIT_TO_OWNER) để cấn trừ công nợ và ghi nhận tiền về cho chủ.
- Khi hỏi báo cáo nhân viên / công nợ ví thợ: Gọi read_system_data (TABLE: payroll) để đối soát rành mạch 2 phần: Thợ còn nợ chủ bao nhiêu và Tiệm còn nợ thợ bao nhiêu.
- Khi nhập / xuất / báo hết vật tư (kim, mực): Gọi mutate_system_data (ACTION: UPDATE_INVENTORY) để cập nhật số lượng tồn kho mới nhất.

---

# XỬ LÝ NGOẠI LỆ & AN TOÀN DỮ LIỆU (EDGE CASES & GUARDRAILS)
1. **Thiếu dữ liệu bắt buộc:** Nếu người dùng chỉ nói "Chia 70/30 ca 5 triệu nhé" mà không nói tên thợ, và tra cứu lịch hôm nay có nhiều hơn 1 thợ đang làm -> KHÔNG tự đoán, hãy hỏi ngắn gọn: "Ca 5 triệu này của thợ nào thực hiện để em cập nhật vào sổ ạ?"
2. **Xung đột số liệu:** Nếu tin nhắn nói "Khách cọc 500k" nhưng hệ thống đang ghi nhận "Khách đã cọc 1.000.000đ", hãy thông báo rõ sự chênh lệch này và hỏi xác nhận trước khi ghi đè.
3. **Thao tác nhạy cảm:** Trình bày chi tiết dữ liệu sắp bị thay đổi và yêu cầu người dùng xác nhận "Đồng ý" trước khi gọi lệnh xóa vĩnh viễn.

---

# ĐỊNH DẠNG PHẢN HỒI (OUTPUT FORMAT)
- Ngắn gọn, súc tích, chuẩn phong cách quản lý tài chính chuyên nghiệp.
- Khi cập nhật xong giao dịch chia 70/30, luôn trình bày theo mẫu chuẩn sau:

✅ **ĐÃ CẬP NHẬT HỆ THỐNG (Mã đơn: #[ID])**
- **Thợ thực hiện:** [Tên thợ] | **Khách hàng:** [Tên khách]
- **Tổng giá trị ca xăm:** [Số tiền]đ *(Đã cọc: [Số tiền]đ | Thanh toán nốt: [Số tiền]đ)*
- **Khấu trừ vật tư:** [Số tiền]đ *(Chế độ chia: [GROSS/NET])*
- **Tỷ lệ chia ([X]/[Y]):**
  * 👨‍🎨 **Thợ nhận ([X]% + Tip):** **[Số tiền]đ**
  * 🏪 **Tiệm nhận ([Y]%):** **[Số tiền]đ**
- **Trạng thái quỹ & Công nợ:** [Ví dụ: Tiệm đang giữ đủ tiền -> Cộng +3.500.000đ vào ví lương của thợ Nam].
"""

# =====================================================================
# BỘ TOOLS DATABASE ĐỘC QUYỀN BACKEND
# =====================================================================

def read_system_data(table: str = "all", filters_json: str = None) -> dict:
    """
    Đọc dữ liệu từ hệ thống tiệm xăm:
    - table: 'artists' (danh sách thợ & ví lương), 'bookings' (lịch hẹn & cọc), 'transactions' (đợt thu & chia tiền), 'inventory' (kho vật tư), 'payout_logs' (lịch sử ứng/lương), 'all' (tổng quan).
    - filters_json: Chuỗi JSON các bộ lọc tùy chọn (ví dụ: {"artist_name": "Nam", "customer_name": "Tuấn"}).
    """
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json) if isinstance(filters_json, str) else filters_json
        except Exception:
            pass

    conn = get_db_connection()
    c = conn.cursor()
    table_clean = (table or "all").strip().lower()

    try:
        if table_clean in ["artists", "tho", "nhan_vien"]:
            c.execute("SELECT id, name, phone, role, commission_rate, COALESCE(wallet_balance, 0) as wallet_balance FROM staff WHERE is_active = 1")
            artists = [dict(r) for r in c.fetchall()]
            if filters.get("artist_name"):
                q = filters["artist_name"].lower()
                artists = [a for a in artists if q in a["name"].lower()]
            return {"table": "artists", "count": len(artists), "records": artists}

        elif table_clean in ["bookings", "lich_hen", "khach", "don_hang"]:
            query = "SELECT id, customer_name, customer_phone, artist_name, service_desc, appointment_date, total_price, deposit_amount, deposit_status, amount_paid, payment_status, client_source FROM bookings WHERE 1=1"
            params = []
            if filters.get("customer_name"):
                query += " AND customer_name LIKE ?"
                params.append(f"%{filters['customer_name']}%")
            if filters.get("artist_name"):
                query += " AND artist_name LIKE ?"
                params.append(f"%{filters['artist_name']}%")
            query += " ORDER BY id DESC LIMIT 20"
            c.execute(query, params)
            return {"table": "bookings", "records": [dict(r) for r in c.fetchall()]}

        elif table_clean in ["customers", "customer_profiles", "khach_hang", "crm", "ho_so"]:
            query = "SELECT phone, name, dob, health_notes, style_preference, notes, created_at FROM customer_profiles WHERE 1=1"
            params = []
            name_filter = filters.get("customer_name") or filters.get("name")
            if name_filter:
                query += " AND name LIKE ?"
                params.append(f"%{name_filter}%")
            if filters.get("phone"):
                query += " AND phone LIKE ?"
                params.append(f"%{filters['phone']}%")
            query += " ORDER BY created_at DESC LIMIT 30"
            c.execute(query, params)
            return {"table": "customer_profiles", "records": [dict(r) for r in c.fetchall()]}

        elif table_clean in ["transactions", "installments", "thu_tien"]:
            query = "SELECT id, booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, staff_payout_status, staff_paid_to_owner_status, staff_remit_type, notes FROM payment_installments WHERE 1=1"
            params = []
            if filters.get("artist_name"):
                query += " AND artist_name LIKE ?"
                params.append(f"%{filters['artist_name']}%")
            query += " ORDER BY id DESC LIMIT 25"
            c.execute(query, params)
            return {"table": "transactions", "records": [dict(r) for r in c.fetchall()]}

        elif table_clean in ["inventory", "kho", "vat_tu"]:
            query = "SELECT id, item_name, category, unit, stock_quantity, min_threshold FROM inventory_items WHERE 1=1"
            params = []
            if filters.get("item_name"):
                query += " AND item_name LIKE ?"
                params.append(f"%{filters['item_name']}%")
            query += " ORDER BY stock_quantity ASC"
            c.execute(query, params)
            items = [dict(r) for r in c.fetchall()]
            return {"table": "inventory", "records": items, "low_stock": [i for i in items if i["stock_quantity"] <= i["min_threshold"]]}

        elif table_clean in ["payout_logs", "ung_luong"]:
            c.execute("SELECT id, artist_name, amount, payout_type, note, created_at FROM payout_logs ORDER BY id DESC LIMIT 20")
            return {"table": "payout_logs", "records": [dict(r) for r in c.fetchall()]}

        elif table_clean in ["payroll", "luong", "cong_no", "vi_luong", "nhan_vien_luong"]:
            from main import get_staff_payroll
            payroll = get_staff_payroll()
            c.execute("SELECT id, name, phone, role, commission_rate, COALESCE(wallet_balance, 0) as wallet_balance FROM staff WHERE is_active = 1")
            staff_list = [dict(r) for r in c.fetchall()]
            return {
                "table": "payroll",
                "summary": payroll.get("summary", {}),
                "staff_balances": staff_list
            }

        # Mặc định: ALL (Tổng quan toàn bộ studio)
        from main import get_staff_payroll
        payroll = get_staff_payroll()
        c.execute("SELECT COALESCE(SUM(amount_paid), 0) FROM bookings")
        total_rev = c.fetchone()[0]
        c.execute("SELECT item_name, unit, stock_quantity, min_threshold FROM inventory_items WHERE stock_quantity <= min_threshold")
        low_stock = [dict(r) for r in c.fetchall()]

        return {
            "table": "all",
            "total_revenue": total_rev,
            "payroll_summary": payroll.get("summary", {}),
            "low_stock_warning": low_stock
        }
    finally:
        conn.close()


def mutate_system_data(action: str, payload_json: str) -> dict:
    """
    Cập nhật dữ liệu hệ thống:
    - action: 'UPSERT_TRANSACTION' (hạch toán ca xăm chia 70/30), 'RECORD_DEPOSIT' (ghi nhận cọc), 'ARTIST_PAYOUT' (ứng/trả lương thợ), 'UPDATE_INVENTORY' (xuất/nhập kho).
    - payload_json: Chuỗi JSON chứa các tham số chi tiết.
    """
    try:
        payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
    except Exception as e:
        return {"error": f"Lỗi định dạng JSON payload: {str(e)}"}

    action_clean = action.strip().upper()
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        # LUỒNG 2: UPSERT_TRANSACTION (HẠCH TOÁN 70/30)
        if action_clean in ["UPSERT_TRANSACTION", "COMPLETE_AND_SPLIT"]:
            customer_name = payload.get("customer_name", "").strip()
            artist_name = payload.get("artist_name", "").strip()
            total_amount = float(payload.get("total_amount") or 0)
            deposit_amount = float(payload.get("deposit_amount") or 0)
            material_cost = float(payload.get("material_cost") or 0)
            tip_amount = float(payload.get("tip_amount") or 0)
            split_mode = payload.get("split_mode", "NET").strip().upper()
            artist_percent = payload.get("artist_percent")
            cash_holder = payload.get("cash_holder", "STUDIO").strip().upper() # STUDIO hoặc ARTIST
            deposit_holder = payload.get("deposit_holder", "STUDIO").strip().upper()
            stage_name = payload.get("stage_name", "Đợt hoàn thành ca xăm").strip()
            booking_id = payload.get("booking_id")

            # 1. Tìm kiếm booking liên quan nếu có
            matched_booking = None
            if booking_id:
                c.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
                matched_booking = c.fetchone()
            elif customer_name:
                c.execute("SELECT * FROM bookings WHERE customer_name LIKE ? ORDER BY id DESC LIMIT 1", (f"%{customer_name}%",))
                matched_booking = c.fetchone()

            if matched_booking:
                booking_id = matched_booking["id"]
                if not customer_name:
                    customer_name = matched_booking["customer_name"]
                if not artist_name:
                    artist_name = matched_booking["artist_name"]
                if total_amount == 0 and matched_booking["total_price"]:
                    total_amount = float(matched_booking["total_price"])
                if deposit_amount == 0 and matched_booking["deposit_amount"]:
                    deposit_amount = float(matched_booking["deposit_amount"])

            if not artist_name:
                artist_name = "ANH HÍU"

            # 2. Lấy tỷ lệ hoa hồng mặc định của thợ
            c.execute("SELECT commission_rate, COALESCE(wallet_balance, 0) as wallet_balance FROM staff WHERE name LIKE ? LIMIT 1", (f"%{artist_name}%",))
            st_row = c.fetchone()
            if artist_percent is None:
                artist_percent = float(st_row["commission_rate"]) if st_row else 70.0
            else:
                artist_percent = float(artist_percent)

            # 3. Tính toán công thức kế toán Backend 100%
            if split_mode == "NET":
                splittable_revenue = max(0.0, total_amount - material_cost)
            else:
                splittable_revenue = total_amount

            artist_commission = round((splittable_revenue * artist_percent) / 100.0)
            studio_commission = round(splittable_revenue - artist_commission)
            artist_total_receive = round(artist_commission + tip_amount)

            remaining_amount = max(0.0, total_amount - deposit_amount)

            # Tính tiền mặt thợ đã cầm
            cash_collected_by_artist = 0.0
            if deposit_holder == "ARTIST":
                cash_collected_by_artist += deposit_amount
            if cash_holder == "ARTIST":
                cash_collected_by_artist += remaining_amount

            # Biến động ví lương thợ
            wallet_delta = round(artist_total_receive - cash_collected_by_artist)

            # Cập nhật ví lương trong bảng staff
            c.execute("UPDATE staff SET wallet_balance = COALESCE(wallet_balance, 0) + ? WHERE name LIKE ?", (wallet_delta, f"%{artist_name}%"))

            # Đồng bộ session tương ứng
            session_id = None
            if booking_id:
                c.execute("SELECT id FROM tattoo_sessions WHERE booking_id = ? AND payment_status = 'CHUA_THANH_TOAN' ORDER BY id ASC LIMIT 1", (booking_id,))
                sess_row = c.fetchone()
                if sess_row:
                    session_id = sess_row["id"]
                    c.execute("UPDATE tattoo_sessions SET payment_status = 'DA_THANH_TOAN', session_status = 'HOAN_THANH', session_price = ? WHERE id = ?", (total_amount, session_id))

            # Xác định kiểu đối soát nộp chủ
            remit_type = "100" if cash_holder == "ARTIST" else "30"
            remit_status = "CHUA_NOP" if cash_holder == "ARTIST" else "DA_NOP"
            payout_status = "DA_CHI" if cash_holder == "ARTIST" else "CHUA_CHI"

            # Lưu vào payment_installments
            c.execute("""
            INSERT INTO payment_installments (
                booking_id, session_id, artist_name, installment_name, amount, payment_method, payment_date, notes,
                staff_payout_status, staff_paid_to_owner_status, staff_remit_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                booking_id or 1, session_id, artist_name, stage_name, total_amount,
                "TIEN_MAT" if cash_holder == "ARTIST" else "CHUYEN_KHOAN",
                now_str,
                f"Chia {artist_percent:.0f}/{100-artist_percent:.0f} ({split_mode}). {'Thợ tự thu tiền mặt' if cash_holder == 'ARTIST' else 'Tiệm thu qua quầy'}",
                payout_status, remit_status, remit_type
            ))
            inst_id = c.lastrowid

            # Cập nhật booking
            if booking_id:
                c.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_installments WHERE booking_id = ?", (booking_id,))
                new_paid = c.fetchone()[0]
                c.execute("UPDATE bookings SET amount_paid = ?, payment_status = 'DA_TAT_TOAN' WHERE id = ?", (new_paid, booking_id))

            conn.commit()

            # Trạng thái công nợ minh bạch
            if wallet_delta > 0:
                settlement_note = f"Tiệm đang giữ đủ tiền -> Cộng +{wallet_delta:,.0f}đ vào ví lương của thợ {artist_name}"
            elif wallet_delta < 0:
                settlement_note = f"Thợ {artist_name} đã tự cầm tiền mặt -> Thợ cần nộp lại cho chủ tiệm {abs(wallet_delta):,.0f}đ (Ví lương trừ -{abs(wallet_delta):,.0f}đ)"
            else:
                settlement_note = f"Thợ {artist_name} đã thu vừa đúng số tiền công của mình. Hai bên sòng phẳng"

            return {
                "success": True,
                "installment_id": inst_id,
                "artist_name": artist_name,
                "customer_name": customer_name or "Khách hẹn",
                "total_amount": total_amount,
                "deposit_amount": deposit_amount,
                "remaining_amount": remaining_amount,
                "material_cost": material_cost,
                "split_mode": split_mode,
                "artist_percent": artist_percent,
                "studio_percent": 100 - artist_percent,
                "artist_total_receive": artist_total_receive,
                "studio_commission": studio_commission,
                "cash_holder": cash_holder,
                "wallet_delta": wallet_delta,
                "settlement_note": settlement_note
            }

        # LUỒNG 3: RECORD_DEPOSIT (GHI NHẬN CỌC MỚI)
        elif action_clean == "RECORD_DEPOSIT":
            customer_name = payload.get("customer_name", "").strip()
            phone = payload.get("phone", "")
            artist = payload.get("artist_name", "ANH HÍU").strip()
            service_desc = payload.get("service_desc", "Xăm hình nghệ thuật")
            total_price = float(payload.get("total_price") or 2000000)
            deposit_amt = float(payload.get("deposit_amount") or 500000)
            deposit_holder = payload.get("deposit_holder", "STUDIO").upper()

            c.execute("""
            INSERT INTO bookings (
                customer_name, customer_phone, artist_name, service_desc, appointment_date,
                total_price, deposit_amount, deposit_status, amount_paid, payment_status, client_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'DA_COC', ?, 'CHUA_THANH_TOAN', 'NHAN_VIEN')
            """, (customer_name, phone, artist, service_desc, now_str, total_price, deposit_amt, deposit_amt))
            b_id = c.lastrowid

            # Đồng bộ sang hồ sơ khách hàng CRM
            if phone and customer_name:
                c.execute("""
                INSERT INTO customer_profiles (phone, name, notes, created_at)
                VALUES (?, ?, 'Tạo tự động qua AI Assistant', ?)
                ON CONFLICT(phone) DO UPDATE SET name = excluded.name
                """, (phone, customer_name, now_str))

            c.execute("""
            INSERT INTO payment_installments (
                booking_id, installment_name, amount, payment_method, payment_date, notes,
                staff_payout_status, staff_paid_to_owner_status, staff_remit_type, artist_name
            ) VALUES (?, 'Đợt 1 (Cọc)', ?, 'CHUYEN_KHOAN', ?, ?, 'CHUA_CHI', 'CHUA_NOP', '30', ?)
            """, (b_id, deposit_amt, now_str, f"Tiền cọc ({deposit_holder} giữ)", artist))

            conn.commit()
            return {
                "success": True,
                "action": "RECORD_DEPOSIT",
                "booking_id": b_id,
                "customer_name": customer_name,
                "deposit_amount": deposit_amt,
                "deposit_holder": deposit_holder,
                "message": f"Đã ghi nhận đặt cọc {deposit_amt:,.0f}đ cho khách '{customer_name}' ({deposit_holder} giữ)."
            }

        # LUỒNG 3: ARTIST_PAYOUT (ỨNG LƯƠNG / THANH TOÁN LƯƠNG THỢ)
        elif action_clean == "ARTIST_PAYOUT":
            artist_name = payload.get("artist_name", "").strip()
            amount = float(payload.get("amount") or 0)
            p_type = payload.get("type", "ADVANCE").upper() # ADVANCE hoặc SETTLEMENT
            note = payload.get("note", "Tạm ứng tiền mặt qua AI")

            c.execute("INSERT INTO payout_logs (artist_name, amount, payout_type, note) VALUES (?, ?, ?, ?)", (artist_name, amount, p_type, note))
            c.execute("UPDATE staff SET wallet_balance = COALESCE(wallet_balance, 0) - ? WHERE name LIKE ?", (amount, f"%{artist_name}%"))
            conn.commit()

            c.execute("SELECT COALESCE(wallet_balance, 0) FROM staff WHERE name LIKE ?", (f"%{artist_name}%",))
            new_bal = c.fetchone()[0]

            return {
                "success": True,
                "action": "ARTIST_PAYOUT",
                "artist_name": artist_name,
                "amount": amount,
                "payout_type": p_type,
                "new_wallet_balance": new_bal,
                "message": f"Đã chi {amount:,.0f}đ cho thợ {artist_name} ({note}). Ví lương còn lại: {new_bal:,.0f}đ."
            }

        # LUỒNG 4: ARTIST_REMIT_TO_OWNER (THỢ NỘP TIỀN THU HỘ / HOA HỒNG CHO CHỦ TIỆM)
        elif action_clean in ["ARTIST_REMIT_TO_OWNER", "THO_NOP_TIEN"]:
            artist_name = payload.get("artist_name", "").strip()
            amount = float(payload.get("amount") or 0)
            note = payload.get("note", "Thợ nộp tiền mặt cho chủ tiệm qua AI")

            c.execute("INSERT INTO payout_logs (artist_name, amount, payout_type, note) VALUES (?, ?, 'THO_NOP_CHU', ?)", (artist_name, amount, note))
            c.execute("""
            UPDATE payment_installments 
            SET staff_paid_to_owner_status = 'DA_NOP' 
            WHERE artist_name LIKE ? AND staff_paid_to_owner_status = 'CHUA_NOP'
            """, (f"%{artist_name}%",))
            c.execute("UPDATE staff SET wallet_balance = COALESCE(wallet_balance, 0) + ? WHERE name LIKE ?", (amount, f"%{artist_name}%"))
            conn.commit()

            c.execute("SELECT COALESCE(wallet_balance, 0) FROM staff WHERE name LIKE ?", (f"%{artist_name}%",))
            new_bal = c.fetchone()[0]

            return {
                "success": True,
                "action": "ARTIST_REMIT_TO_OWNER",
                "artist_name": artist_name,
                "amount": amount,
                "new_wallet_balance": new_bal,
                "message": f"Đã ghi nhận thợ {artist_name} nộp lại {amount:,.0f}đ tiền mặt cho chủ tiệm ({note}). Ví công nợ đã được cấn trừ."
            }

        # LUỒNG 3: UPDATE_INVENTORY (KHO VẬT TƯ)
        elif action_clean == "UPDATE_INVENTORY":
            item_name = payload.get("item_name", "").strip()
            qty_change = float(payload.get("quantity_change") or 0)
            reason = payload.get("reason", "Cập nhật qua AI")

            c.execute("SELECT id, item_name, unit, stock_quantity, min_threshold FROM inventory_items WHERE item_name LIKE ? LIMIT 1", (f"%{item_name}%",))
            row = c.fetchone()
            if not row:
                return {"error": f"Không tìm thấy vật tư '{item_name}' trong kho"}

            new_qty = max(0.0, float(row["stock_quantity"]) + qty_change)
            c.execute("UPDATE inventory_items SET stock_quantity = ? WHERE id = ?", (new_qty, row["id"]))
            conn.commit()
            is_low = new_qty <= float(row["min_threshold"])

            return {
                "success": True,
                "action": "UPDATE_INVENTORY",
                "item_name": row["item_name"],
                "unit": row["unit"],
                "change": qty_change,
                "new_quantity": new_qty,
                "is_low_stock_warning": is_low,
                "message": f"Kho vật tư '{row['item_name']}': {'+' if qty_change > 0 else ''}{qty_change:.0f} {row['unit']}. Hiện có: {new_qty:.0f} {row['unit']}.{' ⚠️ SẮP HẾT HÀNG!' if is_low else ''}"
            }

        return {"error": f"Hành động '{action}' không xác định."}
    finally:
        conn.close()


# =====================================================================
# BỘ XỬ LÝ NLP CỤC BỘ TUÂN THỦ 100% FORMAT PHẢN HỒI YÊU CẦU
# =====================================================================

def parse_vietnamese_money(text: str) -> float:
    t = text.lower()
    m = re.search(r'(\d+[\.,]?\d*)\s*(tr|triệu|củ|m\b)', t)
    if m:
        return float(m.group(1).replace(',', '.')) * 1000000
    m = re.search(r'(\d+[\.,]?\d*)\s*(k|nghìn|ngàn|lít\b)', t)
    if m:
        return float(m.group(1).replace(',', '.')) * 1000
    m = re.search(r'(\d+[\.,]?\d*)\s*xị', t)
    if m:
        return float(m.group(1).replace(',', '.')) * 100000
    m = re.search(r'(\d+[\.,]?\d*)\s*chục', t)
    if m:
        return float(m.group(1).replace(',', '.')) * 10000
    return 0.0


def process_local_controller(user_message: str) -> dict:
    msg = user_message.lower().strip()
    tool_calls = []

    # Kiểm tra xem người dùng có paste API Key trực tiếp vào chat không
    key_match = re.search(r'(AIzaSy[A-Za-z0-9_-]{33})', user_message)
    if key_match:
        extracted_key = key_match.group(1)
        set_gemini_api_key(extracted_key)
        return {
            "reply": f"🔑 **ĐÃ KÍCH HOẠT THÀNH CÔNG GOOGLE GEMINI 2.5 FLASH!**\n\nKhóa API `{extracted_key[:6]}...{extracted_key[-4:]}` đã được lưu trữ an toàn trong hệ thống. Bây giờ bạn có thể trò chuyện với đầy đủ trí tuệ nhân tạo Gemini 2.5 Flash cao cấp nhất của Google!",
            "tool_calls": [{"tool": "set_gemini_api_key"}]
        }

    # LUỒNG 1: TRA CỨU DOANH THU & KHO
    if any(k in msg for k in ["báo cáo", "tổng quan", "doanh thu", "tra cứu", "xem lại", "tình hình", "lương", "công nợ"]):
        tool_calls.append({"tool": "read_system_data", "table": "all"})
        data = read_system_data("all")
        rev = data.get("total_revenue", 0)
        p_summary = data.get("payroll_summary", {})
        low_items = data.get("low_stock_warning", [])

        low_str = ", ".join([f"{i['item_name']} (còn {i['stock_quantity']:.0f} {i['unit']})" for i in low_items]) if low_items else "Kho ổn định đầy đủ."
        reply = (
            f"📊 **BÁO CÁO TỔNG QUAN TÀI CHÍNH & VẬN HÀNH STUDIO**\n\n"
            f"- **Tổng doanh thu tiệm ghi nhận**: `{rev:,.0f} đ`\n"
            f"- **Tổng công thợ được hưởng (70%)**: `{p_summary.get('total_staff_earned', 0):,.0f} đ` *(Tiệm đã chi: {p_summary.get('total_staff_paid', 0):,.0f} đ | Tiệm còn nợ thợ: {p_summary.get('total_staff_pending', 0):,.0f} đ)*\n"
            f"- **Thợ đã nộp cho chủ tiệm**: `{p_summary.get('total_staff_to_owner_paid', 0):,.0f} đ` *(Thợ còn nợ chủ: {p_summary.get('total_staff_to_owner_pending', 0):,.0f} đ)*\n"
            f"- **Cảnh báo kho vật tư**: {low_str}\n\n"
            f"💡 *Bạn có thể ra lệnh hoàn thành ca xăm, ghi nhận cọc hoặc ứng lương thợ trực tiếp tại đây.*"
        )
        return {"reply": reply, "tool_calls": tool_calls}

    # LUỒNG 2: HOÀN THÀNH CA XĂM & CHIA 70/30 (THEO ĐÚNG OUTPUT TEMPLATE YÊU CẦU)
    if any(k in msg for k in ["xong", "hoàn thành", "tính tiền", "chia 70", "thu tiền", "70/30", "60/40"]):
        # 1. Tra cứu số liệu
        tool_calls.append({"tool": "read_system_data", "table": "bookings"})
        
        # Bóc tách tên khách hàng nếu có
        cust_m = re.search(r'khách\s+([A-ZÀ-Ỹa-zà-ỹ0-9\s]+?)(?=\s+(?:xăm|chỗ|vừa|hết|giá|tiền|thợ|chốt)|$)', user_message, re.IGNORECASE)
        cust_name = cust_m.group(1).strip() if cust_m else "Khách hoàn thành"

        # Bóc tách tên thợ
        artist = "ANH BẮP" if "bắp" in msg else ("MASTER CHICKEN" if "chicken" in msg else "ANH HÍU")
        if "hùng" in msg:
            artist = "Hùng"
        elif "nam" in msg:
            artist = "Nam"

        # Bóc tách tiền ca xăm
        total_amt = parse_vietnamese_money(msg)
        if total_amt == 0:
            total_amt = 3000000.0

        # Bóc tách khấu trừ vật tư
        mat_cost = 0.0
        m_cost = re.search(r'(?:trừ|khấu trừ)\s*(\d+[\.,]?\d*)\s*(k|nghìn|ngàn|lít|tr|triệu|củ)?', msg)
        if m_cost:
            val = float(m_cost.group(1).replace(',', '.'))
            unit = (m_cost.group(2) or 'k').lower()
            mat_cost = val * 1000000 if unit in ['tr', 'triệu', 'củ'] else val * 1000

        is_tho_cam = any(k in msg for k in ["thợ cầm", "thợ thu", "tiền mặt", "cầm tiền"])
        split_mode = "NET" if mat_cost > 0 or any(k in msg for k in ["trừ mực", "trừ kim", "vật tư", "net"]) else "GROSS"

        # Tỷ lệ chia
        pct_match = re.search(r'chia\s*(\d+)[/-](\d+)', msg)
        pct = float(pct_match.group(1)) if pct_match else 70.0

        payload = {
            "customer_name": cust_name,
            "artist_name": artist,
            "total_amount": total_amt,
            "material_cost": mat_cost,
            "split_mode": split_mode,
            "artist_percent": pct,
            "cash_holder": "ARTIST" if is_tho_cam else "STUDIO",
            "stage_name": "Đợt xăm hoàn thành"
        }

        tool_calls.append({"tool": "mutate_system_data", "action": "UPSERT_TRANSACTION", "payload": payload})
        res = mutate_system_data("UPSERT_TRANSACTION", payload)

        # Trình bày THEO ĐÚNG MẪU CHUẨN ĐƯỢC QUY ĐỊNH
        reply = (
            f"✅ **ĐÃ CẬP NHẬT HỆ THỐNG (Mã đơn: #{res['installment_id']})**\n"
            f"- **Thợ thực hiện:** {res['artist_name']} | **Khách hàng:** {res['customer_name']}\n"
            f"- **Tổng giá trị ca xăm:** {res['total_amount']:,.0f}đ *(Đã cọc: {res['deposit_amount']:,.0f}đ | Thanh toán nốt: {res['remaining_amount']:,.0f}đ)*\n"
            f"- **Khấu trừ vật tư:** {res['material_cost']:,.0f}đ *(Chế độ chia: {res['split_mode']})*\n"
            f"- **Tỷ lệ chia ({res['artist_percent']:.0f}/{res['studio_percent']:.0f}):**\n"
            f"  * 👨‍🎨 **Thợ nhận ({res['artist_percent']:.0f}% + Tip):** **{res['artist_total_receive']:,.0f}đ**\n"
            f"  * 🏪 **Tiệm nhận ({res['studio_percent']:.0f}%):** **{res['studio_commission']:,.0f}đ**\n"
            f"- **Trạng thái quỹ & Công nợ:** {res['settlement_note']}."
        )
        return {"reply": reply, "tool_calls": tool_calls}

    # LUỒNG 3: KHO VẬT TƯ
    if any(k in msg for k in ["kho", "kim", "mực", "găng tay", "vật tư"]):
        tool_calls.append({"tool": "read_system_data", "table": "inventory"})
        inv = read_system_data("inventory")
        items = inv.get("records", [])
        lines = [f"- {it['item_name']}: **{it['stock_quantity']:.0f} {it['unit']}** (Ngưỡng an toàn: {it['min_threshold']:.0f})" for it in items[:6]]
        reply = (
            f"📦 **TỒN KHO VẬT TƯ TIỆM XĂM HIỆN TẠI**:\n\n"
            + "\n".join(lines)
            + "\n\n💡 *Để xuất hoặc nhập kho, bạn có thể ra lệnh: 'Xuất 2 hộp kim 3RL' hoặc 'Nhập 5 chai mực Dynamic Black'.*"
        )
        return {"reply": reply, "tool_calls": tool_calls}

    # Phản hồi mặc định (Trợ lý Trường Con)
    return {
        "reply": "Dạ em chào anh Chủ quán ạ! Em là Trợ lý Trường Con của Tiệm Xăm Tạm Chấp Nhận Được đây ạ! Hôm nay anh Chủ cần em kiểm tra sổ sách doanh thu, xem lại lịch hẹn, tính tiền thợ hay hạch toán ca nào cứ giao hết cho em nhé! Em sẵn sàng phục vụ anh Chủ 24/7 ạ!",
        "tool_calls": []
    }


# =====================================================================
# HÀM GỌI GEMINI 2.5 FLASH THẬT (FUNCTION CALLING)
# =====================================================================

def chat_with_gemini(message: str, history: List[dict] = None) -> dict:
    # Tự động bắt key nếu người dùng gửi trong chat (hỗ trợ cả chuẩn AIzaSy... và AQ....)
    key_match = re.search(r'(AIzaSy[A-Za-z0-9_-]{33}|AQ\.[A-Za-z0-9_-]{30,})', message)
    if key_match:
        detected_key = key_match.group(1).strip()
        set_gemini_api_key(detected_key)
        return {
            "reply": f"🔑 **ĐÃ KÍCH HOẠT THÀNH CÔNG GOOGLE GEMINI AI!**\n\nKhóa API `{detected_key[:6]}...{detected_key[-4:]}` đã được lưu vĩnh viễn vào hệ thống. Kể từ lúc này, mọi yêu cầu đối thoại sẽ được AI phân tích và xử lý trực tiếp kết nối với cơ sở dữ liệu studio!",
            "tool_calls": [{"tool": "set_gemini_api_key"}]
        }

    api_key = get_gemini_api_key()

    # Nếu không có API Key hoặc thư viện google.genai chưa sẵn sàng -> Chạy NLP Controller chuẩn mẫu
    if not api_key or not HAS_GENAI_LIB:
        return process_local_controller(message)

    try:
        client = genai.Client(api_key=api_key)

        # Truyền chính xác 2 tools theo đúng thiết kế
        tools = [read_system_data, mutate_system_data]

        config = types.GenerateContentConfig(
            system_instruction=EXACT_SYSTEM_PROMPT,
            tools=tools,
            temperature=0.2
        )

        # Danh sách mô hình ưu tiên hỗ trợ các thế hệ Gemini API key khác nhau
        PREFERRED_MODELS = [
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-2.5-flash",
            "gemini-flash-latest"
        ]

        chat = None
        last_err = None
        for model_name in PREFERRED_MODELS:
            try:
                chat = client.chats.create(model=model_name, config=config)
                response = chat.send_message(message)
                reply_text = response.text or "Đã xử lý xong yêu cầu của bạn."
                return {
                    "reply": reply_text,
                    "tool_calls": [{"tool": f"{model_name}-function-calling"}]
                }
            except Exception as me:
                last_err = me
                continue

        # Nếu không có model nào kết nối được thì ném lỗi
        if last_err:
            raise last_err

    except Exception as e:
        err_str = str(e)
        try:
            # Ghi log an toàn không chứa ký tự gây lỗi encode trên Windows
            safe_err = err_str[:120].encode('ascii', errors='replace').decode('ascii')
            print(f"[AI Assistant Notice] Fallback to local controller: {safe_err}")
        except Exception:
            pass

        res = process_local_controller(message)
        res["reply"] += f"\n\n*(Lưu ý: Đã chuyển sang xử lý cục bộ do lỗi kết nối Gemini: {err_str[:90]}...)*"
        return res
