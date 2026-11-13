# Hướng dẫn cài đặt wa-zalo-claude trên macOS

Cài cả hệ thống lên **một máy Mac** (Apple Silicon hoặc Intel):

- **Agent**: thu tin WhatsApp và Zalo.
- **Cloud**: lưu tin, đọc file PDF/Excel, chạy trang quản trị.
- **Claude Desktop**: kết nối với Cloud qua MCP để tổng hợp tin nhắn.

Cài lần đầu mất khoảng 15–30 phút; phần lớn thời gian là chờ tải phần mềm. Hướng dẫn cho Windows: [INSTALL.md](INSTALL.md).

## 0. Chuẩn bị

| Hạng mục | Yêu cầu |
|---|---|
| Máy | macOS bản còn được Homebrew hỗ trợ, RAM từ 8 GB (khuyên dùng 16 GB), trống ít nhất 5 GB |
| Mạng | Có Internet trong lúc cài |
| Quyền | Biết mật khẩu đăng nhập máy Mac (cần khi cài Homebrew lần đầu) |
| Điện thoại | Điện thoại đang đăng nhập WhatsApp và Zalo của số cần thu tin, dùng để quét QR |
| Điều khiển từ xa | Nếu cài qua TeamViewer: trên Mac cần cấp quyền **Screen Recording** và **Accessibility** cho TeamViewer, trong System Settings › Privacy & Security |

## 1. Mở Terminal

Bấm `Cmd + Space`, gõ `Terminal`, rồi bấm Enter.

## 2. Cài Homebrew (bỏ qua nếu máy đã có)

Kiểm tra máy đã có Homebrew chưa:

```bash
brew -v
```

Nếu báo `command not found`, cài Homebrew:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Khi được hỏi, nhập mật khẩu máy Mac. Lúc gõ, mật khẩu không hiện ra màn hình; đó là bình thường. Cài xong, Homebrew in ra mục **Next steps** gồm 2 lệnh `echo ... >> ~/.zprofile` và `eval ...`. **Chép và chạy đúng 2 lệnh đó**, rồi kiểm tra lại bằng `brew -v`.

## 3. Cài Git và tải mã nguồn

```bash
brew install git
git clone https://github.com/tungvu9x/wa-zalo-claude.git ~/wa-zalo-claude
cd ~/wa-zalo-claude
```

Thư mục cài đặt là `/Users/<tên user>/wa-zalo-claude`.

## 4. Chạy bộ cài

```bash
./install-mac.sh
```

Bộ cài tự làm lần lượt các bước sau. Mỗi bước in ra một dòng `==> ...`.

1. Cài **Python 3.12**, **Node.js** (nếu máy chưa có bản 20 trở lên) và **PostgreSQL 16** bằng Homebrew.
2. Bật PostgreSQL. Từ đó PostgreSQL tự chạy mỗi lần đăng nhập macOS.
3. Tạo database `wadaily`, sinh toàn bộ mật khẩu và khoá vào `cloud/.env` và `agent/.env`.
4. Cài thư viện Python và Node. Bước này tải cả Chromium cho WhatsApp, khoảng 150 MB.
5. Đăng ký máy này với Cloud.
6. Ghi cấu hình kết nối vào Claude Desktop. File cấu hình cũ được giữ lại với đuôi `.bak-...`.

Khi xong, màn hình hiện:

```
CAI DAT XONG
Trang quan tri: http://127.0.0.1:8080/admin   Mat khau: xxxxxxxxxxxx
```

**Ghi lại mật khẩu này.** Nếu quên, mở file `cloud/.env` và xem dòng `ADMIN_UI_PASSWORD`.

