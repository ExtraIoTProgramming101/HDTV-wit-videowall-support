"""
HDTV Matrix Sensors
Sensores que muestran el estado de las salidas de la matriz.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_OUTPUTS
from .coordinator import HDTVCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """
    Configura los sensores de la integración.
    
    Args:
        hass: Instancia de Home Assistant
        entry: Entrada de configuración
        async_add_entities: Callback para agregar entidades
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HDTVCoordinator = entry_data["coordinator"]

    # Obtener número de salidas desde configuración
    num_outputs = entry.options.get(
        CONF_OUTPUTS,
        entry.data.get(CONF_OUTPUTS, 36)
    )
    
    # Validar número de salidas
    num_outputs = _validate_output_count(num_outputs, entry.entry_id)
    
    # Esperar datos iniciales si no están disponibles
    if coordinator.data is None:
        _LOGGER.debug("Esperando datos iniciales del coordinador...")
        await coordinator.async_request_refresh()
    
    # Crear sensores
    sensors = []
    for output_num in range(1, num_outputs + 1):
        sensors.append(
            HDTVMatrixOutputSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                output_number=output_num,
                device_info=_create_device_info(entry)
            )
        )
    
    async_add_entities(sensors, update_before_add=True)
    
    _LOGGER.info(
        "Creados %d sensores para matriz HDTV (entry: %s)",
        len(sensors),
        entry.entry_id
    )


class HDTVMatrixOutputSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor que representa el estado de una salida de la matriz.
    
    Muestra qué entrada está conectada a cada salida.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:video-input-hdmi"

    def __init__(
        self,
        coordinator: HDTVCoordinator,
        entry_id: str,
        output_number: int,
        device_info: DeviceInfo
    ) -> None:
        """
        Inicializa el sensor.
        
        Args:
            coordinator: Coordinador de actualizaciones
            entry_id: ID de la entrada de configuración
            output_number: Número de salida (1-indexed)
            device_info: Información del dispositivo
        """
        super().__init__(coordinator)
        
        self._output_number = output_number
        self._attr_unique_id = f"{entry_id}_output_sensor_{output_number}"
        self._attr_name = f"Output {output_number}"
        self._attr_device_info = device_info
        
        _LOGGER.debug(
            "Sensor inicializado: %s (output: %d)",
            self._attr_unique_id,
            output_number
        )

    @property
    def native_value(self) -> Optional[str]:
        """
        Retorna el valor actual del sensor.
        
        Returns:
            Descripción de la entrada conectada o "Sin entrada"
        """
        if not self.coordinator.data:
            return None
        
        output_state = self.coordinator.get_output_state(self._output_number)
        
        if not output_state:
            return None
        
        input_num = output_state.get("input")
        
        if input_num is None:
            return "Sin entrada"
        
        return f"Entrada {input_num}"

    @property
    def available(self) -> bool:
        """
        Indica si el sensor está disponible.
        
        Returns:
            True si hay conexión con el dispositivo
        """
        return self.coordinator.is_connected

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """
        Atributos adicionales del sensor.
        
        Returns:
            Diccionario con información detallada de la salida
        """
        if not self.coordinator.data:
            return {}
        
        output_state = self.coordinator.get_output_state(self._output_number)
        
        if not output_state:
            return {}
        
        input_num = output_state.get("input")
        raw_value = output_state.get("raw_value")
        
        attributes = {
            "output_number": self._output_number,
            "raw_value": raw_value,
            "connected": input_num is not None,
        }
        
        if input_num is not None:
            attributes["input_number"] = input_num
        
        return attributes

    @property
    def entity_registry_enabled_default(self) -> bool:
        """
        Define si el sensor está habilitado por defecto.
        
        Returns:
            True para habilitar por defecto
        """
        return True

    async def async_added_to_hass(self) -> None:
        """Callback cuando el sensor se agrega a Home Assistant."""
        await super().async_added_to_hass()
        _LOGGER.debug(
            "Sensor %s agregado (Output %d)",
            self.entity_id,
            self._output_number
        )

    async def async_will_remove_from_hass(self) -> None:
        """Callback cuando el sensor se va a remover de Home Assistant."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug(
            "Sensor %s removido (Output %d)",
            self.entity_id,
            self._output_number
        )


def _validate_output_count(num_outputs: Any, entry_id: str) -> int:
    """
    Valida el número de salidas.
    
    Args:
        num_outputs: Valor a validar
        entry_id: ID de la entrada (para logging)
        
    Returns:
        Número validado entre 1 y 64
    """
    try:
        count = int(num_outputs)
        
        if count < 1:
            _LOGGER.warning(
                "Número de salidas inválido: %d, usando 4 (entry: %s)",
                count,
                entry_id
            )
            return 4
        
        if count > 64:
            _LOGGER.warning(
                "Número de salidas muy alto: %d, usando 64 (entry: %s)",
                count,
                entry_id
            )
            return 64
        
        return count
        
    except (ValueError, TypeError):
        _LOGGER.warning(
            "Número de salidas inválido: '%s', usando 4 (entry: %s)",
            num_outputs,
            entry_id
        )
        return 4


def _create_device_info(entry: ConfigEntry) -> DeviceInfo:
    """
    Crea información del dispositivo para agrupar entidades.
    
    Args:
        entry: Entrada de configuración
        
    Returns:
        Información del dispositivo
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="HDTV Matrix",
        manufacturer="HDTV Supply",
        model="HDMI Matrix Switch",
        configuration_url=entry.data.get("base_url"),
    )
