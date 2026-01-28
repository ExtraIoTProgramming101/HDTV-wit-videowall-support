"""
HDTV Matrix Coordinator
Gestiona actualizaciones periódicas del estado de la matriz HDMI.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HDTVMatrixApi, ConnectionError, InvalidResponseError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class HDTVCoordinator(DataUpdateCoordinator):
    """
    Coordinator que gestiona actualizaciones periódicas de la API HDTV.
    
    Este coordinador mantiene el estado sincronizado con la matriz física
    y notifica a todas las entidades cuando hay cambios.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: HDTVMatrixApi,
        update_interval: timedelta = timedelta(seconds=1)
    ) -> None:
        """
        Inicializa el coordinador.
        
        Args:
            hass: Instancia de Home Assistant
            api: Cliente API de la matriz HDTV
            update_interval: Intervalo entre actualizaciones
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=update_interval,
        )
        self.api = api
        self._consecutive_failures = 0
        self._max_failures_before_warning = 3
        
        _LOGGER.debug(
            "Coordinator initialized with update interval: %s",
            update_interval
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """
        Obtiene datos actualizados de la matriz.
        
        Este método es llamado automáticamente por Home Assistant según
        el intervalo configurado.
        
        Returns:
            Diccionario con el estado actual de la matriz
            
        Raises:
            UpdateFailed: Si falla la actualización de datos
        """
        try:
            # Obtener estado actual de la matriz
            data = await self.api.get_status()
            
            # Reset contador de fallos en caso de éxito
            if self._consecutive_failures > 0:
                _LOGGER.info(
                    "Connection restored after %d failed attempts",
                    self._consecutive_failures
                )
                self._consecutive_failures = 0
            
            _LOGGER.debug("Data updated successfully: %d outputs", data.get("total_outputs", 0))
            return data
            
        except ConnectionError as err:
            self._consecutive_failures += 1
            
            # Solo logear warning después de varios fallos consecutivos
            if self._consecutive_failures >= self._max_failures_before_warning:
                _LOGGER.warning(
                    "Connection error (%d consecutive failures): %s",
                    self._consecutive_failures,
                    err
                )
            else:
                _LOGGER.debug("Connection error (attempt %d): %s", self._consecutive_failures, err)
            
            raise UpdateFailed(f"Connection error: {err}") from err
            
        except InvalidResponseError as err:
            self._consecutive_failures += 1
            _LOGGER.error("Invalid response from device: %s", err)
            raise UpdateFailed(f"Invalid response: {err}") from err
            
        except Exception as err:
            self._consecutive_failures += 1
            _LOGGER.exception("Unexpected error updating data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_request_refresh_and_wait(self) -> None:
        """
        Solicita actualización inmediata y espera a que complete.
        
        Útil después de ejecutar comandos que cambian el estado.
        """
        await self.async_request_refresh()
        # Dar tiempo a que se procese la actualización
        await self.async_wait_for_update()

    async def async_wait_for_update(self) -> None:
        """Espera a que complete la próxima actualización."""
        import asyncio
        
        # Esperar máximo 5 segundos
        timeout = 5
        start_time = self.hass.loop.time()
        
        while self.hass.loop.time() - start_time < timeout:
            if self.last_update_success:
                return
            await asyncio.sleep(0.1)
        
        _LOGGER.warning("Timeout waiting for coordinator update")

    @property
    def is_connected(self) -> bool:
        """Indica si hay conexión activa con el dispositivo."""
        return self.last_update_success and self.data is not None

    @property
    def consecutive_failures(self) -> int:
        """Número de fallos consecutivos."""
        return self._consecutive_failures

    def get_output_state(self, output_number: int) -> Dict[str, Any]:
        """
        Obtiene el estado de una salida específica.
        
        Args:
            output_number: Número de salida (1-indexed)
            
        Returns:
            Diccionario con el estado de la salida
        """
        if not self.data:
            return {}
        
        matrix_data = self.data.get("matrix", {})
        output_key = f"output_{output_number}"
        
        return matrix_data.get(output_key, {})

    def get_all_outputs(self) -> Dict[str, Dict[str, Any]]:
        """
        Obtiene el estado de todas las salidas.
        
        Returns:
            Diccionario con todos los estados de salidas
        """
        if not self.data:
            return {}
        
        return self.data.get("matrix", {})

    def get_connected_outputs(self) -> list[int]:
        """
        Obtiene lista de números de salidas con entrada conectada.
        
        Returns:
            Lista de números de salida
        """
        if not self.data:
            return []
        
        connected = []
        matrix_data = self.data.get("matrix", {})
        
        for output_key, output_data in matrix_data.items():
            if output_data.get("input") is not None:
                # Extraer número de salida del key "output_N"
                output_num = int(output_key.split("_")[1])
                connected.append(output_num)
        
        return sorted(connected)

    async def async_shutdown(self) -> None:
        """Limpieza al cerrar el coordinador."""
        _LOGGER.debug("Shutting down coordinator")
        # Limpiar callback de la API
        if self.api:
            self.api.set_change_callback(None)
