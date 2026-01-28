"""
HDTV Matrix Integration para Home Assistant
Integración para controlar matrices HDMI HDTV Supply.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HDTVMatrixApi, ConnectionError, HDTVMatrixError
from .coordinator import HDTVCoordinator
from .const import DOMAIN, CONF_SCAN_INTERVAL
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "select", "button"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """
    Configuración inicial de la integración.
    
    Args:
        hass: Instancia de Home Assistant
        config: Configuración desde configuration.yaml
        
    Returns:
        True si la configuración es exitosa
    """
    _LOGGER.debug("Inicializando integración %s", DOMAIN)
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Configura la integración desde una entrada de configuración.
    
    Args:
        hass: Instancia de Home Assistant
        entry: Entrada de configuración
        
    Returns:
        True si la configuración es exitosa
        
    Raises:
        ConfigEntryNotReady: Si el dispositivo no está disponible
    """
    _LOGGER.debug("Configurando %s (entry_id=%s)", DOMAIN, entry.entry_id)

    # Obtener sesión HTTP
    session = async_get_clientsession(hass)

    # Obtener configuración
    base_url = entry.data.get("base_url")
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, 1)
    )
    
    # Validar y normalizar scan_interval
    scan_interval = _validate_scan_interval(scan_interval, entry.entry_id)
    
    _LOGGER.info(
        "Configurando matriz HDTV - URL: %s, Intervalo: %ds",
        base_url,
        scan_interval
    )

    # Crear instancia de la API
    try:
        api = HDTVMatrixApi(session, base_url, timeout=10)
    except Exception as err:
        _LOGGER.error("Error al crear cliente API: %s", err)
        raise ConfigEntryNotReady(f"Failed to create API client: {err}") from err
    
    # Probar conexión con el dispositivo
    try:
        _LOGGER.debug("Probando conexión con el dispositivo...")
        connection_ok = await api.test_connection()
        
        if not connection_ok:
            raise ConfigEntryNotReady(
                "Device not responding. Please check the URL and network connection."
            )
        
        _LOGGER.info("Conexión con dispositivo establecida correctamente")
        
    except ConnectionError as err:
        _LOGGER.error("No se pudo conectar con el dispositivo: %s", err)
        raise ConfigEntryNotReady(
            f"Cannot connect to device at {base_url}: {err}"
        ) from err
    
    except HDTVMatrixError as err:
        _LOGGER.error("Error de la matriz HDTV: %s", err)
        raise ConfigEntryNotReady(str(err)) from err

    # Crear coordinador
    coordinator = HDTVCoordinator(
        hass,
        api,
        update_interval=timedelta(seconds=scan_interval)
    )

    # Guardar datos en hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "entry": entry,
    }

    # Registrar listener para cambios en opciones
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Realizar primera actualización
    try:
        _LOGGER.debug("Realizando primera actualización del coordinador...")
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.info("Primera actualización completada exitosamente")
        
    except Exception as err:
        _LOGGER.error(
            "Falló la primera actualización del coordinador: %s",
            err
        )
        # Limpiar datos antes de fallar
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady(
            f"Failed to fetch initial data: {err}"
        ) from err

    # Configurar servicios (solo una vez, en la primera entrada)
    if len(hass.data[DOMAIN]) == 1:
        await async_setup_services(hass)
        _LOGGER.debug("Servicios configurados")

    # Cargar plataformas (sensor, select)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Integración %s configurada correctamente (entry_id=%s)",
        DOMAIN,
        entry.entry_id
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Descarga la integración cuando se elimina la entrada.
    
    Args:
        hass: Instancia de Home Assistant
        entry: Entrada de configuración
        
    Returns:
        True si la descarga es exitosa
    """
    _LOGGER.debug("Descargando %s (entry_id=%s)", DOMAIN, entry.entry_id)

    # Descargar plataformas
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if not unload_ok:
        _LOGGER.warning(
            "No se pudieron descargar todas las plataformas de %s",
            DOMAIN
        )
        return False

    # Obtener datos de la entrada
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    
    if entry_data:
        coordinator = entry_data.get("coordinator")
        api = entry_data.get("api")
        
        # Limpiar coordinador
        if coordinator:
            try:
                await coordinator.async_shutdown()
                _LOGGER.debug("Coordinador detenido correctamente")
            except Exception as e:
                _LOGGER.exception("Error al detener coordinador: %s", e)
        
        # Limpiar callback de API
        if api:
            try:
                api.set_change_callback(None)
                _LOGGER.debug("Callback de API removido")
            except Exception as e:
                _LOGGER.exception("Error al limpiar API: %s", e)

    # Descargar servicios si no quedan más entradas
    if not hass.data.get(DOMAIN):
        await async_unload_services(hass)
        _LOGGER.debug("Servicios descargados")

    _LOGGER.info("Integración %s descargada (entry_id=%s)", DOMAIN, entry.entry_id)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Recarga la integración cuando cambian las opciones.
    
    Args:
        hass: Instancia de Home Assistant
        entry: Entrada de configuración
    """
    _LOGGER.debug(
        "Recargando entry %s de %s tras cambio de opciones",
        entry.entry_id,
        DOMAIN
    )
    await hass.config_entries.async_reload(entry.entry_id)


def _validate_scan_interval(scan_interval: any, entry_id: str) -> int:
    """
    Valida y normaliza el intervalo de escaneo.
    
    Args:
        scan_interval: Valor a validar
        entry_id: ID de la entrada (para logging)
        
    Returns:
        Intervalo validado entre 1 y 60 segundos
    """
    try:
        interval = int(scan_interval)
        
        if interval < 1:
            _LOGGER.warning(
                "scan_interval %d es muy bajo, usando 1 segundo (entry: %s)",
                interval,
                entry_id
            )
            return 1
        
        if interval > 60:
            _LOGGER.warning(
                "scan_interval %d es muy alto, usando 60 segundos (entry: %s)",
                interval,
                entry_id
            )
            return 60
        
        return interval
        
    except (ValueError, TypeError):
        _LOGGER.warning(
            "scan_interval inválido '%s', usando 1 segundo por defecto (entry: %s)",
            scan_interval,
            entry_id
        )
        return 1
