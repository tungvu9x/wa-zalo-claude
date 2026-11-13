# Hướng dẫn cài đặt wa-zalo-daily trên Windows

Cài cả hệ thống lên **một máy Windows**:

- **Agent**: thu tin WhatsApp và Zalo của số hotline.
- **Cloud**: lưu tin, đọc file PDF/Excel, chạy trang quản trị.
- **Claude Desktop**: kết nối với Cloud qua MCP để tổng hợp tin nhắn.

Cài trên Mac: xem [INSTALL-mac.md](INSTALL-mac.md). Toàn bộ thao tác làm qua TeamViewer được. Cài lần đầu mất khoảng 20–30 phút; phần lớn thời gian là chờ tải phần mềm.

## 0. Chuẩn bị

| Hạng mục | Yêu cầu |
|---|---|
| Máy | Windows 10/11 64-bit, RAM từ 8 GB (khuyên dùng 16 GB), trống ít nhất 5 GB ổ C |
| Mạng | Có Internet trong lúc cài |
| Quyền | Tài khoản Windows được cài phần mềm. Khi cài PostgreSQL, Windows sẽ hiện hộp thoại UAC để xác nhận |
| Điện thoại | Điện thoại đang đăng nhập WhatsApp và Zalo của số hotline, dùng để quét QR |
| Winget | Có sẵn trên Windows 10 (1809 trở lên) và Windows 11. Nếu không có, cài "App Installer" từ Microsoft Store |

> Qua TeamViewer: hộp thoại UAC có thể hiện thành màn hình đen phía người điều khiển. Khi đó nhờ người ngồi tại máy bấm **Yes**, hoặc bật TeamViewer với quyền Administrator.

## 1. Mở PowerShell

Bấm **Start**, gõ `PowerShell`, chọn **Windows PowerShell**. Mở bình thường, **không** chọn "Run as administrator".

## 2. Cài Git

```powershell
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
```

Cài xong, **đóng PowerShell và mở cửa sổ mới**, để Windows nhận lệnh `git`.

## 3. Tải mã nguồn

```powershell
git clone https://github.com/tungvu9x/wa-zalo-claude.git $HOME\wa-zalo-claude
cd $HOME\wa-zalo-claude
```

Thư mục cài đặt là `C:\Users\<tên user>\wa-zalo-claude`.

## 4. Chạy bộ cài

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Bộ cài tự làm lần lượt các bước sau. Mỗi bước in ra một dòng `==> ...`.

1. Cài **Python 3.12**, **Node.js LTS** và **PostgreSQL 16** nếu máy chưa có. Lúc cài PostgreSQL, Windows hiện hộp thoại UAC: bấm **Yes**.
2. Tạo database `wadaily` và sinh toàn bộ mật khẩu, khoá vào `cloud\.env` và `agent\.env`.
3. Cài thư viện Python và Node. Bước này tải cả Chromium cho WhatsApp, khoảng 150 MB.
4. Đăng ký máy này với Cloud.
5. Ghi cấu hình kết nối vào Claude Desktop. File cấu hình cũ được giữ lại với đuôi `.bak-...`.
6. Tạo 2 shortcut trên Desktop: **WA-Zalo Daily** và **WA-Zalo Admin**.

Khi xong, màn hình hiện:

```
CAI DAT XONG
Trang quan tri: http://127.0.0.1:8080/admin   Mat khau: xxxxxxxxxxxx
```

**Ghi lại mật khẩu này.** Nếu quên, mở file `cloud\.env` và xem dòng `ADMIN_UI_PASSWORD`.

