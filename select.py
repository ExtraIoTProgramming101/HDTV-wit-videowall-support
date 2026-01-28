"""
HDTV Matrix Select Entities
Controles de selección para cambiar entradas en salidas de la matriz.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
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
    """
    Configura los selectores de entrada de la integración.
    
    Args:
        hass: Instancia de Home Assistant
        entry: Entrada de configuración
        async_add_entities: Callback para agregar entidades
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HDTVCoordinator = entry_data["coordinator"]
    api: HDTVMatrixApi = entry_data["api"]

    # Obtener configuración de entradas y salidas
    num_inputs = entry.options.get(
        CONF_INPUTS,
        entry.data.get(CONF_INPUTS, 36)
    )
    num_outputs = entry.options.get(
        CONF_OUTPUTS,
        entry.data.get(CONF_OUTPUTS, 36)
    )
    
    # Validar números
    num_inputs = _validate_port_count(num_inputs, "entradas", entry.entry_id)
    num_outputs = _validate_port_count(num_outputs, "salidas", entry.entry_id)
    
    # Esperar datos iniciales si no están disponibles
    if coordinator.data is None:
        _LOGGER.debug("Esperando datos iniciales del coordinador...")
        await coordinator.async_request_refresh()
    
    # Crear selectores
    selectors = []
    for output_num in range(1, num_outputs + 1):
        selectors.append(
            HDTVMatrixInputSelector(
                coordinator=coordinator,
                api=api,
                entry_id=entry.entry_id,
                output_number=output_num,
                num_inputs=num_inputs,
                device_info=_create_device_info(entry)
            )
        )
    
    async_add_entities(selectors, update_before_add=True)
    
    _LOGGER.info(
        "Creados %d selectores para matriz HDTV (entry: %s)",
        len(selectors),
        entry.entry_id
    )


