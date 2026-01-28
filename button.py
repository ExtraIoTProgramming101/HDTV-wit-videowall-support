"""
HDTV Matrix Button Entities
Botones para operaciones especiales como videowall y splice.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HDTVMatrixApi, CommandError
from .const import DOMAIN, CONF_INPUTS, CONF_OUTPUTS
from .coordinator import HDTVCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Configura los botones de la integración."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HDTVCoordinator = entry_data["coordinator"]
    api: HDTVMatrixApi = entry_data["api"]

    num_inputs = entry.options.get(
        CONF_INPUTS,
        entry.data.get(CONF_INPUTS, 4)
    )
    num_outputs = entry.options.get(
        CONF_OUTPUTS,
        entry.data.get(CONF_OUTPUTS, 4)
    )

    buttons = []

    # Botón reset matriz
    buttons.append(
        HDTVMatrixResetButton(
            coordinator=coordinator,
            api=api,
            entry_id=entry.entry_id,
            num_inputs=num_inputs,
            num_outputs=num_outputs,
            device_info=_create_device_info(entry)
        )
    )

    # Preset Videowall 2x2
    if num_outputs >= 4:
        buttons.append(
            HDTVMatrixVideowallButton(
                coordinator=coordinator,
                api=api,
                entry_id=entry.entry_id,
                device_info=_create_device_info(entry),
                config_name="2x2",
                grid_x=2,
                grid_y=2,
                description="Videowall 2×2 (4 pantallas)"
            )
        )

    async_add_entities(buttons, update_before_add=False)

    _LOGGER.info(
        "Creados %d botones de configuración (entry: %s)",
        len(buttons),
        entry.entry_id
    )


class HDTVMatrixResetButton(CoordinatorEntity, ButtonEntity):
    """Botón para resetear toda la matriz."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: HDTVCoordinator,
        api: HDTVMatrixApi,
        entry_id: str,
        num_inputs: int,
        num_outputs: int,
        device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)

        self._api = api
        self._num_outputs = num_outputs

        self._attr_unique_id = f"{entry_id}_reset_matrix"
        self._attr_name = "Reset Matrix"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        try:
            _LOGGER.info("Reseteando matriz HDMI (limpiando videowall)")

            # 🔥 Limpieza global de videowall
            await self._api.clear_videowall_mode()

            # Refrescar estado en Home Assistant
            await self.coordinator.async_request_refresh()

            _LOGGER.info("Matriz reseteada correctamente")

        except CommandError as err:
            _LOGGER.error("Error al resetear matriz: %s", err)
            raise

        except Exception as err:
            _LOGGER.exception("Error inesperado al resetear matriz: %s", err)
            raise


    @property
    def available(self) -> bool:
        return self.coordinator.is_connected


class HDTVMatrixVideowallButton(CoordinatorEntity, ButtonEntity):
    """Botón preset para videowall fijo."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:wall"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: HDTVCoordinator,
        api: HDTVMatrixApi,
        entry_id: str,
        device_info: DeviceInfo,
        config_name: str,
        grid_x: int,
        grid_y: int,
        description: str
    ) -> None:
        super().__init__(coordinator)

        self._api = api
        self._config_name = config_name
        self._grid_x = grid_x
        self._grid_y = grid_y
        self._description = description

        self._attr_unique_id = f"{entry_id}_videowall_{config_name}"
        self._attr_name = f"Videowall {config_name}"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        _LOGGER.info(
            "Ejecutando preset Videowall %s (%dx%d)",
            self._config_name,
            self._grid_x,
            self._grid_y
        )

        try:
            # ---------------------------------
            # 1️⃣ Enrutar Input 1 → Outputs 1..4
            # ---------------------------------
            input_port = 1
            outputs = [1, 2, 3, 4]

            for output_port in outputs:
                _LOGGER.info(
                    "Ruteando entrada %d a salida %d",
                    input_port,
                    output_port
                )
                await self._api.set_route(
                    input_port=input_port,
                    output_port=output_port
                )

            _LOGGER.info(
                "Ruteo completado: Entrada %d → Salidas %s",
                input_port,
                outputs
            )

            # ---------------------------------
            # 2️⃣ Ejecutar preset splice + videowall
            # ---------------------------------
            await self._api.set_splice_and_video()

            # ---------------------------------
            # 3️⃣ Refrescar estado
            # ---------------------------------
            await self.coordinator.async_request_refresh()

            _LOGGER.info(
                "Preset Videowall %s ejecutado correctamente",
                self._config_name
            )

        except CommandError as err:
            _LOGGER.error(
                "Error ejecutando preset Videowall %s: %s",
                self._config_name,
                err
            )
            raise

        except Exception:
            _LOGGER.exception(
                "Error inesperado ejecutando preset Videowall %s",
                self._config_name
            )
            raise


    @property
    def available(self) -> bool:
        return self.coordinator.is_connected

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Atributos informativos del preset."""
        return {
            "preset": self._config_name,
            "mode": "videowall",
            "grid": f"{self._grid_x}x{self._grid_y}",
            "screens": self._grid_x * self._grid_y,
            "description": self._description,
            "preset_type": "fixed"
        }


def _create_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Crea información del dispositivo."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="HDTV Matrix",
        manufacturer="HDTV Supply",
        model="HDMI Matrix Switch",
        configuration_url=entry.data.get("base_url"),
    )
