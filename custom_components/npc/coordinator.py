"""Data update coordinator for EVN VN"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import sqlite3
import os

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .npc_api import EVNAPI
from .const import SCAN_INTERVAL, REFRESH_WINDOW_DAYS, DB_PATH, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EVNDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for EVN data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EVNAPI,
        customer_id: str,
        ngaydauky: int = 1,
    ):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{customer_id}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.api = api
        self.customer_id = customer_id
        self.ngaydauky = ngaydauky
        self.data: Dict[str, Any] = {}

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API and save to database."""
        try:
            # Login if needed
            if not self.api.access_token:
                if not await self.api.login():
                    raise UpdateFailed("Failed to login")

            # Fetch daily data in batches of 15 days
            # Start from 01/01/2025 to today
            today = datetime.now()
            start_date = datetime(2025, 1, 1)
            batch_days = 15

            # Chỉ tải lại cửa sổ gần đây: chỉ số các ngày đã qua không đổi nữa.
            # Database còn rỗng thì mới tải toàn bộ lịch sử.
            last_saved = self._get_last_saved_date()
            if last_saved:
                fetch_start = max(
                    last_saved - timedelta(days=REFRESH_WINDOW_DAYS), start_date
                )
            else:
                fetch_start = start_date
                _LOGGER.info(f"Chưa có dữ liệu, tải toàn bộ lịch sử từ {start_date:%d/%m/%Y}")

            # Calculate all batches
            all_daily_data = []
            current_start = fetch_start

            while current_start < today:
                current_end = min(current_start + timedelta(days=batch_days - 1), today)
                from_date_str = current_start.strftime("%d/%m/%Y")
                to_date_str = current_end.strftime("%d/%m/%Y")
                
                _LOGGER.debug(f"Fetching daily data from {from_date_str} to {to_date_str}")
                daily_data = await self.api.get_chisongay(from_date_str, to_date_str)
                
                if daily_data and daily_data.get("data"):
                    all_daily_data.extend(daily_data["data"])
                    _LOGGER.debug(f"Got {len(daily_data['data'])} records for {from_date_str} to {to_date_str}")
                
                # Move to next batch
                current_start = current_end + timedelta(days=1)
            
            # Save all daily data
            if all_daily_data:
                await self._save_daily_data(all_daily_data)
                await self._save_monthly_consumption_from_daily()
                _LOGGER.info(f"Saved total {len(all_daily_data)} daily records")

            # Fetch monthly data for current and previous months
            current_month = today.month
            current_year = today.year
            
            monthly_data = await self.api.get_chisothang(current_month, current_year)
            if monthly_data and monthly_data.get("data"):
                await self._save_monthly_data(monthly_data["data"], current_month, current_year)

            # Fetch previous month
            if current_month == 1:
                prev_month = 12
                prev_year = current_year - 1
            else:
                prev_month = current_month - 1
                prev_year = current_year

            prev_monthly_data = await self.api.get_chisothang(prev_month, prev_year)
            if prev_monthly_data and prev_monthly_data.get("data"):
                await self._save_monthly_data(prev_monthly_data["data"], prev_month, prev_year)

            # Fetch bill data (hóa đơn)
            # Danh sách rỗng nghĩa là không nợ tiền, vẫn phải ghi để sensor
            # tiền nợ có bảng mà đọc.
            bill_data = await self.api.get_hoadon()
            if bill_data is not None and isinstance(bill_data.get("data"), list):
                await self._save_bill_data(bill_data["data"])
                # Also save to monthly_bill table
                await self._save_hoadon_to_monthly_bill(bill_data["data"])

            # Fetch power outage schedule.
            # Lịch ngừng cấp điện là thông báo cho ngày SẮP TỚI, nên cửa sổ
            # phải kéo tới tương lai; dừng ở hôm nay thì không bao giờ thấy
            # đợt cắt điện được báo trước. Lùi 30 ngày để giữ lịch sử gần đây.
            from_date = (today - timedelta(days=30)).strftime("%d/%m/%Y")
            to_date = (today + timedelta(days=30)).strftime("%d/%m/%Y")
            outage_data = await self.api.get_ngungcapdien(from_date, to_date)
            if outage_data and outage_data.get("data"):
                await self._save_outage_data(outage_data["data"])

            # Lịch ngừng cấp điện BÁO TRƯỚC không nằm trong API tra cứu ở trên
            # (nó trả về rỗng); EVN chỉ đẩy vào feed "Thông báo" của app. Lấy
            # thêm từ đó rồi gộp vào cùng bảng để sensor lich_cat_dien đọc được.
            notifications = await self.api.get_thongbao()
            if notifications:
                await self._save_notification_outages(notifications)
                await self._save_notifications(notifications)

            # Return summary data
            return {
                "last_update": datetime.now().isoformat(),
                "customer_id": self.customer_id,
            }

        except Exception as err:
            raise UpdateFailed(f"Error updating EVN data: {err}") from err

    def _get_last_saved_date(self) -> Optional[datetime]:
        """Ngày mới nhất đã lưu trong database, None nếu chưa có dữ liệu."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            # ngay lưu dạng dd-mm-yyyy nên phải sắp xếp theo năm/tháng/ngày
            cursor.execute("""
                SELECT ngay FROM daily_consumption WHERE userevn = ?
                ORDER BY substr(ngay, 7, 4) DESC,
                         substr(ngay, 4, 2) DESC,
                         substr(ngay, 1, 2) DESC
                LIMIT 1
            """, (self.customer_id,))
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                return datetime.strptime(row[0], "%d-%m-%Y")
        except Exception as e:
            _LOGGER.debug(f"Chưa đọc được ngày cuối trong database: {e}")
        return None

    async def _save_daily_data(self, data: list):
        """Save daily consumption data to database."""
        if not data:
            return

        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_consumption (
                    userevn TEXT,
                    ngay TEXT,
                    chi_so REAL,
                    dien_tieu_thu_kwh REAL,
                    PRIMARY KEY (userevn, ngay)
                )
            """)

            # API returns data from newest to oldest (index 0 is newest)
            # But we need to process from oldest to newest to calculate daily consumption
            # So reverse the list first, then sort by date to be safe
            sorted_data = sorted(data, key=lambda x: self._parse_date_for_sort(record=x))
            
            prev_chi_so = None
            prev_ngay = None
            
            for record in sorted_data:
                # Parse date from record (format may vary)
                ngay = self._parse_date(record)
                # Try multiple field names for chi_so
                chi_so = self._parse_float(
                    record.get("CHISO_MOI") or 
                    record.get("chi_so_moi") or
                    record.get("CHISO") or 
                    record.get("chi_so") or
                    record.get("CHI_SO") or
                    record.get("chiSo")
                )
                
                # Calculate daily consumption
                # Priority: Use DIEN_TIEU_THU from API if available (HCMC, SPC provide this)
                # Otherwise, calculate from meter readings
                dien_tieu_thu = self._parse_float(
                    record.get("dien_tieu_thu") or 
                    record.get("DIEN_TIEU_THU") or
                    record.get("SAN_LUONG") or
                    record.get("san_luong") or
                    record.get("DIEN_TIEU_THU_KWH")
                )
                
                # If not provided by API, calculate from meter readings
                # Only calculate if prev_ngay is the previous day (not many days ago)
                if dien_tieu_thu is None and prev_chi_so is not None and chi_so is not None:
                    # Check if prev_ngay is the previous day
                    can_calculate = False
                    if prev_ngay:
                        try:
                            from datetime import datetime
                            prev_date = datetime.strptime(prev_ngay, "%d-%m-%Y").date()
                            current_date = datetime.strptime(ngay, "%d-%m-%Y").date()
                            # Only calculate if prev_date is exactly 1 day before current_date
                            if (current_date - prev_date).days == 1:
                                can_calculate = True
                            else:
                                _LOGGER.debug(
                                    f"Không tính tiêu thụ từ chỉ số cho {ngay}: "
                                    f"ngày trước ({prev_ngay}) không phải ngày liền trước "
                                    f"(cách {(current_date - prev_date).days} ngày)"
                                )
                        except Exception as e:
                            _LOGGER.warning(f"Lỗi parse ngày để kiểm tra: {e}")
                            # Fallback: allow calculation if dates are close (within 2 days)
                            can_calculate = True
                    else:
                        # No previous day, cannot calculate
                        can_calculate = False
                    
                    if can_calculate and chi_so >= prev_chi_so:
                        dien_tieu_thu = chi_so - prev_chi_so
                    elif chi_so < prev_chi_so:
                        # Chỉ số giảm (có thể reset hoặc lỗi), không tính
                        _LOGGER.warning(
                            f"Chỉ số giảm tại {ngay}: {chi_so} < {prev_chi_so}, "
                            f"bỏ qua tính tiêu thụ từ chỉ số"
                        )
                        dien_tieu_thu = None

                # COALESCE: không ghi đè giá trị đã có bằng NULL. Cần thiết vì
                # ngày đầu mỗi cửa sổ làm mới không có ngày liền trước để tính
                # sản lượng (NPC/HN/CPC chỉ trả chỉ số), và vì bản ghi gộp
                # nhiều ngày của SPC không có chỉ số chốt.
                cursor.execute("""
                    INSERT INTO daily_consumption
                    (userevn, ngay, chi_so, dien_tieu_thu_kwh)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(userevn, ngay) DO UPDATE SET
                        chi_so = COALESCE(excluded.chi_so, daily_consumption.chi_so),
                        dien_tieu_thu_kwh = COALESCE(
                            excluded.dien_tieu_thu_kwh,
                            daily_consumption.dien_tieu_thu_kwh
                        )
                """, (self.customer_id, ngay, chi_so, dien_tieu_thu))
                
                prev_chi_so = chi_so
                prev_ngay = ngay

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Saved {len(data)} daily records for {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving daily data: {e}", exc_info=True)

    async def _save_monthly_consumption_from_daily(self):
        """Tổng hợp sản lượng từng tháng từ bảng ngày đã lưu.

        API hóa đơn chỉ trả về hóa đơn CHƯA thanh toán, nên bảng monthly_bill
        gần như rỗng với khách hàng không nợ. Gộp lại từ dữ liệu ngày (không
        tốn thêm request) để sensor "Hóa đơn năm nay" có đủ các tháng.
        Chỉ ghi sản lượng, giữ nguyên tiền điện của hóa đơn thật.
        """
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_bill (
                    userevn TEXT,
                    thang INTEGER,
                    nam INTEGER,
                    tien_dien REAL,
                    san_luong_kwh REAL,
                    PRIMARY KEY (userevn, thang, nam)
                )
            """)

            # ngay lưu dạng dd-mm-yyyy
            cursor.execute("""
                SELECT CAST(substr(ngay, 4, 2) AS INTEGER) AS thang,
                       CAST(substr(ngay, 7, 4) AS INTEGER) AS nam,
                       ROUND(SUM(dien_tieu_thu_kwh), 2)
                FROM daily_consumption
                WHERE userevn = ? AND dien_tieu_thu_kwh IS NOT NULL
                GROUP BY nam, thang
            """, (self.customer_id,))

            rows = cursor.fetchall()
            for thang, nam, san_luong in rows:
                cursor.execute("""
                    INSERT INTO monthly_bill
                    (userevn, thang, nam, tien_dien, san_luong_kwh)
                    VALUES (?, ?, ?, NULL, ?)
                    ON CONFLICT(userevn, thang, nam)
                    DO UPDATE SET san_luong_kwh = excluded.san_luong_kwh
                """, (self.customer_id, thang, nam, san_luong))

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Tổng hợp sản lượng {len(rows)} tháng cho {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error aggregating monthly consumption: {e}", exc_info=True)

    async def _save_monthly_data(self, data: list, month: int, year: int):
        """Save monthly bill data to database."""
        if not data:
            return

        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_bill (
                    userevn TEXT,
                    thang INTEGER,
                    nam INTEGER,
                    tien_dien REAL,
                    san_luong_kwh REAL,
                    PRIMARY KEY (userevn, thang, nam)
                )
            """)

            # Extract monthly totals from data
            # API response structure: data is a list with one record
            # Record contains: CHISO_MOI, CHISO_CU, DIEN_TTHU
            tien_dien = None
            san_luong = None
            
            if isinstance(data, list) and len(data) > 0:
                # Get from first record
                record = data[0]
                
                # Điện tiêu thụ từ DIEN_TTHU
                san_luong = self._parse_float(
                    record.get("DIEN_TTHU") or
                    record.get("dien_tthu") or
                    record.get("SAN_LUONG") or
                    record.get("san_luong")
                )
                
                # Nếu không có, tính từ CHISO_MOI - CHISO_CU
                if san_luong is None:
                    chi_so_moi = self._parse_float(
                        record.get("CHISO_MOI") or 
                        record.get("chi_so_moi")
                    )
                    chi_so_cu = self._parse_float(
                        record.get("CHISO_CU") or 
                        record.get("chi_so_cu")
                    )
                    if chi_so_moi is not None and chi_so_cu is not None:
                        san_luong = chi_so_moi - chi_so_cu
                
                # Tiền điện không có trong chisothang, sẽ lấy từ hoadon
                # Chỉ lưu san_luong ở đây

            if san_luong is not None:
                cursor.execute("""
                    INSERT OR REPLACE INTO monthly_bill 
                    (userevn, thang, nam, tien_dien, san_luong_kwh)
                    VALUES (?, ?, ?, ?, ?)
                """, (self.customer_id, month, year, tien_dien, san_luong))

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Saved monthly data for {self.customer_id}, {month}/{year}")

        except Exception as e:
            _LOGGER.error(f"Error saving monthly data: {e}", exc_info=True)

    async def _save_bill_data(self, data: list):
        """Save bill data (tiền nợ) to database.

        Danh sách rỗng = không còn hóa đơn chưa thanh toán, ghi tiền nợ 0.
        """
        if not isinstance(data, list):
            return

        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tien_no_evn (
                    userevn TEXT,
                    tien_no REAL,
                    ngay_cap_nhat TEXT,
                    PRIMARY KEY (userevn)
                )
            """)

            tien_no = 0
            for bill in data:
                if bill.get("TTRANG_TTOAN") == "CHUATT":
                    tien_no = self._parse_float(bill.get("TONG_TIEN", 0))
                    break

            ngay_cap_nhat = datetime.now().strftime("%d-%m-%Y")
            cursor.execute("""
                INSERT OR REPLACE INTO tien_no_evn
                (userevn, tien_no, ngay_cap_nhat)
                VALUES (?, ?, ?)
            """, (self.customer_id, tien_no, ngay_cap_nhat))

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Saved bill data (tiền nợ) for {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving bill data: {e}", exc_info=True)

    async def _save_hoadon_to_monthly_bill(self, data: list):
        """Save hóa đơn data to monthly_bill table."""
        if not data or not isinstance(data, list):
            return

        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_bill (
                    userevn TEXT,
                    thang INTEGER,
                    nam INTEGER,
                    tien_dien REAL,
                    san_luong_kwh REAL,
                    PRIMARY KEY (userevn, thang, nam)
                )
            """)

            # Save each bill to monthly_bill
            for bill in data:
                thang = bill.get("THANG")
                nam = bill.get("NAM")
                tien_dien = self._parse_float(bill.get("TONG_TIEN"))
                san_luong = self._parse_float(bill.get("DIEN_TTHU"))  # DIEN_TTHU = điện tiêu thụ
                
                if thang is not None and nam is not None:
                    cursor.execute("""
                        INSERT OR REPLACE INTO monthly_bill 
                        (userevn, thang, nam, tien_dien, san_luong_kwh)
                        VALUES (?, ?, ?, ?, ?)
                    """, (self.customer_id, thang, nam, tien_dien, san_luong))
                    _LOGGER.debug(f"Saved hóa đơn: thang={thang}, nam={nam}, tien={tien_dien}, sl={san_luong}")

            conn.commit()
            conn.close()
            _LOGGER.info(f"Saved {len(data)} hóa đơn records to monthly_bill for {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving hóa đơn to monthly_bill: {e}", exc_info=True)

    async def _save_outage_data(self, data: list):
        """Save power outage schedule to database."""
        if not data:
            return

        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS power_outage_schedule (
                    userevn TEXT,
                    ngay_bat_dau TEXT,
                    ngay_ket_thuc TEXT,
                    thoi_gian_bat_dau TEXT,
                    thoi_gian_ket_thuc TEXT,
                    ly_do TEXT,
                    khu_vuc TEXT,
                    PRIMARY KEY (userevn, ngay_bat_dau, thoi_gian_bat_dau)
                )
            """)

            for outage in data:
                # Try multiple field names for NPC API
                ngay_bat_dau = (
                    outage.get("NGAY_BAT_DAU") or 
                    outage.get("ngay_bat_dau") or
                    outage.get("NGAY") or
                    outage.get("ngay")
                )
                ngay_ket_thuc = (
                    outage.get("NGAY_KET_THUC") or 
                    outage.get("ngay_ket_thuc") or
                    outage.get("NGAY") or
                    outage.get("ngay")
                )
                thoi_gian_bat_dau = (
                    outage.get("THOI_GIAN_BAT_DAU") or 
                    outage.get("thoi_gian_bat_dau") or
                    outage.get("THOI_GIAN") or
                    outage.get("thoi_gian") or
                    outage.get("THOI_DIEM") or
                    outage.get("thoi_diem") or
                    ""
                )
                thoi_gian_ket_thuc = (
                    outage.get("THOI_GIAN_KET_THUC") or 
                    outage.get("thoi_gian_ket_thuc") or
                    ""
                )
                ly_do = (
                    outage.get("LY_DO") or 
                    outage.get("ly_do") or
                    outage.get("NOI_DUNG") or
                    outage.get("noi_dung") or
                    ""
                )
                khu_vuc = (
                    outage.get("KHU_VUC") or 
                    outage.get("khu_vuc") or
                    outage.get("DIA_CHI") or
                    outage.get("dia_chi") or
                    ""
                )
                
                # Parse dates to dd-mm-yyyy format if needed
                if ngay_bat_dau:
                    ngay_bat_dau = self._parse_date({"NGAY": ngay_bat_dau})
                if ngay_ket_thuc:
                    ngay_ket_thuc = self._parse_date({"NGAY": ngay_ket_thuc})

                cursor.execute("""
                    INSERT OR REPLACE INTO power_outage_schedule 
                    (userevn, ngay_bat_dau, ngay_ket_thuc, thoi_gian_bat_dau, 
                     thoi_gian_ket_thuc, ly_do, khu_vuc)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (self.customer_id, ngay_bat_dau, ngay_ket_thuc, 
                      thoi_gian_bat_dau, thoi_gian_ket_thuc, ly_do, khu_vuc))

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Saved {len(data)} outage records for {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving outage data: {e}", exc_info=True)

    def _parse_outage_notification(self, summary: str) -> Optional[Dict]:
        """Tách thông tin lịch cắt điện từ nội dung một thông báo.

        Hai mẫu đã gặp:
        - Mẫu cũ: "... thuộc ĐZ 35kV lộ 373E8.22 thời điểm từ 05h00 đến 11h30
          ngày 28/8/2026 để ĐL Kim Thành sửa chữa ...".
        - Mẫu mới (từ 11/2026): "... sẽ tạm ngừng cấp điện tại địa chỉ của
          Quý khách từ 08:00 10-11-2026. Thời gian dự kiến cấp điện trở lại:
          17:00 10-11-2026 ...".
        Trả về dict theo standard format của _save_outage_data, hoặc None nếu
        thiếu ngày/giờ (không dựng được lịch).
        """
        if not summary:
            return None

        # Ngày: mẫu cũ "ngày 28/8/2026", mẫu mới "10-11-2026".
        date_m = re.search(r"ngày\s+(\d{1,2})/(\d{1,2})/(\d{4})", summary)
        if date_m:
            d, m, y = date_m.group(1), date_m.group(2), date_m.group(3)
        else:
            date_m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", summary)
            if not date_m:
                return None
            d, m, y = date_m.group(1), date_m.group(2), date_m.group(3)

        # Giờ: mẫu cũ "từ 05h00 đến 11h30", mẫu mới "từ 08:00" +
        # "cấp điện trở lại: 17:00".
        time_m = re.search(
            r"từ\s+(\d{1,2})h(\d{2})\s+đến\s+(\d{1,2})h(\d{2})", summary
        )
        if time_m:
            sh, sm, eh, em = (
                time_m.group(1),
                time_m.group(2),
                time_m.group(3),
                time_m.group(4),
            )
        else:
            start_m = re.search(r"từ\s+(\d{1,2}):(\d{2})", summary)
            end_m = re.search(r"cấp điện trở lại:\s*(\d{1,2}):(\d{2})", summary)
            if not (start_m and end_m):
                return None
            sh, sm, eh, em = (
                start_m.group(1),
                start_m.group(2),
                end_m.group(1),
                end_m.group(2),
            )

        area_m = re.search(r"thuộc\s+(.+?)\s+thời điểm", summary)
        reason_m = re.search(r"để\s+(.+?)(?:\s*\.\.\.|\s*$)", summary)

        ngay = f"{int(d):02d}/{int(m):02d}/{y}"
        return {
            "NGAY_BAT_DAU": ngay,
            "NGAY_KET_THUC": ngay,
            "THOI_GIAN_BAT_DAU": f"{int(sh):02d}:{sm}",
            "THOI_GIAN_KET_THUC": f"{int(eh):02d}:{em}",
            "KHU_VUC": area_m.group(1).strip() if area_m else "",
            "LY_DO": reason_m.group(1).strip() if reason_m else summary.strip(),
        }

    async def _save_notification_outages(self, notifications: list):
        """Lọc thông báo loại 'Ngừng cấp điện' đúng mã khách hàng đang cấu hình
        rồi lưu vào cùng bảng power_outage_schedule."""
        records = []
        for note in notifications:
            if not isinstance(note, dict):
                continue
            if note.get("notificationType") != "NGUNGCAP_DIEN":
                continue
            summary = note.get("summary") or ""
            # Feed trả về mọi mã của cùng số điện thoại; chỉ giữ đúng mã này.
            if self.customer_id and self.customer_id not in summary:
                continue
            record = self._parse_outage_notification(summary)
            if record:
                records.append(record)

        if records:
            await self._save_outage_data(records)
            _LOGGER.info(
                f"Đã lưu {len(records)} lịch cắt điện từ thông báo cho {self.customer_id}"
            )

    @staticmethod
    def _notification_category(notif_type: str) -> str:
        """Gom notificationType của feed về đúng 3 mục app: Hóa đơn / Truyền
        thông / Ngừng điện. Feed dùng 'HOADON_TBAO_TTOAN' cho hóa đơn nên gom
        theo tiền tố."""
        t = (notif_type or "").upper()
        if t.startswith("NGUNGCAP_DIEN"):
            return "NGUNGCAP_DIEN"
        if t.startswith("HOADON"):
            return "HOADON"
        if t.startswith("TRUYEN_THONG"):
            return "TRUYEN_THONG"
        return t or "KHAC"

    async def _save_notifications(self, notifications: list):
        """Lưu thông báo app (đã lọc theo mã khách hàng) vào bảng notifications
        để các sensor thông báo đọc tin mới nhất từng mục."""
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    userevn TEXT,
                    notif_id TEXT,
                    loai TEXT,
                    tieu_de TEXT,
                    noi_dung TEXT,
                    thoi_gian TEXT,
                    da_doc INTEGER,
                    PRIMARY KEY (userevn, notif_id)
                )
            """)

            count = 0
            for note in notifications:
                if not isinstance(note, dict):
                    continue
                summary = note.get("summary") or ""
                # Feed trả về mọi mã của cùng số điện thoại: bỏ tin gắn mã KHÁC,
                # giữ tin không gắn mã nào (thông báo chung, ví dụ truyền thông).
                if re.search(r"P[A-Z]\d{6,}", summary) and \
                        self.customer_id and self.customer_id not in summary:
                    continue
                loai = self._notification_category(note.get("notificationType"))
                cursor.execute("""
                    INSERT OR REPLACE INTO notifications
                    (userevn, notif_id, loai, tieu_de, noi_dung, thoi_gian, da_doc)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.customer_id, note.get("id"), loai, note.get("title"),
                    summary, note.get("createdDate"),
                    1 if note.get("readStatus") else 0,
                ))
                count += 1

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Đã lưu {count} thông báo cho {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving notifications: {e}", exc_info=True)

    def _parse_date(self, record: Dict) -> str:
        """Parse date from record to dd-mm-yyyy format."""
        # Try different date fields (priority order)
        # NPC API returns "NGAY" field with format "dd/mm/yyyy"
        date_fields = [
            "NGAY", "ngay",  # Most common for NPC API
            "NGAY_DO", "ngay_do", "NGAY_DO_CS", "ngay_do_cs",
            "THOI_DIEM", "thoi_diem",  # NPC also has THOI_DIEM field
            "THOI_GIAN", "thoi_gian",
            "NGAY_BAT_DAU", "ngay_bat_dau",
            "NGAY_KET_THUC", "ngay_ket_thuc"
        ]
        
        for field in date_fields:
            if field in record:
                date_str = str(record[field]).strip()
                if not date_str or date_str.lower() in ['null', 'none', '']:
                    continue
                
                # Handle THOI_DIEM format: "24/01/2026 00:33" -> extract date part
                if field in ["THOI_DIEM", "thoi_diem"] and ' ' in date_str:
                    date_str = date_str.split(' ')[0]

                # SPC gộp nhiều ngày vào một bản ghi khi công tơ không chốt
                # được từng ngày: "08/10/2025-09/10/2025" -> lấy ngày cuối.
                # Không xử lý thì _parse_date trả về hôm nay và ghi đè dữ
                # liệu thật của hôm nay.
                if '/' in date_str and '-' in date_str:
                    date_str = date_str.split('-')[-1].strip()


                # Try to parse and format
                try:
                    # Try dd/mm/yyyy (most common for NPC API)
                    if len(date_str) == 10 and date_str[2] == '/':
                        dt = datetime.strptime(date_str, "%d/%m/%Y")
                        return dt.strftime("%d-%m-%Y")
                    # Try yyyy-mm-dd
                    elif len(date_str) == 10 and date_str[4] == '-':
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        return dt.strftime("%d-%m-%Y")
                    # Already dd-mm-yyyy
                    elif len(date_str) == 10 and date_str[2] == '-':
                        return date_str
                    # Try yyyymmdd
                    elif len(date_str) == 8 and date_str.isdigit():
                        dt = datetime.strptime(date_str, "%Y%m%d")
                        return dt.strftime("%d-%m-%Y")
                    # Try ddmmYYYY (without separators)
                    elif len(date_str) == 8 and date_str[:2].isdigit() and date_str[2:4].isdigit():
                        try:
                            dt = datetime.strptime(date_str, "%d%m%Y")
                            return dt.strftime("%d-%m-%Y")
                        except:
                            pass
                except Exception as e:
                    _LOGGER.debug(f"Error parsing date {date_str} from field {field}: {e}")
                    continue
        
        # Default to today
        _LOGGER.warning(f"Could not parse date from record: {record}, using today")
        return datetime.now().strftime("%d-%m-%Y")

    def _parse_date_for_sort(self, record: Dict) -> datetime:
        """Parse date for sorting purposes."""
        date_str = self._parse_date(record)
        try:
            return datetime.strptime(date_str, "%d-%m-%Y")
        except:
            return datetime.now()

    def _parse_float(self, value: Any) -> Optional[float]:
        """Parse float value from various formats."""
        if value is None:
            return None
        
        if isinstance(value, (int, float)):
            return float(value)
        
        if isinstance(value, str):
            # Remove spaces and replace comma with dot
            value = value.strip().replace(',', '.').replace(' ', '')
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        
        return None
