"""API client for EVN VN (NPC, HN, CPC, SPC)"""

import asyncio
import logging
import time
import aiohttp
import json
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Máy chủ EVN hay lỗi/bảo trì hàng giờ liền (log HA 31/10–01/11/2026: 15 giờ liên tiếp, mỗi giờ 3 lệnh gọi đều 500 hoặc trả trang HTML). Lỗi phía EVN không phải
# lỗi của tích hợp: ghi gọn một dòng cảnh báo, mỗi endpoint chỉ nhắc lại sau _NHAC_LAI_GIAY, KHÔNG ghi payload/URL (chứa mã điểm đo của khách) và không in traceback.
_NHAC_LAI_GIAY = 6 * 3600
_LAN_BAO_CUOI: dict[str, float] = {}


def _bao_loi_may_chu_evn(ten: str, chi_tiet: str) -> None:
    bay_gio = time.monotonic()
    if bay_gio - _LAN_BAO_CUOI.get(ten, -_NHAC_LAI_GIAY) < _NHAC_LAI_GIAY:
        _LOGGER.debug("%s: máy chủ EVN vẫn lỗi (%s)", ten, chi_tiet)
        return
    _LAN_BAO_CUOI[ten] = bay_gio
    _LOGGER.warning("%s: máy chủ EVN lỗi hoặc đang bảo trì (%s); dữ liệu cũ được giữ, thử lại ở chu kỳ sau "
                    "(chỉ nhắc lại sau %d giờ nếu vẫn lỗi)", ten, chi_tiet, _NHAC_LAI_GIAY // 3600)


def _ghi_loi_http(ten: str, status: int, noi_dung: str = "") -> None:
    """Lỗi HTTP của một lệnh gọi EVN: 5xx = lỗi phía EVN (cảnh báo gọn, không lặp); còn lại (401 sau khi đăng nhập lại, 4xx…) vẫn là ERROR kèm đoạn đầu phản hồi."""
    if status >= 500:
        _bao_loi_may_chu_evn(ten, f"HTTP {status}")
    else:
        _LOGGER.error("%s failed with status %s, response: %s", ten, status, noi_dung[:300])


def _ghi_ngoai_le(ten: str, loi: Exception) -> None:
    """Ngoại lệ ngoài cùng của một hàm lấy dữ liệu. Lỗi mạng/trả sai kiểu (EVN trả HTML) là lỗi phía EVN; còn lại là lỗi thật, giữ traceback."""
    if isinstance(loi, aiohttp.ContentTypeError):
        _bao_loi_may_chu_evn(ten, "trả trang HTML thay vì JSON")
    elif isinstance(loi, (aiohttp.ClientError, asyncio.TimeoutError)):
        _bao_loi_may_chu_evn(ten, type(loi).__name__)
    else:
        _LOGGER.error("%s error: %s", ten, loi, exc_info=True)

# Base URLs for different regions
EVN_REGIONS = {
    "HN": "https://gwkong.evnhanoi.vn",
    "NPC": "https://apicskhevn.npc.com.vn",
    "CPC": "https://cskh-api.cpc.vn",
    "SPC": "https://api.cskh.evnspc.vn",
    "HCMC": "https://cskh.evnhcmc.vn",  # HCMC dùng base URL riêng cho API data
}

# Common login URL
LOGIN_URL = "https://cskh.evn.com.vn/cskh/v1/auth/login"

# SPC có cổng đăng nhập riêng: tài khoản SPC không tồn tại trên cổng chung
# cskh.evn.com.vn nên phải xác thực thẳng ở api.cskh.evnspc.vn
SPC_LOGIN_URL = "https://api.cskh.evnspc.vn/api/user/authenticate"

# Feed "Thông báo" dùng chung của app EVN (cùng cổng đăng nhập cskh.evn.com.vn).
# Lịch ngừng cấp điện BÁO TRƯỚC chỉ xuất hiện ở đây; API tra cứu lịch ngừng cấp
# điện (ngungcapdien) trả về rỗng với các đợt cắt được thông báo. getAllByUser
# trả về mọi mã khách hàng của cùng số điện thoại nên phải lọc theo mã đang cấu
# hình (xem coordinator._save_notification_outages).
NOTIFICATION_URL = "https://cskh.evn.com.vn/cskh/v1/notification/getAllByUser"

# Chi tiết một thông báo: nội dung đầy đủ nằm ở data.body (HTML).
# Feed getAllByUser chỉ trả summary là bản xem trước bị cắt ngắn (~200 ký tự),
# nên khi parse summary không ra ngày/giờ thì gọi thêm endpoint này để lấy
# toàn bộ nội dung rồi parse lại (xem coordinator._save_notification_outages).
NOTIFICATION_DETAIL_URL = "https://cskh.evn.com.vn/cskh/v1/customer/notificationDetail"


class EVNAPI:
    """EVN API Client"""

    def __init__(self, hass, region: str, username: str, password: str, customer_id: str):
        """Initialize EVN API client."""
        self.hass = hass
        self.region = region.upper()
        self.username = username
        self.password = password
        self.customer_id = customer_id
        self.base_url = EVN_REGIONS.get(self.region)
        self.access_token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self.ma_dviqly: Optional[str] = None  # Lưu từ login response
        self.ma_ddo: Optional[str] = None  # Lưu từ login response (maKhang hoặc maHdong)
        self.ma_khang: Optional[str] = None  # Lưu từ login response
        self.ten_khang: Optional[str] = None  # Tên khách hàng, từ login response
        self.dien_thoai: Optional[str] = None  # Số điện thoại, từ login response
        self.dia_chi: Optional[str] = None  # Địa chỉ, từ login response
        self.hcmc_session: Optional[str] = None  # Session cookie cho HCMC

        if not self.base_url:
            raise ValueError(f"Invalid region: {region}")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_ma_dviqly_and_ma_ddo(self):
        """Get MA_DVIQLY and MA_DDO for API payload based on region.
        
        Returns:
            tuple: (MA_DVIQLY, MA_DDO)
        """
        if self.region == "HN":
            # HN: dùng customer_id[:6] và customer_id + "001"
            # (như nestup_evn)
            ma_dviqly = self.customer_id[:6] if self.customer_id else ""
            ma_ddo = f"{self.customer_id}001" if self.customer_id else ""
        elif self.region == "NPC":
            # NPC: dùng customer_id[:6] và customer_id + "001"
            # (như nestup_evn)
            ma_dviqly = self.customer_id[:6] if self.customer_id else ""
            ma_ddo = f"{self.customer_id}001" if self.customer_id else ""
        elif self.region == "CPC":
            # CPC: dùng customer_id[:6] và customer_id + "001"
            # (như test đã xác nhận)
            ma_dviqly = self.customer_id[:6] if self.customer_id else ""
            ma_ddo = f"{self.customer_id}001" if self.customer_id else ""
        elif self.region == "SPC":
            # SPC: dùng maKhang để extract (cho các API khác)
            # Nhưng với chisongay, SPC dùng endpoint riêng với strMaDiemDo
            if self.ma_dviqly and self.ma_ddo:
                ma_dviqly = self.ma_dviqly
                ma_ddo = self.ma_ddo
            else:
                # Fallback: extract từ customer_id
                ma_dviqly = self.customer_id[:6] if self.customer_id else ""
                ma_ddo = self.customer_id if self.customer_id else ""
        else:
            # Fallback: extract từ customer_id
            ma_dviqly = self.customer_id[:6] if self.customer_id else ""
            ma_ddo = self.customer_id if self.customer_id else ""
        
        return ma_dviqly, ma_ddo

    async def login(self) -> bool:
        """Login to EVN and get access token."""
        if self.region == "SPC":
            return await self._login_spc()

        try:
            session = await self._get_session()

            payload = {
                "username": self.username,
                "password": self.password,
                "deviceInfo": {
                    "deviceId": f"ha-{self.customer_id}",
                    "deviceType": "Android/HomeAssistant",
                },
            }

            headers = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "user-agent": "okhttp/4.12.0",
                "connection": "Keep-Alive",
            }

            async with session.post(LOGIN_URL, json=payload, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"Login failed with status {resp.status}")
                    return False

                data = await resp.json()
                
                if not data.get("success") or "data" not in data:
                    _LOGGER.error(f"Login failed: {data}")
                    return False

                access_token = data["data"].get("accessToken")
                if not access_token:
                    _LOGGER.error("No access token in login response")
                    return False

                # Lưu maKhang từ login response
                user_data = data["data"].get("data", {})
                ma_kh_login = user_data.get("maKhang", "")
                self.ma_khang = ma_kh_login
                self.ten_khang = user_data.get("tenKhang")
                self.dien_thoai = user_data.get("dthoai")
                self.dia_chi = user_data.get("diaChi")
                
                # Với HN: không dùng maDviqly/maHdong từ login,
                # sẽ dùng customer_id trực tiếp
                # Với NPC/CPC/SPC: dùng maKhang để extract
                if self.region == "HN":
                    # HN: không lưu ma_dviqly/ma_ddo,
                    # sẽ dùng customer_id trực tiếp trong API calls
                    self.ma_dviqly = None
                    self.ma_ddo = None
                elif ma_kh_login:
                    # NPC/CPC/SPC: dùng maKhang để extract
                    self.ma_dviqly = ma_kh_login[:6]
                    self.ma_ddo = ma_kh_login
                else:
                    # Fallback: extract từ customer_id
                    self.ma_dviqly = (
                        self.customer_id[:6] if self.customer_id else ""
                    )
                    self.ma_ddo = self.customer_id if self.customer_id else ""

                if ma_kh_login and ma_kh_login != self.customer_id:
                    _LOGGER.info(f"Switching account from {ma_kh_login} to {self.customer_id}")
                    if not await self._switch_account(access_token):
                        return False
                    # Sau khi switch, maKhang mới đã được cập nhật trong _switch_account
                else:
                    self.access_token = access_token

                # HCMC cần lấy session cookie từ login endpoint riêng
                if self.region == "HCMC":
                    if not await self._login_hcmc_session():
                        _LOGGER.error("Failed to get HCMC session cookie")
                        return False

                if self.region == "HN":
                    _LOGGER.info(
                        f"Login successful for {self.customer_id} "
                        "(HN: will use customer_id[:6] and customer_id+'001')"
                    )
                elif self.region == "HCMC":
                    _LOGGER.info(
                        f"Login successful for {self.customer_id} "
                        f"(HCMC: session cookie obtained)"
                    )
                else:
                    _LOGGER.info(
                        f"Login successful for {self.customer_id}, "
                        f"maDviqly={self.ma_dviqly}, maDdo={self.ma_ddo}"
                    )
                return True

        except Exception as e:
            _LOGGER.error(f"Login error: {e}", exc_info=True)
            return False

    async def _login_spc(self) -> bool:
        """Login to EVNSPC endpoint to get access token.

        SPC không dùng cổng đăng nhập chung cskh.evn.com.vn. Endpoint riêng
        trả về token cùng maKH/maDonVi của khách hàng.
        """
        try:
            session = await self._get_session()

            payload = {
                "strUsername": self.username,
                "strPassword": self.password,
                "strDeviceID": self.customer_id,
            }

            headers = {
                "accept": "application/json",
                "content-type": "application/json",
                "user-agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
                "accept-language": "vi-vn",
                "connection": "keep-alive",
            }

            async with session.post(
                SPC_LOGIN_URL, json=payload, headers=headers, ssl=False
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"SPC login failed with status {resp.status}")
                    return False

                data = await resp.json(content_type=None)

                token = data.get("token") if isinstance(data, dict) else None
                ma_kh_login = data.get("maKH", "") if isinstance(data, dict) else ""

                # SPC trả về 200 với maKH rỗng khi sai tài khoản/mật khẩu
                if not token or not ma_kh_login:
                    _LOGGER.error(f"SPC login failed: {data}")
                    return False

                self.access_token = token
                self.ma_khang = ma_kh_login
                self.ma_dviqly = data.get("maDonVi") or ma_kh_login[:6]
                self.ma_ddo = ma_kh_login
                self.ten_khang = data.get("tenKH")
                self.dien_thoai = data.get("dienThoai")

                _LOGGER.info(
                    f"Login successful for {self.customer_id}, "
                    f"maDviqly={self.ma_dviqly}, maDdo={self.ma_ddo}"
                )
                return True

        except Exception as e:
            _LOGGER.error(f"SPC login error: {e}", exc_info=True)
            return False

    async def _login_hcmc_session(self) -> bool:
        """Login to HCMC endpoint to get session cookie.
        
        HCMC requires a separate login to get session cookie for API calls.
        This is called after successful login with common base URL.
        """
        try:
            session = await self._get_session()
            hcmc_login_url = "https://cskh.evnhcmc.vn/Dangnhap/checkLG"
            
            payload = {"u": self.username, "p": self.password}
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            
            async with session.post(hcmc_login_url, data=payload, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"HCMC login failed with status {resp.status}")
                    return False
                
                # Lấy session cookie từ HCMC login (giống test_hcmc.py)
                cookies = resp.headers.get("Set-Cookie", "")
                evn_session = None
                for cookie in cookies.split(";"):
                    if "evn_session=" in cookie:
                        evn_session = cookie.split("evn_session=")[-1].strip()
                        if ";" in evn_session:
                            evn_session = evn_session.split(";")[0]
                
                if evn_session:
                    self.hcmc_session = evn_session
                    _LOGGER.info("HCMC session cookie obtained successfully")
                    return True
                else:
                    _LOGGER.error("No evn_session cookie in HCMC login response")
                    return False
                    
        except Exception as e:
            _LOGGER.error(f"HCMC login error: {e}", exc_info=True)
            return False

    async def _switch_account(self, token: str) -> bool:
        """Switch to different customer account."""
        try:
            session = await self._get_session()
            switch_url = f"https://cskh.evn.com.vn/cskh/v1/user/switch/{self.customer_id}"

            headers = {
                "accept": "application/json, text/plain, */*",
                "accept-encoding": "gzip",
                "connection": "Keep-Alive",
                "user-agent": "okhttp/4.12.0",
                "authorization": f"Bearer {token}",
            }

            async with session.get(switch_url, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    _LOGGER.error(f"Switch account failed with status {resp.status}")
                    return False

                data = await resp.json()
                
                if not data.get("success") or "data" not in data:
                    _LOGGER.error(f"Switch account failed: {data}")
                    return False

                new_token = data["data"].get("accessToken")
                if not new_token:
                    _LOGGER.error("No access token in switch response")
                    return False

                self.access_token = new_token
                
                # Lấy lại maKhang từ switch response (chỉ để tham khảo)
                switch_user_data = data["data"].get("data", {})
                self.ma_khang = switch_user_data.get("maKhang", "")
                self.ten_khang = switch_user_data.get("tenKhang")
                self.dien_thoai = switch_user_data.get("dthoai")
                self.dia_chi = switch_user_data.get("diaChi")
                
                # Với HN: không lưu ma_dviqly/ma_ddo, sẽ dùng customer_id trực tiếp trong API calls
                # Với NPC/CPC/SPC: dùng maKhang để extract
                if self.region == "HN":
                    # HN: không lưu ma_dviqly/ma_ddo
                    self.ma_dviqly = None
                    self.ma_ddo = None
                elif self.ma_khang:
                    # NPC/CPC/SPC: dùng maKhang để extract MA_DVIQLY và MA_DDO
                    self.ma_dviqly = self.ma_khang[:6]
                    self.ma_ddo = self.ma_khang
                else:
                    # Fallback: extract từ customer_id
                    self.ma_dviqly = self.customer_id[:6] if self.customer_id else ""
                    self.ma_ddo = self.customer_id if self.customer_id else ""
                
                _LOGGER.info(f"Account switched successfully to {self.customer_id}, maDviqly={self.ma_dviqly}, maDdo={self.ma_ddo}")
                return True

        except Exception as e:
            _LOGGER.error(f"Switch account error: {e}", exc_info=True)
            return False

    def _convert_spc_to_standard_format(self, records: list) -> list:
        """Convert SPC API response format to standard format.
        
        SPC format: {
            "strTime": "dd/mm/yyyy",
            "dGiaoBT": 1234.56,
            "dSanLuongBT": 10.5
        }
        
        Standard format: {
            "NGAY": "dd/mm/yyyy",
            "CHISO_MOI": 1234.56,
            "DIEN_TIEU_THU": 10.5
        }
        """
        converted = []
        for record in records:
            if not isinstance(record, dict):
                continue
            
            converted_record = {}
            # Copy all existing fields
            converted_record.update(record)
            
            # Convert strTime -> NGAY
            if "strTime" in record:
                converted_record["NGAY"] = record["strTime"]
            
            # Convert dGiaoBT -> CHISO_MOI and CHISO
            # Bản ghi gộp nhiều ngày (strTime dạng "08/10/2025-09/10/2025")
            # không có chỉ số chốt, dGiaoBT = 0: bỏ qua để không ghi đè
            # chuỗi chỉ số bằng số 0.
            if record.get("dGiaoBT"):
                converted_record["CHISO_MOI"] = record["dGiaoBT"]
                converted_record["CHISO"] = record["dGiaoBT"]
            
            # Convert dSanLuongBT -> DIEN_TIEU_THU and SAN_LUONG
            if "dSanLuongBT" in record:
                converted_record["DIEN_TIEU_THU"] = record["dSanLuongBT"]
                converted_record["SAN_LUONG"] = record["dSanLuongBT"]
            
            converted.append(converted_record)
        
        return converted

    def _convert_spc_outage_to_standard_format(self, records: list) -> list:
        """Convert SPC outage API response format to standard format.
        
        SPC format: {
            "strTuNgay": "08:00:00 ngày 01/02/2026",
            "strDenNgay": "08:15:00 ngày 01/02/2026",
            "strThoiGianMatDien": "từ 08:00:00 ngày 01/02/2026 đến 08:15:00 ngày 01/02/2026",
            "strLyDoMatDien": "Bảo trì, sửa chữa lưới điện",
            "strDiaChi": "14.AB/38-39/7.H/1.H-T473-KP Tân Trà..."
        }
        
        Standard format: {
            "NGAY_BAT_DAU": "01/02/2026",
            "NGAY_KET_THUC": "01/02/2026",
            "THOI_GIAN_BAT_DAU": "08:00:00",
            "THOI_GIAN_KET_THUC": "08:15:00",
            "LY_DO": "Bảo trì, sửa chữa lưới điện",
            "DIA_CHI": "14.AB/38-39/7.H/1.H-T473-KP Tân Trà..."
        }
        """
        converted = []
        for record in records:
            if not isinstance(record, dict):
                continue
            
            converted_record = {}
            # Copy all existing fields
            converted_record.update(record)
            
            # Parse strTuNgay: "08:00:00 ngày 01/02/2026" -> NGAY_BAT_DAU="01/02/2026", THOI_GIAN_BAT_DAU="08:00:00"
            if "strTuNgay" in record and record["strTuNgay"]:
                tu_ngay = str(record["strTuNgay"]).strip()
                # Extract time and date: "08:00:00 ngày 01/02/2026"
                if "ngày" in tu_ngay:
                    parts = tu_ngay.split("ngày")
                    if len(parts) == 2:
                        time_part = parts[0].strip()
                        date_part = parts[1].strip()
                        converted_record["THOI_GIAN_BAT_DAU"] = time_part
                        converted_record["NGAY_BAT_DAU"] = date_part
                        converted_record["NGAY"] = date_part  # Also set NGAY for compatibility
            
            # Parse strDenNgay: "08:15:00 ngày 01/02/2026" -> NGAY_KET_THUC="01/02/2026", THOI_GIAN_KET_THUC="08:15:00"
            if "strDenNgay" in record and record["strDenNgay"]:
                den_ngay = str(record["strDenNgay"]).strip()
                # Extract time and date: "08:15:00 ngày 01/02/2026"
                if "ngày" in den_ngay:
                    parts = den_ngay.split("ngày")
                    if len(parts) == 2:
                        time_part = parts[0].strip()
                        date_part = parts[1].strip()
                        converted_record["THOI_GIAN_KET_THUC"] = time_part
                        converted_record["NGAY_KET_THUC"] = date_part
            
            # Convert strLyDoMatDien -> LY_DO
            if "strLyDoMatDien" in record:
                converted_record["LY_DO"] = record["strLyDoMatDien"]
                converted_record["ly_do"] = record["strLyDoMatDien"]
            
            # Convert strDiaChi -> DIA_CHI and KHU_VUC
            if "strDiaChi" in record:
                converted_record["DIA_CHI"] = record["strDiaChi"]
                converted_record["dia_chi"] = record["strDiaChi"]
                converted_record["KHU_VUC"] = record["strDiaChi"]
                converted_record["khu_vuc"] = record["strDiaChi"]
            
            converted.append(converted_record)
        
        return converted

    def _convert_hcmc_to_standard_format(self, records: list) -> list:
        """Convert HCMC API response format to standard format.
        
        HCMC format (từ ajax_dienNangTieuThuTheoNgay):
        {
            "ngay": "01/12",
            "ngayFull": "01/12/2025",
            "TD": 0.09,
            "BT": 0.2,
            "CD": 0.07,
            "Tong": 0.36,
            "p_giao_bt": "8,568.63",
            "tong_p_giao": "15,248.39"
        }
        
        Standard format:
        {
            "NGAY": "01/12/2025",
            "CHISO_MOI": 15248.39,
            "CHISO": 15248.39,
            "DIEN_TIEU_THU": 0.36,
            "SAN_LUONG": 0.36
        }
        """
        converted = []
        for record in records:
            if not isinstance(record, dict):
                continue
            
            converted_record = {}
            # Copy all existing fields
            converted_record.update(record)
            
            # Convert ngayFull -> NGAY
            if "ngayFull" in record:
                converted_record["NGAY"] = record["ngayFull"]
            
            # Convert tong_p_giao -> CHISO_MOI and CHISO
            if "tong_p_giao" in record:
                try:
                    # Remove commas and convert to float
                    chi_so_str = str(record["tong_p_giao"]).replace(",", "").strip()
                    chi_so = float(chi_so_str)
                    converted_record["CHISO_MOI"] = chi_so
                    converted_record["CHISO"] = chi_so
                except (ValueError, TypeError):
                    pass
            
            # Convert Tong -> DIEN_TIEU_THU and SAN_LUONG
            if "Tong" in record:
                try:
                    tieu_thu = float(record["Tong"])
                    converted_record["DIEN_TIEU_THU"] = tieu_thu
                    converted_record["SAN_LUONG"] = tieu_thu
                except (ValueError, TypeError):
                    pass
            
            converted.append(converted_record)
        
        return converted

    def _convert_cpc_outage_to_standard_format(self, records: list) -> list:
        """Convert CPC outage API response format to standard format.
        
        CPC format: {
            "TGIAN_BDAU": "05/12/2025 05:30",
            "TGIAN_KTHUC": "05/12/2025 17:00",
            "LY_DO": "Đội QLĐ Cẩm Lệ...",
            "KHUVUCMATDIEN": "ĐZ 483HXU:..."
        }
        
        Standard format: {
            "NGAY_BAT_DAU": "05/12/2025",
            "NGAY_KET_THUC": "05/12/2025",
            "THOI_GIAN_BAT_DAU": "05:30",
            "THOI_GIAN_KET_THUC": "17:00",
            "LY_DO": "Đội QLĐ Cẩm Lệ...",
            "KHU_VUC": "ĐZ 483HXU:..."
        }
        """
        converted = []
        for record in records:
            if not isinstance(record, dict):
                continue
            
            converted_record = {}
            # Copy all existing fields
            converted_record.update(record)
            
            # Parse TGIAN_BDAU: "05/12/2025 05:30" -> NGAY_BAT_DAU="05/12/2025", THOI_GIAN_BAT_DAU="05:30"
            if "TGIAN_BDAU" in record and record["TGIAN_BDAU"]:
                tgian_bdau = str(record["TGIAN_BDAU"]).strip()
                # Extract date and time: "05/12/2025 05:30"
                if " " in tgian_bdau:
                    parts = tgian_bdau.split(" ", 1)
                    if len(parts) == 2:
                        date_part = parts[0].strip()
                        time_part = parts[1].strip()
                        converted_record["NGAY_BAT_DAU"] = date_part
                        converted_record["THOI_GIAN_BAT_DAU"] = time_part
                        converted_record["NGAY"] = date_part  # Also set NGAY for compatibility
            
            # Parse TGIAN_KTHUC: "05/12/2025 17:00" -> NGAY_KET_THUC="05/12/2025", THOI_GIAN_KET_THUC="17:00"
            if "TGIAN_KTHUC" in record and record["TGIAN_KTHUC"]:
                tgian_kthuc = str(record["TGIAN_KTHUC"]).strip()
                # Extract date and time: "05/12/2025 17:00"
                if " " in tgian_kthuc:
                    parts = tgian_kthuc.split(" ", 1)
                    if len(parts) == 2:
                        date_part = parts[0].strip()
                        time_part = parts[1].strip()
                        converted_record["NGAY_KET_THUC"] = date_part
                        converted_record["THOI_GIAN_KET_THUC"] = time_part
            
            # Convert LY_DO -> LY_DO (giữ nguyên vì đã đúng format)
            if "LY_DO" in record:
                converted_record["LY_DO"] = record["LY_DO"]
                converted_record["ly_do"] = record["LY_DO"]
            
            # Convert KHUVUCMATDIEN -> KHU_VUC and DIA_CHI
            if "KHUVUCMATDIEN" in record:
                converted_record["KHU_VUC"] = record["KHUVUCMATDIEN"]
                converted_record["khu_vuc"] = record["KHUVUCMATDIEN"]
                converted_record["DIA_CHI"] = record["KHUVUCMATDIEN"]
                converted_record["dia_chi"] = record["KHUVUCMATDIEN"]
            
            converted.append(converted_record)
        
        return converted

    async def get_chisongay(
        self, from_date: str, to_date: str
    ) -> Optional[Dict[str, Any]]:
        """Get daily consumption data.
        
        Args:
            from_date: Format dd/mm/yyyy
            to_date: Format dd/mm/yyyy
            
        Returns:
            Dict with data or None
        """
        if not self.access_token:
            if not await self.login():
                return None

        try:
            session = await self._get_session()
            
            # HCMC dùng endpoint và format riêng
            if self.region == "HCMC":
                if not self.hcmc_session:
                    if not await self.login():
                        return None
                
                url = f"{self.base_url}/Tracuu/ajax_dienNangTieuThuTheoNgay"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Cookie": f"evn_session={self.hcmc_session}",
                }
                
                payload = {
                    "input_makh": self.customer_id,
                    "input_tungay": from_date,
                    "input_denngay": to_date,
                }
                
                _LOGGER.debug(f"get_chisongay (HCMC): URL={url}, payload={payload}, region={self.region}")
                
                async with session.post(url, data=payload, headers=headers, ssl=False) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_chisongay", resp.status, error_text)
                        return None
                    
                    # HCMC API trả về JSON nhưng Content-Type là text/html
                    try:
                        response_text = await resp.text(encoding='utf-8', errors='replace')
                        data = json.loads(response_text)
                    except Exception as e:
                        _LOGGER.error(f"Failed to parse HCMC response: {e}")
                        return None
                    
                    # Xử lý response theo format HCMC
                    if data.get("state") == "success" and "data" in data:
                        records = data["data"].get("sanluong_tungngay", [])
                        if isinstance(records, list):
                            converted_data = self._convert_hcmc_to_standard_format(records)
                            return {"data": converted_data}
                    
                    _LOGGER.error(f"HCMC get_chisongay: Invalid response format: {data}")
                    return None
            
            # SPC dùng endpoint và format riêng
            elif self.region == "SPC":
                from datetime import datetime, timedelta
                # Convert dd/mm/yyyy to YYYYMMDD format (như nestup_evn: from_date - 1 ngày)
                from_date_obj = datetime.strptime(from_date, "%d/%m/%Y") - timedelta(days=1)
                to_date_obj = datetime.strptime(to_date, "%d/%m/%Y")
                from_date_str = from_date_obj.strftime("%Y%m%d")
                to_date_str = to_date_obj.strftime("%Y%m%d")
                
                url = f"{self.base_url}/api/NghiepVu/LayThongTinSanLuongTheoNgay_v2"
                params = {
                    "strMaDiemDo": f"{self.customer_id}001",
                    "strFromDate": from_date_str,
                    "strToDate": to_date_str,
                }
                headers = {
                    "accept": "application/json, text/plain, */*",
                    "user-agent": "okhttp/4.12.0",
                    "authorization": f"Bearer {self.access_token}",
                }
                
                _LOGGER.debug(f"get_chisongay (SPC): URL={url}, params={params}, region={self.region}")
                
                async with session.get(url, params=params, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        # Token expired, try login again
                        if await self.login():
                            headers["authorization"] = f"Bearer {self.access_token}"
                            async with session.get(url, params=params, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    error_text = await retry_resp.text()
                                    _ghi_loi_http("get_chisongay", retry_resp.status, error_text)
                                    return None
                                data = await retry_resp.json()
                                # SPC trả về list trực tiếp, chuyển đổi format và wrap vào dict với key "data"
                                if isinstance(data, list):
                                    converted_data = self._convert_spc_to_standard_format(data)
                                    return {"data": converted_data}
                                return data
                        return None

                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_chisongay", resp.status, error_text)
                        return None

                    data = await resp.json()
                    # SPC trả về list trực tiếp, chuyển đổi format và wrap vào dict với key "data"
                    if isinstance(data, list):
                        converted_data = self._convert_spc_to_standard_format(data)
                        return {"data": converted_data}
                    return data
            else:
                # Các region khác dùng endpoint chung
                url = f"{self.base_url}/api/evn/tracuu/chisongay"

                # Lấy MA_DVIQLY và MA_DDO dựa trên region
                ma_dviqly, ma_ddo = self._get_ma_dviqly_and_ma_ddo()

                payload = {
                    "MA_DVIQLY": ma_dviqly,
                    "MA_DDO": ma_ddo,
                    "TU_NGAY": from_date,
                    "DEN_NGAY": to_date,
                }

                headers = {
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/json",
                    "user-agent": "okhttp/4.12.0",
                    "authorization": f"Bearer {self.access_token}",
                }

                _LOGGER.debug(f"get_chisongay: URL={url}, payload={payload}, region={self.region}")

                async with session.post(url, json=payload, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        # Token expired, try login again
                        if await self.login():
                            headers["authorization"] = f"Bearer {self.access_token}"
                            async with session.post(url, json=payload, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    error_text = await retry_resp.text()
                                    _ghi_loi_http("get_chisongay", retry_resp.status, error_text)
                                    return None
                                return await retry_resp.json()
                        return None

                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_chisongay", resp.status, error_text)
                        return None

                    return await resp.json()

        except Exception as e:
            _ghi_ngoai_le("get_chisongay", e)
            return None

    async def get_chisothang(
        self, month: int, year: int
    ) -> Optional[Dict[str, Any]]:
        """Get monthly consumption data.
        
        Args:
            month: Month (1-12)
            year: Year
            
        Returns:
            Dict with data or None
        """
        if not self.access_token:
            if not await self.login():
                return None

        try:
            # HCMC và SPC tính từ dữ liệu ngày (như nestup_evn)
            if self.region == "HCMC" or self.region == "SPC":
                from datetime import datetime, timedelta
                from calendar import monthrange
                
                # Lấy dữ liệu ngày cho cả tháng
                month_start = datetime(year, month, 1)
                _, last_day = monthrange(year, month)
                month_end = datetime(year, month, last_day)

                # Cần cả chỉ số ngày cuối tháng trước làm chỉ số đầu kỳ.
                # SPC: get_chisongay đã tự lùi 1 ngày, HCMC thì chưa.
                if self.region == "SPC":
                    from_date = month_start.strftime("%d/%m/%Y")
                else:
                    from_date = (month_start - timedelta(days=1)).strftime("%d/%m/%Y")
                to_date = month_end.strftime("%d/%m/%Y")
                
                daily_data = await self.get_chisongay(from_date, to_date)
                if not daily_data or not daily_data.get("data"):
                    _LOGGER.error(f"get_chisothang: Failed to get daily data for {self.region}")
                    return None
                
                records = daily_data["data"]
                if not isinstance(records, list) or len(records) == 0:
                    _LOGGER.error(f"get_chisothang: No daily records for {self.region}")
                    return None
                
                # Tính chỉ số tháng
                first_record = records[0]
                last_record = records[-1]
                
                # HCMC dùng CHISO_MOI, SPC dùng dGiaoBT
                if self.region == "HCMC":
                    chi_so_cu = float(first_record.get("CHISO_MOI", 0) or first_record.get("CHISO", 0))
                    chi_so_moi = float(last_record.get("CHISO_MOI", 0) or last_record.get("CHISO", 0))
                    chi_so_thang = round(chi_so_moi - chi_so_cu, 2)
                    
                    # Parse ngày từ response HCMC
                    ngay_dau = first_record.get("NGAY", first_record.get("ngayFull", ""))
                    ngay_cuoi = last_record.get("NGAY", last_record.get("ngayFull", ""))
                    if ngay_dau:
                        from_date_parsed = datetime.strptime(ngay_dau, "%d/%m/%Y") + timedelta(days=1)
                    else:
                        from_date_parsed = month_start
                    if ngay_cuoi:
                        to_date_parsed = datetime.strptime(ngay_cuoi, "%d/%m/%Y")
                    else:
                        to_date_parsed = month_end
                else:  # SPC
                    d_giao_bt_old = float(first_record.get("dGiaoBT", 0))
                    d_giao_bt_new = float(last_record.get("dGiaoBT", 0))
                    chi_so_thang = round(d_giao_bt_new - d_giao_bt_old, 2)
                    chi_so_cu = d_giao_bt_old
                    chi_so_moi = d_giao_bt_new
                    
                    # Parse ngày từ response SPC
                    from_date_parsed = datetime.strptime(first_record.get("strTime", ""), "%d/%m/%Y") + timedelta(days=1)
                    to_date_parsed = datetime.strptime(last_record.get("strTime", ""), "%d/%m/%Y")
                
                # Trả về đúng format của API chisothang chung: data là list
                # bản ghi với các key CHISO_CU / CHISO_MOI / DIEN_TTHU,
                # vì coordinator._save_monthly_data đọc theo format đó.
                return {
                    "data": [
                        {
                            "THANG": month,
                            "NAM": year,
                            "DIEN_TTHU": chi_so_thang,
                            "CHISO_CU": chi_so_cu,
                            "CHISO_MOI": chi_so_moi,
                            "TU_NGAY": from_date_parsed.strftime("%d/%m/%Y"),
                            "DEN_NGAY": to_date_parsed.strftime("%d/%m/%Y"),
                        }
                    ]
                }
            else:
                # Các region khác dùng endpoint chung
                session = await self._get_session()
                url = f"{self.base_url}/api/evn/tracuu/chisothang"

                # Lấy MA_DVIQLY và MA_DDO dựa trên region
                ma_dviqly, ma_ddo = self._get_ma_dviqly_and_ma_ddo()

                # Format: MM/YYYY
                thang_nam = f"{month:02d}/{year}"

                payload = {
                    "MA_DVIQLY": ma_dviqly,
                    "MA_DDO": ma_ddo,
                    "TU_THANG_NAM": thang_nam,
                    "DEN_THANG_NAM": thang_nam,
                }

                headers = {
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/json",
                    "user-agent": "okhttp/4.12.0",
                    "authorization": f"Bearer {self.access_token}",
                }

                async with session.post(url, json=payload, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        if await self.login():
                            headers["authorization"] = f"Bearer {self.access_token}"
                            async with session.post(url, json=payload, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    _ghi_loi_http("get_chisothang", retry_resp.status)
                                    return None
                                return await retry_resp.json()
                        return None

                    if resp.status != 200:
                        _ghi_loi_http("get_chisothang", resp.status)
                        return None

                    return await resp.json()

        except Exception as e:
            _ghi_ngoai_le("get_chisothang", e)
            return None

    async def get_hoadon(self) -> Optional[Dict[str, Any]]:
        """Get bill information.
        
        Returns:
            Dict with data or None
        """
        if not self.access_token:
            if not await self.login():
                return None

        try:
            session = await self._get_session()
            
            # HCMC dùng endpoint và format riêng
            if self.region == "HCMC":
                if not self.hcmc_session:
                    if not await self.login():
                        return None
                
                url = f"{self.base_url}/Tracuu/kiemTraNo"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Cookie": f"evn_session={self.hcmc_session}",
                }
                
                payload = {
                    "input_makh": self.customer_id,
                }
                
                _LOGGER.debug(f"get_hoadon (HCMC): URL={url}, payload={payload}, region={self.region}")
                
                async with session.post(url, data=payload, headers=headers, ssl=False) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_hoadon", resp.status, error_text)
                        return None
                    
                    # HCMC API trả về JSON nhưng Content-Type là text/html
                    try:
                        response_text = await resp.text(encoding='utf-8', errors='replace')
                        data = json.loads(response_text)
                    except Exception as e:
                        _LOGGER.error(f"Failed to parse HCMC response: {e}")
                        return None
                    
                    # Xử lý response theo format HCMC
                    # HCMC trả về {"state": "success", "data": {"isNo": 0/1, "info_no": [...]}}
                    # Nếu có nợ (isNo=1), info_no chứa danh sách hóa đơn
                    if data.get("state") == "success" and "data" in data:
                        hcmc_data = data["data"]
                        if hcmc_data.get("isNo") == 1 and "info_no" in hcmc_data:
                            # Có nợ, trả về danh sách hóa đơn
                            bills = hcmc_data["info_no"]
                            if isinstance(bills, list):
                                return {"data": bills}
                        else:
                            # Không có nợ, trả về empty list
                            return {"data": []}
                    
                    _LOGGER.error(f"HCMC get_hoadon: Invalid response format: {data}")
                    return None
            
            # SPC dùng endpoint và format riêng
            elif self.region == "SPC":
                url = f"{self.base_url}/api/NghiepVu/TraCuuNoHoaDon"
                params = {
                    # Phải là mã khách hàng đã cấu hình, không phải mã của tài
                    # khoản đăng nhập: một tài khoản SPC có thể gắn nhiều mã và
                    # API phân quyền theo tham số này.
                    "strMaKH": self.customer_id,
                }
                headers = {
                    "User-Agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept-Language": "vi-vn",
                    "Connection": "keep-alive",
                }
                
                _LOGGER.debug(f"get_hoadon (SPC): URL={url}, params={params}, region={self.region}")
                
                async with session.get(url, params=params, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        if await self.login():
                            headers["Authorization"] = f"Bearer {self.access_token}"
                            async with session.get(url, params=params, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    error_text = await retry_resp.text()
                                    _ghi_loi_http("get_hoadon", retry_resp.status, error_text)
                                    return None
                                data = await retry_resp.json()
                                # SPC trả về list trực tiếp, wrap vào dict với key "data"
                                if isinstance(data, list):
                                    return {"data": data}
                                return data
                        return None

                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_hoadon", resp.status, error_text)
                        return None

                    data = await resp.json()
                    # SPC trả về list trực tiếp, wrap vào dict với key "data"
                    if isinstance(data, list):
                        return {"data": data}
                    return data
            else:
                # Các region khác dùng endpoint chung
                url = f"{self.base_url}/api/evn/tracuu/hoadon"

                headers = {
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/json",
                    "user-agent": "okhttp/4.12.0",
                    "authorization": f"Bearer {self.access_token}",
                }

                async with session.post(url, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        if await self.login():
                            headers["authorization"] = f"Bearer {self.access_token}"
                            async with session.post(url, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    _ghi_loi_http("get_hoadon", retry_resp.status)
                                    return None
                                return await retry_resp.json()
                        return None

                    if resp.status != 200:
                        _ghi_loi_http("get_hoadon", resp.status)
                        return None

                    return await resp.json()

        except Exception as e:
            _ghi_ngoai_le("get_hoadon", e)
            return None

    async def get_ngungcapdien(
        self, from_date: str, to_date: str
    ) -> Optional[Dict[str, Any]]:
        """Get power outage schedule.
        
        Args:
            from_date: Format dd/mm/yyyy
            to_date: Format dd/mm/yyyy
            
        Returns:
            Dict with data or None
        """
        if not self.access_token:
            if not await self.login():
                return None

        try:
            session = await self._get_session()
            
            # SPC dùng endpoint và format riêng
            if self.region == "SPC":
                url = f"{self.base_url}/api/NghiepVu/TraCuuLichNgungGiamCungCapDien"
                params = {
                    # Phải là mã khách hàng đã cấu hình, không phải mã của tài
                    # khoản đăng nhập: một tài khoản SPC có thể gắn nhiều mã và
                    # API phân quyền theo tham số này.
                    "strMaKH": self.customer_id,
                }
                headers = {
                    "User-Agent": "evnapp/59 CFNetwork/1240.0.4 Darwin/20.6.0",
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept-Language": "vi-vn",
                    "Connection": "keep-alive",
                }
                
                _LOGGER.debug(f"get_ngungcapdien (SPC): URL={url}, params={params}, region={self.region}")
                
                async with session.get(url, params=params, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        if await self.login():
                            headers["Authorization"] = f"Bearer {self.access_token}"
                            async with session.get(url, params=params, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    error_text = await retry_resp.text()
                                    _ghi_loi_http("get_ngungcapdien", retry_resp.status, error_text)
                                    return None
                                data = await retry_resp.json()
                                # SPC trả về list trực tiếp, chuyển đổi format và wrap vào dict với key "data"
                                if isinstance(data, list):
                                    converted_data = self._convert_spc_outage_to_standard_format(data)
                                    return {"data": converted_data}
                                return data
                        return None

                    if resp.status != 200:
                        error_text = await resp.text()
                        _ghi_loi_http("get_ngungcapdien", resp.status, error_text)
                        return None

                    data = await resp.json()
                    # SPC trả về list trực tiếp, chuyển đổi format và wrap vào dict với key "data"
                    if isinstance(data, list):
                        converted_data = self._convert_spc_outage_to_standard_format(data)
                        return {"data": converted_data}
                    return data
            else:
                # Các region khác dùng endpoint chung
                url = f"{self.base_url}/api/evn/tracuu/ngungcapdien"

                payload = {
                    "TU_NGAY": from_date,
                    "DEN_NGAY": to_date,
                }

                headers = {
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/json",
                    "user-agent": "okhttp/4.12.0",
                    "authorization": f"Bearer {self.access_token}",
                }

                async with session.post(url, json=payload, headers=headers, ssl=False) as resp:
                    if resp.status == 401:
                        if await self.login():
                            headers["authorization"] = f"Bearer {self.access_token}"
                            async with session.post(url, json=payload, headers=headers, ssl=False) as retry_resp:
                                if retry_resp.status != 200:
                                    _ghi_loi_http("get_ngungcapdien", retry_resp.status)
                                    return None
                                data = await retry_resp.json()
                                # Chuyển đổi format cho CPC
                                if self.region == "CPC" and isinstance(data, dict) and data.get("data"):
                                    if isinstance(data["data"], list):
                                        converted_data = self._convert_cpc_outage_to_standard_format(data["data"])
                                        return {"data": converted_data}
                                return data
                        return None

                    if resp.status != 200:
                        _ghi_loi_http("get_ngungcapdien", resp.status)
                        return None

                    data = await resp.json()
                    # Chuyển đổi format cho CPC
                    if self.region == "CPC" and isinstance(data, dict) and data.get("data"):
                        if isinstance(data["data"], list):
                            converted_data = self._convert_cpc_outage_to_standard_format(data["data"])
                            return {"data": converted_data}
                    return data

        except Exception as e:
            _ghi_ngoai_le("get_ngungcapdien", e)
            return None


    async def get_thongbao(self) -> Optional[list]:
        """Lấy danh sách thông báo trong app EVN (gồm loại 'Ngừng cấp điện').

        Chỉ chạy cho các region đăng nhập qua cổng chung cskh.evn.com.vn
        (HN/NPC/CPC/HCMC). SPC có cổng riêng, token không dùng được ở feed này.

        Returns:
            Danh sách notification (list các dict), hoặc None nếu lỗi/không hỗ trợ.
        """
        if self.region == "SPC":
            return None

        if not self.access_token:
            if not await self.login():
                return None

        try:
            session = await self._get_session()
            headers = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "user-agent": "okhttp/4.12.0",
                "authorization": f"Bearer {self.access_token}",
            }

            async with session.post(
                NOTIFICATION_URL, json={}, headers=headers, ssl=False
            ) as resp:
                if resp.status == 401:
                    if await self.login():
                        headers["authorization"] = f"Bearer {self.access_token}"
                        async with session.post(
                            NOTIFICATION_URL, json={}, headers=headers, ssl=False
                        ) as retry_resp:
                            if retry_resp.status != 200:
                                _LOGGER.error(
                                    f"get_thongbao failed with status {retry_resp.status}"
                                )
                                return None
                            data = await retry_resp.json()
                            return data.get("data") if isinstance(data, dict) else None
                    return None

                if resp.status != 200:
                    _ghi_loi_http("get_thongbao", resp.status)
                    return None

                data = await resp.json()
                return data.get("data") if isinstance(data, dict) else None

        except Exception as e:
            _ghi_ngoai_le("get_thongbao", e)
            return None

    @staticmethod
    def _trich_detail(data: Any, status: int) -> Optional[Any]:
        """Trích payload detail từ JSON trả về, ghi log dạng dữ liệu nhận được.

        Dạng chuẩn theo disassembly: {"data": {..., "body": ...}}. Chưa xác
        minh bằng gọi thật nên nhận cả dạng không có wrapper "data" (JSON
        chính là dict chứa "body"). Chỉ ghi tên khóa (schema) và kiểu dữ
        liệu, không ghi giá trị, nên không lộ nội dung tin.
        """
        if not isinstance(data, dict):
            _LOGGER.warning(
                "npc detail: HTTP %s, response khong phai dict (type=%s), bo qua",
                status, type(data).__name__,
            )
            return None
        keys = sorted(data.keys())
        if "data" in data:
            payload = data["data"]
            co_body = (isinstance(payload, dict) and "body" in payload) or isinstance(payload, str)
            data_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
            _LOGGER.warning(
                "npc detail: HTTP %s, keys=%s, data type=%s, co body=%s, data keys=%s",
                status, keys, type(payload).__name__, co_body, data_keys,
            )
            return payload
        _LOGGER.warning(
            "npc detail: HTTP %s, keys=%s, khong co wrapper data, dung toan bo JSON",
            status, keys,
        )
        return data

    async def get_notification_detail(self, notif_id) -> Optional[Any]:
        """Lấy nội dung đầy đủ của một thông báo theo id.

        Chỉ chạy cho các region đăng nhập qua cổng chung cskh.evn.com.vn
        (HN/NPC/CPC/HCMC). SPC có cổng riêng, token không dùng được ở đây.

        App EVN chỉ gọi endpoint này khi body trong feed trống; nội dung đầy
        đủ nằm ở data.body (HTML). Feed getAllByUser trả summary là bản xem
        trước bị cắt ngắn nên ngày/giờ cắt điện có thể nằm ngoài summary.

        Dạng trả về chưa xác minh bằng gọi thật: thường là dict `data` (có
        trường `body`), nhưng cũng nhận dict không có wrapper "data" hoặc
        `data` là chuỗi HTML trực tiếp. Mỗi bước đều ghi log WARNING (để thấy
        trong log HA mặc định) kèm lý do ngắn khi trả None.

        Args:
            notif_id: trường `id` của tin trong feed (số hoặc chuỗi).

        Returns:
            Dict `data` của response (có trường `body` là HTML nội dung đầy
            đủ), hoặc chuỗi HTML trực tiếp, hoặc None nếu lỗi/không hỗ trợ.
        """
        if self.region == "SPC":
            _LOGGER.warning("npc detail: region SPC khong ho tro, bo qua")
            return None

        if not self.access_token:
            if not await self.login():
                _LOGGER.warning("npc detail: khong co token, login that bai, bo qua")
                return None

        url = f"{NOTIFICATION_DETAIL_URL}/{notif_id}"

        try:
            session = await self._get_session()
            headers = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "user-agent": "okhttp/4.12.0",
                "authorization": f"Bearer {self.access_token}",
            }

            async with session.get(url, headers=headers, ssl=False) as resp:
                if resp.status == 401:
                    _LOGGER.warning(
                        "npc detail: HTTP 401, dang nhap lai va thu lai 1 lan"
                    )
                    if await self.login():
                        headers["authorization"] = f"Bearer {self.access_token}"
                        async with session.get(
                            url, headers=headers, ssl=False
                        ) as retry_resp:
                            if retry_resp.status != 200:
                                _LOGGER.warning(
                                    "npc detail: retry tra status %s, bo qua",
                                    retry_resp.status,
                                )
                                _ghi_loi_http(
                                    "get_notification_detail", retry_resp.status
                                )
                                return None
                            data = await retry_resp.json()
                            return self._trich_detail(data, retry_resp.status)
                    _LOGGER.warning("npc detail: 401 va login that bai, bo qua")
                    return None

                if resp.status != 200:
                    _LOGGER.warning("npc detail: HTTP %s, bo qua", resp.status)
                    _ghi_loi_http("get_notification_detail", resp.status)
                    return None

                data = await resp.json()
                return self._trich_detail(data, resp.status)

        except Exception as e:
            _LOGGER.warning(
                "npc detail: exception %s, bo qua", type(e).__name__
            )
            _ghi_ngoai_le("get_notification_detail", e)
            return None
