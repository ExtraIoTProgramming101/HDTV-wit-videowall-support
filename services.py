"""
HDTV Matrix Services
Servicios personalizados para la integración de matriz HDMI.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .api import HDTVMatrixApi, CommandError, ConnectionError
from .const import DOMAIN, CONF_INPUTS, CONF_OUTPUTS

_LOGGER = logging.getLogger(__name__)

# Esquemas de validación para servicios
SERVICE_SET_ROUTE = "set_route"
SERVICE_SET_ALL_TO_INPUT = "set_all_to_input"

SCHEMA_SET_ROUTE = vol.Schema({
    vol.Required("output"): cv.positive_int,
    vol.Required("input"): cv.positive_int,
})

SCHEMA_SET_ALL_TO_INPUT = vol.Schema({
    vol.Required("input"): cv.positive_int,
})

SERVICE_SET_VIDEOWALL = "set_videowall"

SCHEMA_SET_VIDEOWALL = vol.Schema({
    vol.Required("input"): cv.positive_int,
    vol.Required("grid_width"): vol.All(cv.positive_int, vol.Range(min=2, max=8)),
    vol.Required("grid_height"): vol.All(cv.positive_int, vol.Range(min=2, max=8)),
    vol.Optional("start_output", default=1): cv.positive_int,
})

SERVICE_SET_SPLICE = "set_splice"

SCHEMA_SET_SPLICE = vol.Schema({
    vol.Required("splice_data"): cv.string,  # JSON string con configuración splice
})


async def async_setup_services(hass: HomeAssistant) -> None:
    """
    Registra los servicios de la integración.
    
    Args:
        hass: Instancia de Home Assistant
    """
    _LOGGER.debug("Registrando servicios de %s", DOMAIN)

    async def handle_set_route(call: ServiceCall) -> None:
        """
        Maneja el servicio set_route.
        
        Args:
            call: Llamada al servicio
        """
        output = call.data["output"]
        input_port = call.data["input"]
        
        _LOGGER.info(
            "Servicio set_route llamado: Entrada %d → Salida %d",
            input_port,
            output
        )
        
        # Obtener la primera entrada disponible (o todas si se requiere)
        entry_data = _get_first_entry_data(hass)
        
        if not entry_data:
            raise HomeAssistantError(
                "No HDTV Matrix integration found. "
                "Please add the integration first."
            )
        
        api: HDTVMatrixApi = entry_data["api"]
        
        # Validar que los números estén en rango
        entry = entry_data.get("entry")
        if entry:
            max_inputs = entry.options.get(
                CONF_INPUTS,
                entry.data.get(CONF_INPUTS, 36)
            )
            max_outputs = entry.options.get(
                CONF_OUTPUTS,
                entry.data.get(CONF_OUTPUTS, 36)
            )
            
            if input_port < 1 or input_port > max_inputs:
                raise HomeAssistantError(
                    f"Input {input_port} is out of range (1-{max_inputs})"
                )
            
            if output < 1 or output > max_outputs:
                raise HomeAssistantError(
                    f"Output {output} is out of range (1-{max_outputs})"
                )
        
        try:
            await api.set_route(input_port, output)
            
            # Actualizar coordinador
            coordinator = entry_data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
            
            _LOGGER.info("Ruta establecida exitosamente")
            
        except CommandError as err:
            _LOGGER.error("Error al establecer ruta: %s", err)
            raise HomeAssistantError(f"Failed to set route: {err}") from err
            
        except ConnectionError as err:
            _LOGGER.error("Error de conexión: %s", err)
            raise HomeAssistantError(f"Connection error: {err}") from err

    async def handle_set_videowall(call: ServiceCall) -> None:
        """
        Maneja el servicio set_videowall.
        
        Args:
            call: Llamada al servicio
        """
        input_port = call.data["input"]
        grid_width = call.data["grid_width"]
        grid_height = call.data["grid_height"]
        start_output = call.data.get("start_output", 1)
        
        screens_needed = grid_width * grid_height
        
        _LOGGER.info(
            "Servicio set_videowall llamado: Entrada %d en grid %dx%d (%d pantallas) desde salida %d",
            input_port,
            grid_width,
            grid_height,
            screens_needed,
            start_output
        )
        
        entry_data = _get_first_entry_data(hass)
        
        if not entry_data:
            raise HomeAssistantError(
                "No HDTV Matrix integration found. "
                "Please add the integration first."
            )
        
        api: HDTVMatrixApi = entry_data["api"]
        entry = entry_data.get("entry")
        
        # Validar configuración
        if entry:
            num_inputs = entry.options.get(
                CONF_INPUTS,
                entry.data.get(CONF_INPUTS, 36)
            )
            num_outputs = entry.options.get(
                CONF_OUTPUTS,
                entry.data.get(CONF_OUTPUTS, 36)
            )
            
            if input_port < 1 or input_port > num_inputs:
                raise HomeAssistantError(
                    f"Input {input_port} is out of range (1-{num_inputs})"
                )
            
            if start_output + screens_needed - 1 > num_outputs:
                raise HomeAssistantError(
                    f"Videowall requires {screens_needed} outputs starting from {start_output}, "
                    f"but only {num_outputs} outputs available"
                )
        else:
            num_inputs = 36
            num_outputs = 36
        
        try:
            # Generar configuración de videowall
            data = _generate_videowall_splice_data(
                num_inputs=num_inputs,
                num_outputs=num_outputs,
                input_port=input_port,
                grid_width=grid_width,
                grid_height=grid_height,
                start_output=start_output
            )
            
            await api.set_splice(num_inputs, num_outputs, data)
            
            # Actualizar coordinador
            coordinator = entry_data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
            
            _LOGGER.info(
                "Videowall %dx%d configurado exitosamente (Entrada %d)",
                grid_width,
                grid_height,
                input_port
            )
            
        except CommandError as err:
            _LOGGER.error("Error al configurar videowall: %s", err)
            raise HomeAssistantError(f"Failed to configure videowall: {err}") from err
            
        except ConnectionError as err:
            _LOGGER.error("Error de conexión: %s", err)
            raise HomeAssistantError(f"Connection error: {err}") from err

    async def handle_set_splice(call: ServiceCall) -> None:
        """
        Maneja el servicio set_splice para configuraciones avanzadas.
        
        Args:
            call: Llamada al servicio
        """
        splice_data_str = call.data["splice_data"]
        
        _LOGGER.info("Servicio set_splice llamado con configuración personalizada")
        
        entry_data = _get_first_entry_data(hass)
        
        if not entry_data:
            raise HomeAssistantError(
                "No HDTV Matrix integration found. "
                "Please add the integration first."
            )
        
        api: HDTVMatrixApi = entry_data["api"]
        entry = entry_data.get("entry")
        
        if entry:
            num_inputs = entry.options.get(
                CONF_INPUTS,
                entry.data.get(CONF_INPUTS, 36)
            )
            num_outputs = entry.options.get(
                CONF_OUTPUTS,
                entry.data.get(CONF_OUTPUTS, 36)
            )
        else:
            num_inputs = 36
            num_outputs = 36
        
        try:
            # Parsear JSON string
            import json
            splice_config = json.loads(splice_data_str)
            
            # Validar estructura
            if not isinstance(splice_config, list):
                raise HomeAssistantError("splice_data must be a JSON array")
            
            data = [str(x) for x in splice_config]
            
            # Validar longitud
            expected_length = num_inputs * num_outputs
            if len(data) != expected_length:
                raise HomeAssistantError(
                    f"splice_data must contain {expected_length} elements "
                    f"({num_inputs} inputs × {num_outputs} outputs), got {len(data)}"
                )
            
            await api.set_splice(num_inputs, num_outputs, data)
            
            # Actualizar coordinador
            coordinator = entry_data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
            
            _LOGGER.info("Splice personalizado configurado exitosamente")
            
        except json.JSONDecodeError as err:
            _LOGGER.error("Error al parsear splice_data JSON: %s", err)
            raise HomeAssistantError(f"Invalid JSON in splice_data: {err}") from err
            
        except CommandError as err:
            _LOGGER.error("Error al ejecutar splice: %s", err)
            raise HomeAssistantError(f"Failed to execute splice: {err}") from err
            
        except ConnectionError as err:
            _LOGGER.error("Error de conexión: %s", err)
            raise HomeAssistantError(f"Connection error: {err}") from err

    async def handle_set_all_to_input(call: ServiceCall) -> None:
        """
        Maneja el servicio set_all_to_input.
        
        Args:
            call: Llamada al servicio
        """
        input_port = call.data["input"]
        
        _LOGGER.info(
            "Servicio set_all_to_input llamado: Entrada %d a todas las salidas",
            input_port
        )
        
        entry_data = _get_first_entry_data(hass)
        
        if not entry_data:
            raise HomeAssistantError(
                "No HDTV Matrix integration found. "
                "Please add the integration first."
            )
        
        api: HDTVMatrixApi = entry_data["api"]
        entry = entry_data.get("entry")
        
        # Obtener número de salidas
        if entry:
            num_outputs = entry.options.get(
                CONF_OUTPUTS,
                entry.data.get(CONF_OUTPUTS, 36)
            )
            max_inputs = entry.options.get(
                CONF_INPUTS,
                entry.data.get(CONF_INPUTS, 36)
            )
            
            if input_port < 1 or input_port > max_inputs:
                raise HomeAssistantError(
                    f"Input {input_port} is out of range (1-{max_inputs})"
                )
        else:
            num_outputs = 36
        
        try:
            await api.set_all_to_input(input_port, num_outputs)
            
            # Actualizar coordinador
            coordinator = entry_data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
            
            _LOGGER.info(
                "Todas las salidas enrutadas a entrada %d exitosamente",
                input_port
            )
            
        except CommandError as err:
            _LOGGER.error("Error al enrutar todas las salidas: %s", err)
            raise HomeAssistantError(
                f"Failed to set all outputs: {err}"
            ) from err
            
        except ConnectionError as err:
            _LOGGER.error("Error de conexión: %s", err)
            raise HomeAssistantError(f"Connection error: {err}") from err

    # Registrar servicios
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ROUTE,
        handle_set_route,
        schema=SCHEMA_SET_ROUTE,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALL_TO_INPUT,
        handle_set_all_to_input,
        schema=SCHEMA_SET_ALL_TO_INPUT,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VIDEOWALL,
        handle_set_videowall,
        schema=SCHEMA_SET_VIDEOWALL,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SPLICE,
        handle_set_splice,
        schema=SCHEMA_SET_SPLICE,
    )
    
    _LOGGER.info("Servicios registrados: set_route, set_all_to_input, set_videowall, set_splice")


async def async_unload_services(hass: HomeAssistant) -> None:
    """
    Elimina los servicios cuando se descarga la integración.
    
    Args:
        hass: Instancia de Home Assistant
    """
    _LOGGER.debug("Eliminando servicios de %s", DOMAIN)
    
    hass.services.async_remove(DOMAIN, SERVICE_SET_ROUTE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_ALL_TO_INPUT)
    hass.services.async_remove(DOMAIN, SERVICE_SET_VIDEOWALL)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SPLICE)
    
    _LOGGER.info("Servicios eliminados")


def _get_first_entry_data(hass: HomeAssistant) -> Dict[str, Any] | None:
    """
    Obtiene los datos de la primera entrada de configuración.
    
    Args:
        hass: Instancia de Home Assistant
        
    Returns:
        Diccionario con datos de la entrada o None si no existe
    """
    domain_data = hass.data.get(DOMAIN, {})
    
    if not domain_data:
        return None
    
    # Retornar la primera entrada disponible
    for entry_id, entry_data in domain_data.items():
        return entry_data
    
    return None


def _generate_videowall_splice_data(
    num_inputs: int,
    num_outputs: int,
    input_port: int,
    grid_width: int,
    grid_height: int,
    start_output: int = 1
) -> list[str]:
    """
    Genera el array de datos para configuración de videowall.
    
    Args:
        num_inputs: Número total de entradas
        num_outputs: Número total de salidas
        input_port: Puerto de entrada a usar (1-indexed)
        grid_width: Ancho del grid de videowall
        grid_height: Alto del grid de videowall
        start_output: Primera salida a usar (1-indexed, default: 1)
        
    Returns:
        Lista de strings con la configuración splice
    """
    total_elements = num_inputs * num_outputs
    data = ["-1"] * total_elements
    
    # Convertir a índices 0-based
    input_idx = input_port - 1
    start_output_idx = start_output - 1
    
    # Configurar las salidas para el videowall
    screens_needed = grid_width * grid_height
    
    for screen in range(screens_needed):
        output_idx = start_output_idx + screen
        
        if output_idx >= num_outputs:
            break
        
        # Calcular posición en el array splice
        # El array es: [input0_output0, input0_output1, ..., input1_output0, ...]
        position = (input_idx * num_outputs) + output_idx
        
        if position < total_elements:
            data[position] = str(input_idx)
    
    return data
