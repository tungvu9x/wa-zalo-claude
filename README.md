# wa-zalo-daily

Agent Windows bắt tin WhatsApp và Zalo cá nhân, đẩy lên Cloud. Cloud lưu tin và file gốc, đọc nội dung file PDF/Excel/CSV, phân loại báo giá, có trang quản trị web (đăng nhập QR, file, báo cáo tuần/tháng) và MCP server để Claude Desktop (sau này là claude.ai) tổng hợp tin nhắn và file.

```
Windows agent (Node)                         Cloud (Docker)
 whatsapp-web.js + zca-js                     FastAPI ingest ──▶ Postgres (messages, attachments, files,
 outbox SQLite (retry, dead-letter)  HTTPS                        documents, audit_log)
 ký HMAC theo device key  ─────────────────▶  Storage: volume filedata (tenant/YYYY/MM/DD/xx/sha256)
 heartbeat 60s                                Worker đọc PDF/Excel ──▶ documents (text, doc_type)
                                              /mcp (Bearer) ◀── mcp-remote ◀── Claude Desktop
                                              Retention job (mặc định 150 ngày)
```

## Cài trên một máy Windows (Cloud + Agent + Claude Desktop)

Hướng dẫn chi tiết từng bước, xử lý sự cố và sao lưu: **[INSTALL.md](INSTALL.md)**.

Cách này không cần Docker. Mở **PowerShell** (không cần chạy bằng Admin) và chạy lần lượt:

```powershell
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
# Đóng PowerShell, mở cửa sổ mới, rồi chạy tiếp:
git clone https://github.com/tungvu9x/wa-zalo-claude.git $HOME\wa-zalo-claude
cd $HOME\wa-zalo-claude
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Script `install-windows.ps1` làm những việc sau:

- Cài Python 3.12, Node.js LTS, PostgreSQL 16 bằng winget. Khi cài PostgreSQL, Windows sẽ hiện hộp thoại UAC để xác nhận quyền.
- Tạo database và sinh toàn bộ secret vào `cloud\.env` và `agent\.env`.
- Cài thư viện Python và Node, đăng ký tenant và device.
- Ghi cấu hình MCP vào Claude Desktop. File cấu hình cũ được giữ lại thành `.bak-*`.
- Sinh mật khẩu trang quản trị (in ra cuối quá trình cài, lưu ở `ADMIN_UI_PASSWORD` trong `cloud\.env`).
- Tạo shortcut **WA-Zalo Daily** (chạy hệ thống) và **WA-Zalo Admin** (mở trang quản trị) trên Desktop.

Script chạy lại nhiều lần được: secret và device đã tạo được giữ nguyên.

Mỗi lần dùng:

1. Double-click **WA-Zalo Daily**. Script bật Cloud và Agent (hai cửa sổ thu nhỏ), rồi mở trang quản trị.
2. Lần đầu tiên, ở mục **Đăng nhập tài khoản** trên trang quản trị:
   - WhatsApp: quét mã QR, hoặc bấm "Đăng nhập bằng mã số" và nhập mã 8 ký tự vào điện thoại.
   - Zalo: quét mã QR bằng app Zalo. Khi cookie Zalo hết hạn, mã QR mới tự hiện lại ở đây.
3. Thoát hẳn Claude Desktop (chuột phải icon ở khay hệ thống > Quit), rồi mở lại.

Để dừng, đóng hai cửa sổ `WA-Zalo Agent` và `WA-Zalo Cloud`.

Cập nhật code: `git pull`, rồi chạy lại `install-windows.ps1`.

## Trang quản trị

`http://127.0.0.1:8080/admin`, đăng nhập bằng `ADMIN_UI_PASSWORD`. Trang chỉ mở trên máy chạy Cloud: ai mở được trang là thấy mã QR và liên kết được số điện thoại, nên **không đưa trang này ra Internet**.

| Trang | Nội dung |
|---|---|
| Tổng quan | Trạng thái Cloud, agent, WhatsApp, Zalo; mã QR hoặc mã ghép nối để đăng nhập, liên kết lại, đăng xuất; số liệu và hội thoại hôm nay; file cần xử lý; thông tin MCP |
| File đính kèm | Lọc theo thời gian, kênh, loại, phân loại; tìm trong nội dung; xem text đã đọc; tải file gốc; đọc lại; sửa phân loại (giữ nguyên khi đọc lại) |
| Báo cáo | Tuần hoặc tháng so với kỳ trước: hội thoại, khách mới, tin đến/đi, tỉ lệ được trả lời, thời gian phản hồi trung vị (chỉ tính giờ làm việc), biểu đồ theo ngày, hội thoại nhiều nhất, chat chờ trả lời quá 24 giờ, báo giá gửi/nhận và phản hồi sau đó, nhận xét. Xuất PDF bằng nút "Xuất PDF" (in ra PDF) |
| Cài đặt | Ngày và giờ làm việc, số ngày lưu dữ liệu, thu hồi thiết bị |

