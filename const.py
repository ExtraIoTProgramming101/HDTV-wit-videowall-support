"""
HDTV Matrix Constants
Constantes utilizadas en toda la integración.
"""

# Identificador del dominio de la integración
DOMAIN = "hdtv_matrix"

# Plataformas soportadas
PLATFORMS = ["select", "sensor", "button"]

# Claves de configuración
CONF_BASE_URL = "base_url"
CONF_INPUTS = "inputs"
CONF_OUTPUTS = "outputs"
CONF_SCAN_INTERVAL = "scan_interval"

# Valores por defecto
DEFAULT_SCAN_INTERVAL = 1  # segundos
DEFAULT_TIMEOUT = 10  # segundos
DEFAULT_INPUTS = 4
DEFAULT_OUTPUTS = 4

# Límites de configuración
MIN_SCAN_INTERVAL = 1  # segundos
MAX_SCAN_INTERVAL = 60  # segundos
MIN_PORTS = 1
MAX_PORTS = 64

# Información del dispositivo
MANUFACTURER = "HDTV Supply"
MODEL = "HDMI Matrix Switch"