Nếu bộ cài dừng giữa chừng, xem mục [Xử lý sự cố](#8-xử-lý-sự-cố), sửa xong thì chạy lại đúng lệnh trên. Bộ cài chạy lại nhiều lần được: mật khẩu và thiết bị đã tạo vẫn giữ nguyên.

## 5. Chạy lần đầu và đăng nhập WhatsApp/Zalo

1. Double-click **WA-Zalo Daily** trên Desktop.
   - Hai cửa sổ thu nhỏ hiện ở thanh taskbar: **WA-Zalo Cloud** và **WA-Zalo Agent**. Không đóng hai cửa sổ này.
   - Trình duyệt tự mở trang quản trị. Đăng nhập bằng mật khẩu ở bước 4.
2. Trên trang **Tổng quan**, kéo xuống mục **Đăng nhập tài khoản**.
3. **WhatsApp**, chọn một trong hai cách:
   - Quét QR: trên điện thoại vào WhatsApp › **Cài đặt › Thiết bị đã liên kết › Liên kết thiết bị**, rồi quét mã trên màn hình.
   - Dùng mã số, tiện hơn khi điều khiển qua TeamViewer: bấm **Đăng nhập bằng mã số**, nhập số hotline, bấm **Lấy mã**. Mã 8 ký tự hiện trên trang; điện thoại sẽ nhận thông báo "Nhập mã", nhập mã đó vào.
4. **Zalo**: trên điện thoại mở app Zalo, bấm biểu tượng QR ở thanh tìm kiếm, quét mã trên trang, rồi **xác nhận đăng nhập** trên điện thoại.
5. Khi cả hai thẻ chuyển sang **Đã kết nối** là xong.

> Mã QR WhatsApp đổi khoảng 20 giây một lần, mã Zalo khoảng 100 giây; trang tự cập nhật mã mới. Qua TeamViewer, có thể chĩa camera điện thoại vào màn hình máy đang điều khiển để quét.

## 6. Kết nối Claude Desktop

1. Nếu máy chưa có Claude Desktop, tải tại https://claude.ai/download, cài và đăng nhập tài khoản Claude. Bộ cài đã ghi sẵn cấu hình, **không cần chạy lại bộ cài**.
2. **Thoát hẳn** Claude Desktop: chuột phải biểu tượng Claude ở khay hệ thống (góc phải dưới) › **Quit**. Đóng cửa sổ thôi là chưa đủ.
3. Mở lại Claude Desktop. Vào **Settings › Developer**: mục `wa-zalo-daily` phải ở trạng thái **running**.
4. Hỏi thử: `Gọi system_status của wa-zalo-daily`. Claude sẽ trả về trạng thái agent và số tin đã thu.

Một số câu hỏi mẫu:

- "Tổng hợp tin nhắn Zalo và WhatsApp hôm nay: ai hỏi gì, việc nào cần trả lời"
- "Liệt kê báo giá nhận được tuần này, so sánh đơn giá DALI-2 gateway"
- "Viết báo cáo tuần này và lưu lên trang quản trị". Để dùng mẫu soạn sẵn, vào trang **Báo cáo** và bấm **Sao chép lệnh cho Claude**.

## 7. Sử dụng hằng ngày

| Việc | Cách làm |
|---|---|
| Bật hệ thống | Bật máy, double-click **WA-Zalo Daily** |
| Mở trang quản trị | Double-click **WA-Zalo Admin**, hoặc vào http://127.0.0.1:8080/admin |
| Tắt hệ thống | Đóng hai cửa sổ **WA-Zalo Agent** và **WA-Zalo Cloud** |
| Cài đặt giờ làm việc | Trang quản trị › **Cài đặt**. Giờ làm việc dùng để tính thời gian phản hồi trong báo cáo |
| WhatsApp/Zalo báo "Mất kết nối" hoặc "Chờ quét QR" | Trang **Tổng quan** › **Đăng nhập tài khoản**, quét lại mã |

**Lưu ý:** agent chỉ thu tin **trong lúc đang chạy**. Tin đến khi máy tắt hoặc agent tắt sẽ không có trong dữ liệu và báo cáo.

**Cập nhật phiên bản mới:**

```powershell
cd $HOME\wa-zalo-claude
git pull
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Đóng hai cửa sổ WA-Zalo trước khi cập nhật. Cập nhật xong thì bật lại bằng shortcut.

## 8. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `Khong co winget` | Cài **App Installer** từ Microsoft Store rồi chạy lại bộ cài |
| `Khong tim thay Python 3.12` hoặc `Can Node.js 20+` ngay sau khi cài | Đóng PowerShell, mở cửa sổ mới, chạy lại bộ cài |
| Bộ cài hỏi `Nhap mat khau user 'postgres'` | Máy đã có sẵn PostgreSQL. Nhập mật khẩu user `postgres` của bản đó. Nếu không biết mật khẩu, liên hệ người cài PostgreSQL trước đó |
| `winget cai PostgreSQL.PostgreSQL.16 that bai` | Thường do bấm No ở hộp thoại UAC. Chạy lại bộ cài và bấm Yes |
| `npm install loi` có chữ `better-sqlite3` hoặc `gyp` | Chạy `winget install Microsoft.VisualStudio.2022.BuildTools`, rồi chạy lại bộ cài |
| `Cloud khong khoi dong duoc` | Xem file `data\install-cloud.err.log`. Hay gặp nhất: cổng 8080 đang bị chương trình khác dùng. Khi đó chạy bộ cài kèm `-ApiPort 8090` |
| Trang quản trị báo **Agent không chạy** | Cửa sổ WA-Zalo Agent đã bị đóng. Double-click lại **WA-Zalo Daily** |
| Trang quản trị không mở được | Cửa sổ WA-Zalo Cloud đã bị đóng hoặc báo lỗi. Mở cửa sổ đó xem dòng lỗi cuối, rồi double-click lại **WA-Zalo Daily** |
| Quên mật khẩu trang quản trị | Mở `cloud\.env`, xem dòng `ADMIN_UI_PASSWORD` |
| Claude Desktop không thấy `wa-zalo-daily` | Thoát hẳn Claude (Quit ở khay hệ thống) rồi mở lại. Nếu vẫn không thấy, chạy lại bộ cài sau khi đã cài Claude Desktop |
| Claude báo lỗi khi gọi tool | Kiểm tra trang quản trị mở được. Claude chỉ đọc được dữ liệu khi Cloud đang chạy |
| Bấm lệnh trên trang nhưng không có tác dụng | Agent hỏi lệnh 5 giây một lần. Nếu agent tắt, lệnh hết hạn sau 10 phút. Kiểm tra cửa sổ WA-Zalo Agent còn chạy không |
| File PDF báo "PDF scan, cần OCR" | PDF dạng ảnh scan nên không đọc được chữ. Vẫn tải file gốc được từ trang **File đính kèm** |

Gửi lỗi cho kỹ thuật: chụp màn hình cửa sổ PowerShell hoặc cửa sổ WA-Zalo đang báo lỗi, kèm file `data\install-cloud.err.log` nếu có.

## 9. File quan trọng và sao lưu

| Đường dẫn | Nội dung | Ghi chú |
|---|---|---|
| `cloud\.env` | Mật khẩu database, khoá mã hoá, token MCP, mật khẩu trang quản trị | **Không gửi file này cho ai**. Mất file này thì không đọc lại được secret của thiết bị |
| `agent\.env` | Mã thiết bị, khoá ký gửi tin lên Cloud | Không chia sẻ |
| `agent\data\` | Phiên đăng nhập WhatsApp/Zalo, hàng đợi tin chưa gửi | Xoá thư mục này thì phải quét QR lại |
| `data\files\` | File gốc khách gửi | |
| Database `wadaily` | Tin nhắn, danh sách file, báo cáo | Xem lệnh sao lưu bên dưới |

Sao lưu database. Mật khẩu là dòng `WADAILY_DB_PASSWORD` trong `cloud\.env`:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -h 127.0.0.1 -U wadaily -d wadaily -F c -f "$HOME\wadaily-$(Get-Date -Format yyyyMMdd).dump"
```

Muốn sao lưu đầy đủ thì chép thêm thư mục `data\files\` và hai file `.env`.

## 10. Gỡ cài đặt

1. Đóng hai cửa sổ WA-Zalo.
2. Trên điện thoại:
   - WhatsApp: vào **Thiết bị đã liên kết**, gỡ thiết bị của máy này.
   - Zalo: vào **Cài đặt › Tài khoản và bảo mật › Thiết bị đăng nhập**, đăng xuất thiết bị của máy này.
3. Xoá thư mục `$HOME\wa-zalo-claude` và hai shortcut trên Desktop.
4. Mở `%APPDATA%\Claude\claude_desktop_config.json` và xoá mục `wa-zalo-daily` trong `mcpServers`.
5. Nếu không dùng PostgreSQL cho việc khác, gỡ bằng lệnh `winget uninstall PostgreSQL.PostgreSQL.16`.
