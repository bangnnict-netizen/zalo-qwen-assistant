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