class HDTVMatrixInputSelector(CoordinatorEntity, SelectEntity):
    """
    Selector para cambiar la entrada conectada a una salida.
    
    Permite seleccionar qué fuente HDMI se enruta a una salida específica.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:video-input-hdmi"

    def __init__(
        self,
        coordinator: HDTVCoordinator,
        api: HDTVMatrixApi,
        entry_id: str,
        output_number: int,
        num_inputs: int,
        device_info: DeviceInfo
    ) -> None:
        """
        Inicializa el selector.
        
        Args:
            coordinator: Coordinador de actualizaciones
            api: Cliente API de la matriz
            entry_id: ID de la entrada de configuración
            output_number: Número de salida (1-indexed)
            num_inputs: Número total de entradas disponibles
            device_info: Información del dispositivo
        """
        super().__init__(coordinator)
        
        self._api = api
        self._output_number = output_number
        self._num_inputs = num_inputs
        
        self._attr_unique_id = f"{entry_id}_input_select_output_{output_number}"
        self._attr_name = f"Output {output_number} Input"
        self._attr_device_info = device_info
        
        # Generar lista de opciones: Entrada 1, Entrada 2, ..., Sin entrada
        self._attr_options = self._generate_options()
        
        _LOGGER.debug(
            "Selector inicializado: %s (output: %d, inputs: %d)",
            self._attr_unique_id,
            output_number,
            num_inputs
        )

    def _generate_options(self) -> List[str]:
        """
        Genera la lista de opciones disponibles.
        
        Returns:
            Lista con todas las entradas posibles
        """
        options = [f"Entrada {i}" for i in range(1, self._num_inputs + 1)]
        options.append("Sin entrada")
        return options

    @property
    def current_option(self) -> Optional[str]:
        """
        Retorna la opción actualmente seleccionada.
        
        Returns:
            Entrada actual conectada a esta salida
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

    async def async_select_option(self, option: str) -> None:
        """
        Cambia la entrada conectada a la salida.
        
        Args:
            option: Opción seleccionada (ej: "Entrada 3")
            
        Raises:
            CommandError: Si falla el cambio de ruta
        """
        try:
            # Validar opción
            if option == "Sin entrada":
                _LOGGER.warning(
                    "No se puede desconectar la entrada desde Home Assistant "
                    "(Output %d)",
                    self._output_number
                )
                return
            
            # Extraer número de entrada
            input_num = self._parse_input_number(option)
            
            if input_num is None:
                _LOGGER.error("Opción inválida: %s", option)
                return
            
            _LOGGER.info(
                "Cambiando ruta: Entrada %d → Salida %d",
                input_num,
                self._output_number
            )
            
            # Ejecutar comando en la API
            await self._api.set_route(input_num, self._output_number)
            
            # Solicitar actualización inmediata
            await self.coordinator.async_request_refresh()
            
            _LOGGER.info(
                "Ruta cambiada exitosamente: Entrada %d → Salida %d",
                input_num,
                self._output_number
            )
            
        except CommandError as err:
            _LOGGER.error(
                "Error al cambiar ruta (Output %d): %s",
                self._output_number,
                err
            )
            raise
            
        except Exception as err:
            _LOGGER.exception(
                "Error inesperado al cambiar ruta (Output %d): %s",
                self._output_number,
                err
            )
            raise

    def _parse_input_number(self, option: str) -> Optional[int]:
        """
        Extrae el número de entrada de la opción.
        
        Args:
            option: Texto de la opción (ej: "Entrada 3")
            
        Returns:
            Número de entrada o None si es inválido
        """
        try:
            # Formato esperado: "Entrada N"
            parts = option.split()
            if len(parts) == 2 and parts[0] == "Entrada":
                return int(parts[1])
        except (ValueError, IndexError):
            pass
        
        return None

    @property
    def available(self) -> bool:
        """
        Indica si el selector está disponible.
        
        Returns:
            True si hay conexión con el dispositivo
        """
        return self.coordinator.is_connected

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """
        Atributos adicionales del selector.
        
        Returns:
            Diccionario con información detallada
        """
        if not self.coordinator.data:
            return {}
        
        output_state = self.coordinator.get_output_state(self._output_number)
        
        if not output_state:
            return {}
        
        attributes = {
            "output_number": self._output_number,
            "total_inputs": self._num_inputs,
            "raw_value": output_state.get("raw_value"),
        }
        
        input_num = output_state.get("input")
        if input_num is not None:
            attributes["input_number"] = input_num
        
        return attributes

    @property
    def entity_registry_enabled_default(self) -> bool:
        """
        Define si el selector está habilitado por defecto.
        
        Returns:
            True para habilitar por defecto
        """
        return True

    async def async_added_to_hass(self) -> None:
        """Callback cuando el selector se agrega a Home Assistant."""
        await super().async_added_to_hass()
        _LOGGER.debug(
            "Selector %s agregado (Output %d)",
            self.entity_id,
            self._output_number
        )

    async def async_will_remove_from_hass(self) -> None:
        """Callback cuando el selector se va a remover de Home Assistant."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug(
            "Selector %s removido (Output %d)",
            self.entity_id,
            self._output_number
        )


def _validate_port_count(count: Any, port_type: str, entry_id: str) -> int:
    """
    Valida el número de puertos.
    
    Args:
        count: Valor a validar
        port_type: Tipo de puerto ("entradas" o "salidas")
        entry_id: ID de la entrada (para logging)
        
    Returns:
        Número validado entre 1 y 64
    """
    try:
        num = int(count)
        
        if num < 1:
            _LOGGER.warning(
                "Número de %s inválido: %d, usando 4 (entry: %s)",
                port_type,
                num,
                entry_id
            )
            return 4
        
        if num > 64:
            _LOGGER.warning(
                "Número de %s muy alto: %d, usando 64 (entry: %s)",
                port_type,
                num,
                entry_id
            )
            return 64
        
        return num
        
    except (ValueError, TypeError):
        _LOGGER.warning(
            "Número de %s inválido: '%s', usando 4 (entry: %s)",
            port_type,
            count,
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
