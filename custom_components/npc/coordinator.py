"""Data update coordinator for EVN VN"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import sqlite3
import os

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .npc_api import EVNAPI
from .const import SCAN_INTERVAL, DB_PATH, DOMAIN

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
            
            # Calculate all batches
            all_daily_data = []
            current_start = start_date
            
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
            bill_data = await self.api.get_hoadon()
            if bill_data and bill_data.get("data"):
                await self._save_bill_data(bill_data["data"])
                # Also save to monthly_bill table
                await self._save_hoadon_to_monthly_bill(bill_data["data"])

            # Fetch power outage schedule (from start date to today)
            from_date = start_date.strftime("%d/%m/%Y")
            to_date = today.strftime("%d/%m/%Y")
            outage_data = await self.api.get_ngungcapdien(from_date, to_date)
            if outage_data and outage_data.get("data"):
                await self._save_outage_data(outage_data["data"])

            # Return summary data
            return {
                "last_update": datetime.now().isoformat(),
                "customer_id": self.customer_id,
            }

        except Exception as err:
            raise UpdateFailed(f"Error updating EVN data: {err}") from err

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
                
                # Calculate daily consumption from previous day's reading
                dien_tieu_thu = None
                if prev_chi_so is not None and chi_so is not None and chi_so >= prev_chi_so:
                    dien_tieu_thu = chi_so - prev_chi_so
                else:
                    # Try to get from API response
                    dien_tieu_thu = self._parse_float(
                        record.get("dien_tieu_thu") or 
                        record.get("DIEN_TIEU_THU") or
                        record.get("SAN_LUONG") or
                        record.get("san_luong") or
                        record.get("DIEN_TIEU_THU_KWH")
                    )

                cursor.execute("""
                    INSERT OR REPLACE INTO daily_consumption 
                    (userevn, ngay, chi_so, dien_tieu_thu_kwh)
                    VALUES (?, ?, ?, ?)
                """, (self.customer_id, ngay, chi_so, dien_tieu_thu))
                
                prev_chi_so = chi_so
                prev_ngay = ngay

            conn.commit()
            conn.close()
            _LOGGER.debug(f"Saved {len(data)} daily records for {self.customer_id}")

        except Exception as e:
            _LOGGER.error(f"Error saving daily data: {e}", exc_info=True)

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
        """Save bill data (tiền nợ) to database."""
        if not data or not isinstance(data, list):
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

            for bill in data:
                if bill.get("TTRANG_TTOAN") == "CHUATT":
                    tien_no = self._parse_float(bill.get("TONG_TIEN", 0))
                    ngay_cap_nhat = datetime.now().strftime("%d-%m-%Y")
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO tien_no_evn 
                        (userevn, tien_no, ngay_cap_nhat)
                        VALUES (?, ?, ?)
                    """, (self.customer_id, tien_no, ngay_cap_nhat))
                    break

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
