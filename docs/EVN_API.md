# Ghi chú API EVN (dò được, đo 24/08/2026)

Tài liệu này gom các endpoint EVN mà integration đang dùng hoặc có thể dùng, để
sau khỏi phải dò lại. Tất cả gọi với header `Authorization: Bearer <token>` (trừ
bước đăng nhập). Bỏ verify SSL (`ssl=False`) như app.

## Cách dò lại khi cần (nếu EVN đổi endpoint)

Gateway trả `401` cho mọi path khi thiếu token và `404` sạch khi có token nhưng
path sai → **không đoán được bằng cách dò**. Cách chắc ăn: tải APK rồi grep chuỗi.

- App miền Bắc/chung: `com.evn.cskh.vn` — **React Native + Hermes**; chuỗi nằm
  trong `assets/index.android.bundle`, dùng `strings` moi ra được.
- App miền Nam: `vn.evnspc.cskh.cskhevnspc.CSKHEVNSPC` — **React Native, bundle JS
  thường** (không Hermes), grep trực tiếp ra URL + tên hàm + tham số.
- Tải APK: `curl -sD - https://d.apkpure.net/b/APK/<package>?version=latest` lấy
  redirect sang `data.winudf.com/...` rồi tải file đó (host `d.apkpure.com` hay bị
  chặn, dùng `d.apkpure.net`).

## Đăng nhập

| Vùng | Endpoint | Body |
|---|---|---|
| HN/NPC/CPC/HCMC (cổng chung) | `POST https://cskh.evn.com.vn/cskh/v1/auth/login` | `{"username","password","deviceInfo":{...}}` → `data.accessToken` |
| SPC (cổng riêng) | `POST https://api.cskh.evnspc.vn/api/user/authenticate` | `{"strUsername","strPassword","strDeviceID"}` → `token` |

Đổi tài khoản (cổng chung): `GET https://cskh.evn.com.vn/cskh/v1/user/switch/{maKH}`
→ token mới gắn với mã đó. Token JWT sống ~17 phút.

## Thông báo — miền Bắc / cổng chung (đang dùng)

`POST https://cskh.evn.com.vn/cskh/v1/notification/getAllByUser`, body `{}`.

- Trả về **mọi mã khách hàng của cùng số điện thoại** → phải lọc theo mã đang
  cấu hình (mã nằm trong `summary`). Tin không gắn mã (truyền thông) là tin chung.
- Mỗi tin: `id`, `stateId`, `title`, `summary`, `notificationType`, `createdDate`,
  `maDviqly`, `readStatus`, `readTime`.
- `notificationType`: `NGUNGCAP_DIEN`, `HOADON_TBAO_TTOAN`, `TRUYEN_THONG`.
  Danh mục chuẩn: `GET /cskh/v1/notification/getAllNotificationType`
  → `HOADON` (Hóa đơn), `TRUYEN_THONG` (Truyền thông), `NGUNGCAP_DIEN` (Ngừng điện).
- Lịch ngừng cấp điện nằm trong `summary` dạng chữ, parse bằng regex, ví dụ:
  `"... mã PM11000048612 thuộc ĐZ 35kV lộ 373E8.22 thời điểm từ 05h00 đến 11h30
  ngày 28/8/2026 để ĐL Kim Thành sửa chữa ..."`.

Các endpoint thông báo khác (chưa dùng, có trong bundle): `/cskh/v1/customer/notification/readall`,
`/customer/notification/unreadCount`, `/customer/notification/delete?id=`.

> **Quan trọng:** API tra cứu lịch ngừng cấp điện `/api/evn/tracuu/ngungcapdien`
> (POST `{TU_NGAY,DEN_NGAY}`) **trả rỗng** với các đợt cắt được thông báo trước.
> Với miền Bắc, nguồn thật của lịch cắt điện là feed thông báo trên.

## Thông báo — miền Nam (SPC) — CHƯA wire vào integration

Cùng cổng `https://api.cskh.evnspc.vn`, token từ `authenticate`.

| Mục | Endpoint | Tham số |
|---|---|---|
| Hộp thư thông báo (hóa đơn + ngừng điện) | `GET /api/NghiepVu/LayDanhSachThongBaoKhachHang` | `strMaKh={mã}`, `strRedId={FCM token, để rỗng cũng chạy}` |
| Tin bài / Truyền thông | `GET /api/NghiepVu/LayDanhSachTinBaiTheoLoai_v1` | `iLoaiTinBai={1\|2\|3}` |
| Thông báo trang chủ | `POST /api/NghiepVu/ThongBaoTrangChu_v1` | `{strDonVi, strMaKH}` |
| Lịch ngừng cấp điện (đang dùng) | `GET /api/NghiepVu/TraCuuLichNgungGiamCungCapDien` | `strMaKH={mã}` |

Trường một mục hộp thư (từ bundle): `strTieuDe`/`TieuDe`/`tieuDe` (tiêu đề),
`strNoiDung` (nội dung), `strNgayThongBao`/`ngayTao`/`NgayTao` (ngày),
`iTrangThai` (1 = đã đọc), `lIdThongBao`/`lidThongBao` (id), `strImage`/`urlHinhAnh`.

Khác app miền Bắc: **hộp thư SPC KHÔNG có `notificationType`** — danh sách phẳng,
muốn tách "Ngừng điện" vs "Hóa đơn" phải đoán theo chữ trong tiêu đề. "Truyền
thông" lấy từ endpoint tin bài riêng ở trên.

## Endpoint dữ liệu điện theo vùng (đã dùng trong integration)

Base URL: HN `https://gwkong.evnhanoi.vn`, NPC `https://apicskhevn.npc.com.vn`,
CPC `https://cskh-api.cpc.vn`, SPC `https://api.cskh.evnspc.vn`,
HCMC `https://cskh.evnhcmc.vn`. Chi tiết đường dẫn xem `custom_components/npc/npc_api.py`.
