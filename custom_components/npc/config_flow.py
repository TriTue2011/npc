"""Config flow for EVN VN integration"""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from typing import Any
import logging

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CUSTOMER_ID,
    CONF_REGION,
    CONF_NGAYDAUKY,
    REGION_HN,
    REGION_NPC,
    REGION_CPC,
    REGION_SPC,
    REGION_HCMC,
)
from .npc_api import EVNAPI

_LOGGER = logging.getLogger(__name__)

REGION_OPTIONS = [
    {"value": REGION_HN, "label": "Hà Nội (HN)"},
    {"value": REGION_NPC, "label": "Miền Bắc (NPC)"},
    {"value": REGION_CPC, "label": "Miền Trung (CPC)"},
    {"value": REGION_SPC, "label": "Miền Nam (SPC)"},
    {"value": REGION_HCMC, "label": "Hồ Chí Minh (HCMC)"},
]


class EVNConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for EVN VN."""

    VERSION = 2

    def __init__(self):
        """Initialize config flow."""
        self._user_input = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle initial step - select region."""
        errors = {}

        if user_input is not None:
            self._user_input[CONF_REGION] = user_input[CONF_REGION]
            return await self.async_step_credentials()

        schema = vol.Schema({
            vol.Required(CONF_REGION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=REGION_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": """
### 🔌 Cấu hình EVN VN

Chọn khu vực điện lực của bạn:
- **HN**: Hà Nội
- **NPC**: Miền Bắc
- **CPC**: Miền Trung  
- **SPC**: Miền Nam
- **HCMC**: Hồ Chí Minh
                """
            }
        )

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None):
        """Handle credentials step."""
        errors = {}

        if user_input is not None:
            self._user_input.update({
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            })
            return await self.async_step_customer_id()

        schema = vol.Schema({
            vol.Required(CONF_USERNAME): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                    autocomplete="username"
                )
            ),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD
                )
            ),
        })

        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": f"""
### 🔐 Thông tin đăng nhập

Nhập username và password để đăng nhập vào hệ thống EVN.

**Khu vực đã chọn**: {self._user_input.get(CONF_REGION, 'N/A')}
                """
            }
        )

    async def async_step_customer_id(self, user_input: dict[str, Any] | None = None):
        """Handle customer ID and billing cycle step."""
        errors = {}

        if user_input is not None:
            customer_id = user_input[CONF_CUSTOMER_ID].strip().upper()
            ngaydauky = int(user_input[CONF_NGAYDAUKY])

            # Validate customer ID format
            if not (customer_id.startswith('P') or customer_id.startswith('S')) or len(customer_id) < 11:
                errors[CONF_CUSTOMER_ID] = "invalid_format"
            else:
                # Test login and verify customer ID
                try:
                    api = EVNAPI(
                        self.hass,
                        self._user_input[CONF_REGION],
                        self._user_input[CONF_USERNAME],
                        self._user_input[CONF_PASSWORD],
                        customer_id
                    )

                    if await api.login():
                        # Verify we can get data
                        from datetime import datetime, timedelta
                        today = datetime.now()
                        from_date = (today - timedelta(days=7)).strftime("%d/%m/%Y")
                        to_date = today.strftime("%d/%m/%Y")
                        
                        test_data = await api.get_chisongay(from_date, to_date)
                        if test_data and test_data.get("data"):
                            # Success - create entry
                            await api.close()
                            
                            await self.async_set_unique_id(customer_id)
                            self._abort_if_unique_id_configured()

                            return self.async_create_entry(
                                title=customer_id,
                                data={
                                    CONF_REGION: self._user_input[CONF_REGION],
                                    CONF_USERNAME: self._user_input[CONF_USERNAME],
                                    CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                                    CONF_CUSTOMER_ID: customer_id,
                                    CONF_NGAYDAUKY: ngaydauky,
                                }
                            )
                        else:
                            errors["base"] = "no_data"
                            await api.close()
                    else:
                        errors["base"] = "invalid_auth"
                        await api.close()
                except Exception as e:
                    _LOGGER.error(f"Error during verification: {e}", exc_info=True)
                    errors["base"] = "unknown"
                    try:
                        await api.close()
                    except:
                        pass

        schema = vol.Schema({
            vol.Required(CONF_CUSTOMER_ID): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                    autocomplete="customer_id"
                )
            ),
            vol.Required(CONF_NGAYDAUKY, default=1): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=31,
                    mode=selector.NumberSelectorMode.SLIDER,
                    step=1
                )
            ),
        })

        return self.async_show_form(
            step_id="customer_id",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": f"""
### 📋 Thông tin tài khoản

Nhập mã khách hàng và ngày đầu kỳ thanh toán.

**Khu vực**: {self._user_input.get(CONF_REGION, 'N/A')}
**Username**: {self._user_input.get(CONF_USERNAME, 'N/A')}

**Ngày đầu kỳ**: Ngày bắt đầu chu kỳ thanh toán hàng tháng (1-31)
                """
            }
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get options flow handler."""
        return EVNOptionsFlowHandler(config_entry)


class EVNOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Initialize options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_ngaydauky = self.config_entry.options.get(
            CONF_NGAYDAUKY,
            self.config_entry.data.get(CONF_NGAYDAUKY, 1)
        )

        schema = vol.Schema({
            vol.Required(
                CONF_NGAYDAUKY,
                default=current_ngaydauky
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=31,
                    mode=selector.NumberSelectorMode.BOX
                )
            ),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema
        )