Nhận xét báo cáo: bấm "Sao chép lệnh cho Claude", dán vào Claude Desktop. Claude gọi `period_report`, đọc thêm hội thoại và báo giá, rồi lưu nhận xét bằng `save_report_note`. Có thể tự viết hoặc sửa trên trang.

Đăng nhập lại / đăng xuất là lệnh gửi xuống agent (agent hỏi lệnh mỗi 5 giây). Agent tắt thì lệnh chờ tối đa 10 phút rồi hết hạn.

## 1. Cloud (Docker, khi chạy trên server riêng)

```bash
cp .env.example .env     # điền POSTGRES_PASSWORD, MASTER_KEY, ADMIN_TOKEN (lệnh sinh khóa ghi sẵn trong file)
docker compose up -d --build
curl localhost:8080/healthz
```

Đặt reverse proxy HTTPS (Caddy/Nginx) phía trước cổng `127.0.0.1:8080`, dùng subdomain riêng (không dùng path prefix, vì chữ ký HMAC ký theo path).

Tạo tenant và device:

```bash
H="authorization: Bearer $ADMIN_TOKEN"
curl -s -X POST https://wa.example.com/admin/tenants -H "$H" -H 'content-type: application/json' \
  -d '{"name":"KNXStore","retention_days":150}'
curl -s -X POST https://wa.example.com/admin/devices -H "$H" -H 'content-type: application/json' \
  -d '{"tenant_id":"<tenant_id>","name":"pc-van-phong"}'    # device_secret chỉ hiện một lần
```

Giám sát: `GET /admin/devices` trả `seconds_since_seen` và trạng thái từng connector. Thu hồi máy mất: `POST /admin/devices/{id}/revoke`.

## 2. Agent (Windows)

Cần Node 20+ và Chrome/Chromium (puppeteer tự tải khi `npm install`).

```powershell
cd agent
npm install
copy .env.example .env      # điền CLOUD_URL, DEVICE_ID, DEVICE_SECRET
npm run test:send           # kiểm tra đường ống agent -> Cloud, chưa cần WhatsApp/Zalo
npm start                   # lần đầu: quét QR WhatsApp (terminal) và Zalo (file data\zalo-qr.png)
```

Sau khi đăng nhập được cả hai kênh, cài service tự khởi động (cần NSSM):

```powershell
.\install-service.ps1
```

## 3. MCP cho Claude Desktop

1. Sinh token, điền `MCP_TOKEN` trong `.env` của Cloud, rồi `docker compose up -d --build`.
2. Máy chạy Claude Desktop cần Node 20+ (dùng `npx mcp-remote` làm cầu nối stdio sang HTTP).
3. Mở Claude Desktop > Settings > Developer > Edit Config, chép nội dung `cloud/claude_desktop_config.example.json` vào `mcpServers`, thay `<MCP_TOKEN>`. Khởi động lại Claude Desktop.

| Claude Desktop chạy ở đâu | URL trong config | Ghi chú |
|---|---|---|
| Cùng máy với Cloud | `http://127.0.0.1:8080/mcp` | Mặc định |
| Máy khác trong LAN | `http://<IP-may-Cloud>:8080/mcp` | Đặt `API_BIND=0.0.0.0` trong `.env`, thêm `"--allow-http"` vào `args` của mcp-remote. Token đi dạng rõ trong LAN |
| claude.ai (sau này) | `https://<subdomain>/mcp` | Cloudflare Tunnel. Custom Connector của claude.ai cần OAuth, Bearer tĩnh không đủ: đặt Cloudflare Access (hoặc OAuth proxy) trước `/mcp` |

Trên Windows, nếu Claude Desktop báo không tìm thấy `npx`, đổi `"command": "npx"` thành `"command": "cmd"` và thêm `"/c", "npx"` vào đầu `args`.

Tool (đều chỉ đọc):

