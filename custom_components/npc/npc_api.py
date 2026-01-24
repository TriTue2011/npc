"""API client for EVN VN (NPC, HN, CPC, SPC)"""

import logging
import aiohttp
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Base URLs for different regions
EVN_REGIONS = {
    "HN": "https://gwkong.evnhanoi.vn",
    "NPC": "https://apicskhevn.npc.com.vn",
    "CPC": "https://cskh-api.cpc.vn",
    "SPC": "https://api.cskh.evnspc.vn",
}

# Common login URL
LOGIN_URL = "https://cskh.evn.com.vn/cskh/v1/auth/login"


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
        elif self.ma_dviqly and self.ma_ddo:
            # CPC/SPC: dùng maKhang để extract
            # (đã được lưu trong login)
            ma_dviqly = self.ma_dviqly
            ma_ddo = self.ma_ddo
        else:
            # Fallback: extract từ customer_id
            ma_dviqly = self.customer_id[:6] if self.customer_id else ""
            ma_ddo = self.customer_id if self.customer_id else ""
        
        return ma_dviqly, ma_ddo

    async def login(self) -> bool:
        """Login to EVN and get access token."""
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

                if self.region == "HN":
                    _LOGGER.info(
                        f"Login successful for {self.customer_id} "
                        "(HN: will use customer_id[:6] and customer_id+'001')"
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
                                _LOGGER.error(f"get_chisongay failed with status {retry_resp.status}, response: {error_text[:500]}")
                                return None
                            return await retry_resp.json()
                    return None

                if resp.status != 200:
                    error_text = await resp.text()
                    _LOGGER.error(f"get_chisongay failed with status {resp.status}, URL={url}, payload={payload}, response: {error_text[:500]}")
                    return None

                return await resp.json()

        except Exception as e:
            _LOGGER.error(f"get_chisongay error: {e}", exc_info=True)
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
                                _LOGGER.error(f"get_chisothang failed with status {retry_resp.status}")
                                return None
                            return await retry_resp.json()
                    return None

                if resp.status != 200:
                    _LOGGER.error(f"get_chisothang failed with status {resp.status}")
                    return None

                return await resp.json()

        except Exception as e:
            _LOGGER.error(f"get_chisothang error: {e}", exc_info=True)
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
                                _LOGGER.error(f"get_hoadon failed with status {retry_resp.status}")
                                return None
                            return await retry_resp.json()
                    return None

                if resp.status != 200:
                    _LOGGER.error(f"get_hoadon failed with status {resp.status}")
                    return None

                return await resp.json()

        except Exception as e:
            _LOGGER.error(f"get_hoadon error: {e}", exc_info=True)
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
                                _LOGGER.error(f"get_ngungcapdien failed with status {retry_resp.status}")
                                return None
                            return await retry_resp.json()
                    return None

                if resp.status != 200:
                    _LOGGER.error(f"get_ngungcapdien failed with status {resp.status}")
                    return None

                return await resp.json()

        except Exception as e:
            _LOGGER.error(f"get_ngungcapdien error: {e}", exc_info=True)
            return None