Nếu bộ cài dừng giữa chừng, xem mục [Xử lý sự cố](#8-xử-lý-sự-cố), sửa xong thì chạy lại `./install-mac.sh`. Bộ cài chạy lại nhiều lần được: mật khẩu và thiết bị đã tạo vẫn giữ nguyên.

## 5. Chạy lần đầu và đăng nhập WhatsApp/Zalo

1. Mở **Finder**, vào thư mục `wa-zalo-claude` trong thư mục người dùng, rồi double-click **start-mac.command**. Hoặc chạy `./start-mac.command` trong Terminal.
   - Cloud và Agent chạy nền. Đóng cửa sổ Terminal cũng không làm chúng dừng.
   - Trình duyệt tự mở trang quản trị. Đăng nhập bằng mật khẩu ở bước 4.
   - Nếu macOS báo không mở được file, chuột phải vào file › **Open** › **Open**.
2. Trên trang **Tổng quan**, kéo xuống mục **Đăng nhập tài khoản**.
3. **WhatsApp**, chọn một trong hai cách:
   - Quét QR: trên điện thoại vào WhatsApp › **Cài đặt › Thiết bị đã liên kết › Liên kết thiết bị**, rồi quét mã trên màn hình.
   - Dùng mã số: bấm **Đăng nhập bằng mã số**, nhập số điện thoại, bấm **Lấy mã**. Mã 8 ký tự hiện trên trang; điện thoại sẽ nhận thông báo "Nhập mã", nhập mã đó vào.
4. **Zalo**: trên điện thoại mở app Zalo, bấm biểu tượng QR ở thanh tìm kiếm, quét mã trên trang, rồi **xác nhận đăng nhập** trên điện thoại.
5. Khi cả hai thẻ chuyển sang **Đã kết nối** là xong.

## 6. Kết nối Claude Desktop

1. Nếu máy chưa có Claude Desktop, tải tại https://claude.ai/download, kéo vào thư mục Applications, mở lên và đăng nhập. Bộ cài đã ghi sẵn cấu hình, **không cần chạy lại bộ cài**.
2. **Thoát hẳn** Claude Desktop bằng `Cmd + Q`. Đóng cửa sổ thôi là chưa đủ.
3. Mở lại Claude Desktop. Vào **Settings › Developer**: mục `wa-zalo-daily` phải ở trạng thái **running**.
4. Hỏi thử: `Gọi system_status của wa-zalo-daily`.

Câu hỏi mẫu và cách viết báo cáo tuần giống bản Windows, xem [INSTALL.md › mục 6](INSTALL.md#6-kết-nối-claude-desktop).

## 7. Sử dụng hằng ngày

| Việc | Cách làm |
|---|---|
| Bật hệ thống | Double-click **start-mac.command** |
| Tắt hệ thống | Double-click **stop-mac.command** |
| Mở trang quản trị | http://127.0.0.1:8080/admin. Nên lưu thành bookmark |
| Xem log | `logs/cloud.log` và `logs/agent.log` trong thư mục cài đặt |
| Tự bật khi đăng nhập macOS | System Settings › General › **Login Items** › bấm **+** › chọn `start-mac.command` |

**Về chế độ ngủ:** agent chỉ thu tin **khi máy đang thức**. Trong lúc agent chạy, script đã chặn máy tự ngủ khi để yên. Nhưng **gập nắp MacBook thì máy vẫn ngủ**, và tin đến lúc đó sẽ không được thu. Nếu cần thu tin liên tục, để máy mở nắp và cắm sạc, hoặc dùng một máy luôn bật.

**Cập nhật phiên bản mới:**

```bash
cd ~/wa-zalo-claude
./stop-mac.command
git pull
./install-mac.sh
./start-mac.command
```

## 8. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `brew: command not found` ngay sau khi cài Homebrew | Chưa chạy 2 lệnh trong mục **Next steps** ở bước 2. Chạy `eval "$(/opt/homebrew/bin/brew shellenv)"` (máy Intel thì thay bằng `/usr/local/bin/brew`), rồi mở Terminal mới |
| `Permission denied` khi chạy `./install-mac.sh` | Chạy `chmod +x install-mac.sh start-mac.command stop-mac.command`, hoặc dùng `bash install-mac.sh` |
| `Khong dang nhap duoc PostgreSQL` | Máy đang có một PostgreSQL khác chiếm cổng 5432, ví dụ Postgres.app. Tắt bản đó đi, hoặc chạy `PG_PORT=5433 ./install-mac.sh` sau khi đặt PostgreSQL 16 chạy ở cổng 5433 |
| `Cloud khong khoi dong duoc` | Xem `logs/install-cloud.log`. Nếu cổng 8080 đang bị dùng, chạy `API_PORT=8090 ./install-mac.sh` |
| `npm install` lỗi có chữ `gyp` hoặc `xcrun` | Thiếu công cụ build. Chạy `xcode-select --install`, chờ cài xong rồi chạy lại bộ cài |
| Trang quản trị báo **Agent không chạy** | Agent đã dừng hoặc máy vừa ngủ dậy. Double-click lại **start-mac.command**, rồi xem `logs/agent.log` nếu agent tự dừng |
| Trang quản trị không mở được | Cloud đã dừng. Double-click **start-mac.command**; nếu vẫn lỗi, xem `logs/cloud.log` |
| Quên mật khẩu trang quản trị | `grep ADMIN_UI_PASSWORD ~/wa-zalo-claude/cloud/.env` |
| Claude Desktop không thấy `wa-zalo-daily` | Thoát bằng `Cmd + Q` rồi mở lại. Nếu vẫn không thấy, chạy lại `./install-mac.sh` sau khi đã cài Claude Desktop |
| Claude báo lỗi khi gọi tool | Kiểm tra trang quản trị mở được. Claude chỉ đọc được dữ liệu khi Cloud đang chạy |
| Lỗi Node sau khi đổi phiên bản Node (fnm/nvm) | Bộ cài đã ghi cố định đường dẫn Node vào `tools/env.sh`. Chạy lại `./install-mac.sh` để cập nhật |

Gửi lỗi cho kỹ thuật: chụp màn hình cửa sổ Terminal, kèm 50 dòng cuối của `logs/cloud.log` và `logs/agent.log`. Lấy bằng lệnh `tail -50 logs/cloud.log logs/agent.log`.

## 9. File quan trọng và sao lưu

| Đường dẫn | Nội dung | Ghi chú |
|---|---|---|
| `cloud/.env` | Mật khẩu database, khoá mã hoá, token MCP, mật khẩu trang quản trị | **Không gửi file này cho ai** |
| `agent/.env` | Mã thiết bị, khoá ký gửi tin lên Cloud | Không chia sẻ |
| `agent/data/` | Phiên đăng nhập WhatsApp/Zalo, hàng đợi tin chưa gửi | Xoá thư mục này thì phải quét QR lại |
| `data/files/` | File gốc khách gửi | |
| `tools/env.sh` | Đường dẫn Python, Node, PostgreSQL dùng khi khởi động | Bộ cài tạo lại mỗi lần chạy |
| Database `wadaily` | Tin nhắn, danh sách file, báo cáo | Xem lệnh sao lưu bên dưới |

Sao lưu database:

```bash
"$(brew --prefix postgresql@16)/bin/pg_dump" -h 127.0.0.1 -U wadaily -d wadaily -F c -f ~/wadaily-$(date +%Y%m%d).dump
```

Muốn sao lưu đầy đủ thì chép thêm thư mục `data/files/` và hai file `.env`.

## 10. Gỡ cài đặt

1. Chạy `./stop-mac.command`.
2. Trên điện thoại:
   - WhatsApp: vào **Thiết bị đã liên kết**, gỡ thiết bị của máy này.
   - Zalo: đăng xuất thiết bị của máy này trong phần quản lý thiết bị đăng nhập.
3. Xoá thư mục: `rm -rf ~/wa-zalo-claude`.
4. Mở `~/Library/Application Support/Claude/claude_desktop_config.json` và xoá mục `wa-zalo-daily` trong `mcpServers`.
5. Nếu không dùng PostgreSQL cho việc khác:

   ```bash
   brew services stop postgresql@16
   brew uninstall postgresql@16
   ```

   Lệnh trên chưa xoá dữ liệu database. Muốn xoá hẳn thì xoá thêm thư mục `$(brew --prefix)/var/postgresql@16`.