| Tool | Dùng để |
|---|---|
| `daily_digest(date)` | Toàn bộ hội thoại một ngày, gom theo chat, kèm file. Điểm bắt đầu cho tổng hợp ngày |
| `list_chats(since, until, name_query)` | Tìm chat theo thời gian/tên, lấy `chat_id` |
| `get_chat_messages(chat_id, channel)` | Đọc đầy đủ một hội thoại, phân trang bằng `before_id` |
| `search_messages(query)` | Tìm tin theo từ khóa, không phân biệt dấu |
| `list_files(kind, doc_type, query)` | Lọc file PDF/Excel, `doc_type="quote"` để lấy báo giá; `query` tìm cả trong nội dung file (mã hàng, tên dự án) |
| `read_file(attachment_id)` | Nội dung text của file, phân trang bằng `offset` |
| `system_status()` | Agent online không, file nào chưa đọc |
| `period_report(period, date)` | Số liệu báo cáo tuần/tháng so với kỳ trước |
| `save_report_note(text, period, date)` | Lưu nhận xét báo cáo lên trang quản trị (tool duy nhất có ghi dữ liệu) |

Thời gian nhận `YYYY-MM-DD`, ISO datetime, `today`, `yesterday`, `7d`.

Ví dụ hỏi Claude: "Tổng hợp tin nhắn Zalo và WhatsApp hôm nay, ai hỏi giá gì, việc nào cần trả lời", "Liệt kê các báo giá nhận được tuần này, so sánh đơn giá DALI-2 gateway".

### Đọc file

| Loại | Thư viện | Giới hạn |
|---|---|---|
| PDF có lớp text | pdfplumber | 80 trang đầu |
| PDF scan (ảnh) | Không đọc được, `read=empty` | Chưa có OCR |
| .xlsx/.xlsm | openpyxl, giá trị đã tính (`data_only`) | 3000 dòng/sheet, bỏ sheet ẩn |
| .xls | xlrd | 3000 dòng/sheet |
| .csv | csv (utf-8, cp1258) | 3000 dòng |
| Ảnh, voice, .docx, .xlsb | Bỏ qua, vẫn lưu file gốc | |

Mỗi file đọc trong tiến trình con (timeout 120 giây, RAM 1,5 GB, tối đa 300.000 ký tự), lỗi thử lại 3 lần cách nhau 15 phút. Ô công thức chỉ có giá trị nếu file được Excel lưu kèm kết quả tính; file do phần mềm khác xuất có thể trống ở các ô này.

`doc_type` (quote / price_table / po / invoice / other) đoán bằng từ khóa trong tên file (`bao gia`, `BG`, `quotation`, `PO`...) và 4000 ký tự đầu nội dung (`báo giá`, cột `đơn giá` + `thành tiền`...). Đây là heuristic, có thể nhầm.

## Hành vi cần biết

| Hạng mục | Hành vi |
|---|---|
| Chế độ | Chỉ nghe. Không gửi, không đánh dấu đã đọc, không auto-reply |
| Mất mạng hoặc Cloud lỗi | Tin và file giữ trong outbox, gửi lại với backoff tối đa 5 phút |
| Trùng tin | Khóa `(tenant, channel, external_id)`, gửi lại không tạo bản sao. File dedupe theo sha256 |
| Tin bị Cloud từ chối (4xx dữ liệu) | Tách khỏi batch, vào bảng `dead` trong `data/outbox.db`, không chặn tin khác |
| File không tải được hoặc quá `MAX_FILE_MB` | Vẫn ghi attachment với `status=failed` và `error`, để báo cáo không mất dấu |
| Tìm kiếm | Cột `tsv` đã bỏ dấu tiếng Việt: `tsv @@ plainto_tsquery('simple', f_unaccent('bao gia den'))` |
| Retention | Job chạy mỗi giờ, xóa tin quá `retention_days` của tenant, file mồ côi quá 48 giờ, ghi `audit_log` |
| Xác thực | HMAC-SHA256 theo device, lệch giờ tối đa 300 giây, secret mã hóa Fernet trong DB |

## Chưa làm, cần biết

- OCR cho PDF scan. OAuth cho claude.ai (dự kiến qua Cloudflare Access).
- Tin đến khi agent tắt sẽ không có trong dữ liệu (agent chỉ nghe realtime).

- Agent đã chạy với tài khoản thật trên Windows (2026-11). Media WhatsApp hết hạn trên server, `downloadMedia()` có thể trả null với tin cũ.
- Zalo: khi cookie đã lưu hết hạn, agent không tự chuyển sang QR. Xóa `data\zalo-credentials.json` rồi chạy lại `npm start` để quét QR.
- Storage đang là filesystem sau interface đơn giản. Chuyển sang S3/R2 khi cần nhiều máy Cloud.
- Row-level security theo `tenant_id` chưa làm. MCP chỉ đọc một tenant (`MCP_TENANT_ID` hoặc tenant duy nhất).
- Chưa có cảnh báo khi agent mất heartbeat, cần job báo qua Zalo/email ở giai đoạn 3.
- `DEVICE_SECRET` nằm trong `.env`, khóa ACL bằng `install-service.ps1`. Có thể chuyển sang Windows Credential Manager sau.
