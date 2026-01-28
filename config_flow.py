"""
HDTV Matrix Config Flow
Flujo de configuración para la integración de matriz HDMI.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_BASE_URL, CONF_SCAN_INTERVAL, CONF_INPUTS, CONF_OUTPUTS
from .api import HDTVMatrixApi, ConnectionError, InvalidResponseError

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Flujo de configuración para HDTV Matrix.
    
    Maneja la configuración inicial del usuario y la detección
    de capacidades del dispositivo.
    """
    
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """
        Maneja el paso inicial de configuración del usuario.
        
        Args:
            user_input: Datos ingresados por el usuario
            
        Returns:
            Resultado del flujo (formulario o entrada creada)
        """
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Validar y conectar con el dispositivo
            session = async_get_clientsession(self.hass)
            base_url = user_input[CONF_BASE_URL]
            
            try:
                _LOGGER.debug("Intentando conectar a %s", base_url)
                
                api = HDTVMatrixApi(session, base_url, timeout=10)
                
                # Probar conexión
                connection_ok = await api.test_connection()
                
                if not connection_ok:
                    _LOGGER.error("El dispositivo no responde")
                    errors["base"] = "cannot_connect"
                else:
                    # Intentar obtener estado para detectar salidas
                    try:
                        data = await api.get_status()
                        detected_outputs = data.get("total_outputs")
                        
                        if detected_outputs:
                            _LOGGER.info(
                                "Detectadas automáticamente %d salidas",
                                detected_outputs
                            )
                            # Actualizar con valor detectado si no fue especificado
                            if user_input.get(CONF_OUTPUTS) == 4:  # Valor por defecto
                                user_input[CONF_OUTPUTS] = detected_outputs
                    except Exception as e:
                        _LOGGER.warning(
                            "No se pudo detectar automáticamente las salidas: %s",
                            e
                        )
                    
                    _LOGGER.info("Conexión exitosa con dispositivo HDTV Matrix")
                    
                    # Crear entrada de configuración
                    return self.async_create_entry(
                        title="HDTV Matrix",
                        data=user_input
                    )
                    
            except ConnectionError as err:
                _LOGGER.error("Error de conexión: %s", err)
                errors["base"] = "cannot_connect"
                
            except InvalidResponseError as err:
                _LOGGER.error("Respuesta inválida del dispositivo: %s", err)
                errors["base"] = "invalid_response"
                
            except Exception as err:
                _LOGGER.exception("Error inesperado durante configuración: %s", err)
                errors["base"] = "unknown"

        # Mostrar formulario de configuración
        data_schema = vol.Schema({
            vol.Required(
                CONF_BASE_URL,
                description={"suggested_value": "http://192.168.1.100"}
            ): str,
            vol.Required(CONF_INPUTS, default=4): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=64)
            ),
            vol.Required(CONF_OUTPUTS, default=4): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=64)
            ),
            vol.Required(CONF_SCAN_INTERVAL, default=1): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=60)
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "example_url": "http://192.168.1.100 o http://192.168.1.100:8080",
                "inputs_info": "Número de entradas HDMI (1-64)",
                "outputs_info": "Número de salidas HDMI (1-64)",
                "scan_info": "Intervalo de actualización en segundos (1-60)"
            }
        )

    async def async_step_import(self, import_data: Dict[str, Any]) -> FlowResult:
        """
        Maneja importación desde configuration.yaml.
        
        Args:
            import_data: Datos importados desde YAML
            
        Returns:
            Resultado del flujo
        """
        _LOGGER.debug("Importando configuración desde YAML")
        return await self.async_step_user(import_data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry
    ) -> OptionsFlow:
        """
        Retorna el flujo de opciones para esta entrada.
        
        Args:
            config_entry: Entrada de configuración
            
        Returns:
            Flujo de opciones
        """
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """
    Flujo de opciones para modificar configuración existente.
    
    Permite al usuario cambiar parámetros sin reconfigurar
    completamente la integración.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """
        Inicializa el flujo de opciones.
        
        Args:
            config_entry: Entrada de configuración a modificar
        """
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """
        Maneja la modificación de opciones.
        
        Args:
            user_input: Datos ingresados por el usuario
            
        Returns:
            Resultado del flujo
        """
        if user_input is not None:
            _LOGGER.info(
                "Actualizando opciones de configuración (entry: %s)",
                self.config_entry.entry_id
            )
            return self.async_create_entry(title="", data=user_input)

        # Obtener valores actuales
        current_inputs = self.config_entry.options.get(
            CONF_INPUTS,
            self.config_entry.data.get(CONF_INPUTS, 36)
        )
        current_outputs = self.config_entry.options.get(
            CONF_OUTPUTS,
            self.config_entry.data.get(CONF_OUTPUTS, 36)
        )
        current_scan = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, 1)
        )

        # Esquema del formulario de opciones
        options_schema = vol.Schema({
            vol.Required(CONF_INPUTS, default=current_inputs): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=64)
            ),
            vol.Required(CONF_OUTPUTS, default=current_outputs): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=64)
            ),
            vol.Required(CONF_SCAN_INTERVAL, default=current_scan): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=60)
            ),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            description_placeholders={
                "scan_info": "Intervalo de actualización en segundos (1-60)",
                "inputs_info": "Número de entradas HDMI (1-64)",
                "outputs_info": "Número de salidas HDMI (1-64)"
            }
        )
