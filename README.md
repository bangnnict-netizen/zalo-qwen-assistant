# Zalo Qwen Assistant

Bot Python/FastAPI kết nối Zalo, Groq (LLM), Supabase và Airtable.

## Nguyên tắc bảo mật

- **Không bao giờ** hardcode API keys / secrets trong code.
- Sao chép `.env.example` → `.env` và điền giá trị thật.

## Cấu trúc

```text
app/
  __init__.py
  main.py       # FastAPI app + /health
  config.py     # pydantic-settings
knowledge_base/
  kb_internal.md
  kb_customer.md
prompts/
  persona_internal.md
  persona_customer.md
tests/
.env.example
requirements.txt
README.md
```

## Cài đặt và chạy

### 1. Tạo môi trường ảo

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Cấu hình môi trường

```bash
cp .env.example .env
# Điền GROQ_API_KEY, SUPABASE_URL, SUPABASE_KEY, AIRTABLE_API_KEY, ...
```

### 3. Khởi động server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 7860
```

Mở docs: [http://127.0.0.1:7860/docs](http://127.0.0.1:7860/docs)

### 4. Kiểm tra health

```bash
curl http://127.0.0.1:7860/health
```

Kỳ vọng: `{"status":"ok","service":"zalo-qwen-assistant"}`

## Zalo bridge (`ENABLE_ZALO_REAL`)

Biến `ENABLE_ZALO_REAL` (mặc định `true`) bật/tắt kết nối Zalo thật qua `zlapi`:

| Giá trị | Hành vi |
|---------|---------|
| `true` | Khởi động `RealZaloBridge`, giữ phiên Zalo, lắng nghe tin nhắn nhóm |
| `false` | Dùng `MockZaloBridge` — phù hợp dev local / test qua `/simulate` |

**Dev local:** đặt `ENABLE_ZALO_REAL=false` trong `.env` để tránh tranh phiên Zalo với instance trên mây (Hugging Face Space / server production). Chỉ một instance nên giữ `true` và duy trì phiên đăng nhập QR.

Trang quản trị nhóm: `/zalo/admin?token=<ADMIN_TOKEN>` — khai báo nhóm nội bộ / khách hàng. Group ID Zalo luôn được xử lý dưới dạng **string** (tránh mất chính xác số lớn > 2^53).

## Tính năng vận hành mới

- `/tomtat` (admin only, nội bộ): admin nhắn `/tomtat` hoặc `/tomtat 12` trong group nội bộ sẽ yêu cầu bot tóm tắt N giờ gần nhất (mặc định 24h) từ `message_logs` dưới dạng 5 ý chính, quyết định, việc cần làm và câu hỏi chưa trả lời. Chỉ admin trong `ADMIN_USER_IDS` mới được dùng.
- Lead-capture (nhóm `customer`): khi khách hỏi về `giá|báo giá|bao nhiêu|hợp đồng`, bot yêu cầu `tên + số điện thoại` và lưu pending lead trong bộ nhớ (TTL 10 phút). Tin tiếp theo chứa số điện thoại (`0\\d{9,10}`) sẽ kích hoạt ghi lead lên Airtable (bảng `Leads`) và bot xác nhận.
- Tra cứu đơn: tin chứa mã DH (ví dụ `DH1001`, `đơn DH1001`) sẽ truy vấn bảng `orders` trong Supabase và trả về `status`, `received_at`, `note` nếu tìm thấy.

Xem `tests/test_ops.py` để biết kịch bản kiểm thử cho các flow này.
