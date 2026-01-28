
"""
HDTV Matrix API Client
Módulo para interactuar con la API REST de matrices HDMI HDTV Supply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol
from urllib.parse import urlencode, quote

import aiohttp

_LOGGER = logging.getLogger(__name__)


# ============================================================================
# CONSTANTES Y CONFIGURACIÓN
# ============================================================================

class APIEndpoint(str, Enum):
    """Endpoints disponibles de la API."""
    STATUS = "get_json_scene.php"
    COMMAND = "get_json_cmd.php"
    SPLICE = "get_json_splice.php"


class MatrixCommand(str, Enum):
    """Comandos soportados por la matriz."""
    OUTPUT_TO_OUTPUT = "o2ox"  # Enrutar entrada a salida


class HTTPMethod(str, Enum):
    """Métodos HTTP soportados."""
    GET = "GET"
    POST = "POST"


# Valor que indica salida desconectada en la API
DISCONNECTED_VALUE = 65535

# Timeout por defecto para requests HTTP (segundos)
DEFAULT_TIMEOUT = 10


# ============================================================================
# MODELOS DE DATOS
# ============================================================================

@dataclass
class MatrixOutput:
    """Representa el estado de una salida de la matriz."""
    output_number: int
    input_number: Optional[int]  # None significa desconectado
    raw_value: int
    
    @property
    def is_connected(self) -> bool:
        """Indica si la salida tiene una entrada conectada."""
        return self.input_number is not None
    
    def __repr__(self) -> str:
        input_str = f"Input {self.input_number}" if self.is_connected else "Disconnected"
        return f"Outputs {self.output_number}: {input_str}"


@dataclass
class MatrixState:
    """Estado completo de la matriz HDMI."""
    outputs: Dict[int, MatrixOutput] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    @property
    def total_outputs(self) -> int:
        """Número total de salidas en la matriz."""
        return len(self.outputs)
    
    def get_output(self, output_number: int) -> Optional[MatrixOutput]:
        """Obtiene el estado de una salida específica."""
        return self.outputs.get(output_number)
    
    def get_connected_outputs(self) -> List[MatrixOutput]:
        """Retorna lista de salidas con entrada conectada."""
        return [output for output in self.outputs.values() if output.is_connected]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el estado a diccionario para compatibilidad."""
        matrix_dict = {}
        for num, output in self.outputs.items():
            matrix_dict[f"output_{num}"] = {
                "input": output.input_number,
                "raw_value": output.raw_value
            }
        
        return {
            "matrix": matrix_dict,
            "total_outputs": self.total_outputs,
            "timestamp": self.timestamp
        }

@dataclass
class HTTPRequest:
    """Información de una petición HTTP."""
    url: str
    method: HTTPMethod
    params: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    start_time: float = field(default_factory=time.time)
    
    @property
    def elapsed_ms(self) -> int:
        """Tiempo transcurrido en milisegundos."""
        return int((time.time() - self.start_time) * 1000)
    
    def build_curl_command(self) -> str:
        """Construye comando curl equivalente para debugging."""
        query = f"?{urlencode(self.params)}" if self.params else ""
        curl_cmd = f"curl --location '{self.url}{query}'"
        
        if self.headers:
            for key, value in self.headers.items():
                curl_cmd += f" \\\n  --header '{key}: {value}'"
        
        return curl_cmd

@dataclass
class HTTPResponse:
    """Resultado de una petición HTTP."""
    request: HTTPRequest
    status_code: int
    data: Any
    success: bool
    error_message: Optional[str] = None
    
    @property
    def response_time_ms(self) -> int:
        """Tiempo de respuesta en milisegundos."""
        return self.request.elapsed_ms


# ============================================================================
# PROTOCOLOS Y CALLBACKS
# ============================================================================

class ChangeCallback(Protocol):
    """Protocolo para callbacks de cambios en la matriz."""
    def __call__(self) -> None:
        """Llamado cuando hay cambios en el estado."""
        ...


# ============================================================================
# EXCEPCIONES PERSONALIZADAS
# ============================================================================

class HDTVMatrixError(Exception):
    """Excepción base para errores de la API de matriz HDTV."""
    pass


class ConnectionError(HDTVMatrixError):
    """Error de conexión con el dispositivo."""
    pass


class InvalidResponseError(HDTVMatrixError):
    """Respuesta inválida del dispositivo."""
    pass


class CommandError(HDTVMatrixError):
    """Error al ejecutar un comando."""
    pass


# ============================================================================
# UTILIDADES
# ============================================================================

class RequestLogger:
    """Gestiona el logging de peticiones HTTP."""
    
    @staticmethod
    def log_request(
        response: HTTPResponse,
        operation: Optional[str] = None,
        include_curl: bool = True
    ) -> None:
        """Registra información de una petición HTTP."""
        request = response.request
        
        # Construir snippet de curl si se requiere
        curl_snippet = ""
        if include_curl:
            curl_snippet = f"\n{request.build_curl_command()}"
        
        # Información de la operación
        op_info = operation if operation else "HTTP Request"
        
        # Mensaje base
        if response.success:
            log_level = logging.INFO
            status_msg = "Success"
        else:
            log_level = logging.ERROR
            status_msg = f"Failed: {response.error_message or 'Unknown error'}"
        
        _LOGGER.log(
            log_level,
            "%s | Status: %d | %s | Response Time: %d ms%s",
            op_info,
            response.status_code,
            status_msg,
            response.response_time_ms,
            curl_snippet
        )


class StateParser:
    """Parsea respuestas de la API a objetos de dominio."""
    
    @staticmethod
    def parse_status_array(status_array: List[int]) -> MatrixState:
        """
        Parsea el array de estado devuelto por get_json_scene.php
        
        Args:
            status_array: Array donde índice = salida-1, valor = entrada-1 o 65535
            
        Returns:
            MatrixState con el estado parseado
        """
        outputs = {}
        
        for output_idx, raw_value in enumerate(status_array):
            output_num = output_idx + 1
            
            # 65535 significa desconectado
            if raw_value == DISCONNECTED_VALUE:
                input_num = None
            else:
                input_num = raw_value + 1
            
            outputs[output_num] = MatrixOutput(
                output_number=output_num,
                input_number=input_num,
                raw_value=raw_value
            )
        
        return MatrixState(outputs=outputs, timestamp=time.time())


class SplicePayloadBuilder:
    """Construye payloads para operaciones splice."""
    
    @staticmethod
    def build_payload(
        num_inputs: 2,
        num_outputs: 2,
        data: Optional[List[str]] = None
    ) -> str:
        """
        Genera payload codificado para splice.
        
        Args:
            num_inputs: Número de entradas (x)
            num_outputs: Número de salidas (y)
            data: Lista de valores. Si None, usa [-1] * (x * y)
            
        Returns:
            String URL-encoded del payload JSON
            
        Raises:
            ValueError: Si la longitud de data no coincide
        """
        if data is None:
            data = ["-1"] * (num_inputs * num_outputs)
        elif len(data) != num_inputs * num_outputs:
            raise ValueError(
                f"Data length must be {num_inputs * num_outputs} "
                f"(num_inputs * num_outputs), got {len(data)}"
            )
        
        payload = [{
            "x": 2,
            "y": 2,
            "data": data
        }]
        
        # JSON compacto
        json_str = json.dumps(payload, separators=(",", ":"))
        
        # URL encode completo
        return quote(json_str, safe="")


# ============================================================================
# CLIENTE API PRINCIPAL
# ============================================================================

class HDTVMatrixApi:
    """
    Cliente asíncrono para la API de matrices HDMI HDTV Supply.
    
    Esta clase proporciona una interfaz de alto nivel para interactuar
    con matrices HDMI a través de sus endpoints REST.
    """
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT
    ) -> None:
        """
        Inicializa el cliente API.
        
        Args:
            session: Sesión aiohttp para hacer requests
            base_url: URL base del dispositivo (ej: http://192.168.1.100/)
            timeout: Timeout en segundos para requests
        """
        # Normalizar URL base
        self._base_url = base_url.rstrip("/") + "/"
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._change_callback: Optional[ChangeCallback] = None
        
        _LOGGER.debug("API client initialized for %s", self._base_url)
    
    # ========================================================================
    # PROPIEDADES Y CONFIGURACIÓN
    # ========================================================================
    
    @property
    def base_url(self) -> str:
        """URL base del dispositivo."""
        return self._base_url
    
    def set_change_callback(self, callback: Optional[ChangeCallback]) -> None:
        """
        Registra un callback para notificaciones de cambios.
        
        Args:
            callback: Función a llamar cuando hay cambios, o None para limpiar
        """
        self._change_callback = callback
        _LOGGER.debug("Change callback %s", "registered" if callback else "cleared")
    
    async def notify_change(self) -> None:
        """Notifica cambios ejecutando el callback registrado."""
        if self._change_callback:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._change_callback
                )
            except Exception as e:
                _LOGGER.exception("Error in change callback: %s", e)
    
    # ========================================================================
    # MÉTODOS HTTP DE BAJO NIVEL
    # ========================================================================
    
    def _build_headers(self) -> Dict[str, str]:
        """Construye headers HTTP estándar."""
        return {"Content-Type": "application/json"}
    
    async def _execute_request(
        self,
        endpoint: APIEndpoint,
        params: Optional[Dict[str, str]] = None,
        operation: Optional[str] = None
    ) -> HTTPResponse:
        """
        Ejecuta una petición HTTP GET.
        
        Args:
            endpoint: Endpoint de la API
            params: Parámetros de query string
            operation: Nombre de la operación para logging
            
        Returns:
            HTTPResponse con el resultado
            
        Raises:
            ConnectionError: Error de red o timeout
            InvalidResponseError: Respuesta malformada
        """
        url = self._base_url + endpoint.value
        headers = self._build_headers()
        
        request = HTTPRequest(
            url=url,
            method=HTTPMethod.GET,
            params=params,
            headers=headers
        )
        
        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout
            ) as resp:
                
                # Intentar parsear JSON
                try:
                    data = await resp.json(content_type=None)
                    success = resp.status == 200
                    error_msg = None if success else f"HTTP {resp.status}"
                    
                except Exception as json_error:
                    data = await resp.text()
                    success = False
                    error_msg = f"JSON parse error: {str(json_error)}"
                
                response = HTTPResponse(
                    request=request,
                    status_code=resp.status,
                    data=data,
                    success=success,
                    error_message=error_msg
                )
                
                RequestLogger.log_request(response, operation)
                
                if not success:
                    if resp.status >= 500:
                        raise ConnectionError(f"Server error: {error_msg}")
                    elif resp.status >= 400:
                        raise InvalidResponseError(f"Client error: {error_msg}")
                
                return response
                
        except asyncio.TimeoutError as e:
            error_msg = f"Request timeout after {self._timeout.total}s"
            _LOGGER.error("%s: %s", operation or "Request", error_msg)
            raise ConnectionError(error_msg) from e
            
        except aiohttp.ClientError as e:
            error_msg = f"Connection error: {str(e)}"
            _LOGGER.error("%s: %s", operation or "Request", error_msg)
            raise ConnectionError(error_msg) from e
    
    # ========================================================================
    # OPERACIONES DE LA MATRIZ
    # ========================================================================
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Obtiene el estado actual de todas las salidas de la matriz.
        
        Returns:
            Diccionario con el estado de la matriz (formato legacy para compatibilidad)
            
        Raises:
            ConnectionError: Error de conexión
            InvalidResponseError: Respuesta inválida
        """
        response = await self._execute_request(
            endpoint=APIEndpoint.STATUS,
            params={"id": "0"},
            operation="Get Status"
        )
        
        # Parsear respuesta
        if isinstance(response.data, list):
            state = StateParser.parse_status_array(response.data)
            return state.to_dict()
        else:
            # Si ya es dict, devolverlo tal cual
            return response.data
    
    async def get_status_typed(self) -> MatrixState:
        """
        Obtiene el estado actual como objeto tipado.
        
        Returns:
            MatrixState con el estado actual
            
        Raises:
            ConnectionError: Error de conexión
            InvalidResponseError: Respuesta inválida
        """
        response = await self._execute_request(
            endpoint=APIEndpoint.STATUS,
            params={"id": "0"},
            operation="Get Status (Typed)"
        )
        
        if isinstance(response.data, list):
            return StateParser.parse_status_array(response.data)
        else:
            raise InvalidResponseError(
                f"Expected array response, got {type(response.data)}"
            )
    
    async def set_route(self, input_port: int, output_port: int) -> Dict[str, Any]:
        """
        Enruta una entrada a una salida específica.
        
        Args:
            input_port: Número de entrada (1-indexed)
            output_port: Número de salida (1-indexed)
            
        Returns:
            Respuesta de la API
            
        Raises:
            ConnectionError: Error de conexión
            CommandError: Error al ejecutar el comando
        """
        # Convertir a 0-indexed para la API
        input_idx = input_port - 1
        output_idx = output_port - 1
        
        params = {
            "cmd": MatrixCommand.OUTPUT_TO_OUTPUT.value,
            "prm": f"{input_idx},{output_idx}"
        }
        
        operation = f"Set Route: Input {input_port} → Output {output_port}"
        
        try:
            response = await self._execute_request(
                endpoint=APIEndpoint.COMMAND,
                params=params,
                operation=operation
            )
            
            _LOGGER.info("Route set successfully: Input %d → Output %d", input_port, output_port)
            
            # Notificar cambio
            await self.notify_change()
            
            return response.data
            
        except (ConnectionError, InvalidResponseError) as e:
            raise CommandError(f"Failed to set route: {str(e)}") from e
    
    async def set_splice_and_video(self) -> None:
        """
        Ejecuta secuencialmente los 4 casos predefinidos de splice:
        - cmd=splice
        - get_json_splice.php

        Casos:
        1) ["1","-1","-1","-1"]
        2) ["1","2","-1","-1"]
        3) ["1","2","3","-1"]
        4) ["1","2","3","4"]
        """

        # -----------------------------
        # Casos predefinidos (orden exacto de los curl)
        # -----------------------------
        cases = [
            {
                "prm": "1,1,1,2,2,1,",
                "splice": ["1", "-1", "-1", "-1"]
            },
            {
                "prm": "2,1,2,2,2,1,",
                "splice": ["1", "2", "-1", "-1"]
            },
            {
                "prm": "3,1,3,2,2,1,",
                "splice": ["1", "2", "3", "-1"]
            },
            {
                "prm": "4,1,4,2,2,1,",
                "splice": ["1", "2", "3", "4"]
            }
        ]

        cmd_url = self._base_url + "/get_json_cmd.php"
        splice_url = self._base_url + "get_json_splice.php"

        for idx, case in enumerate(cases, start=1):

            # =============================
            # 1️⃣ cmd=splice
            # =============================
            prm = case["prm"]
            full_cmd_url = f"{cmd_url}?cmd=splice&prm={prm}"

            operation = f"Videowall splice CMD case {idx}"

            _LOGGER.info("Ejecutando %s → prm=%s", operation, prm)

            try:
                request = HTTPRequest(
                    url=cmd_url,
                    method=HTTPMethod.GET,
                    params={"cmd": "splice", "prm": prm}
                )

                async with self._session.get(
                    full_cmd_url,
                    headers=self._build_headers(),
                    timeout=self._timeout
                ) as resp:
                    text = await resp.text()
                    success = resp.status == 200
                    error_msg = None if success else f"HTTP {resp.status}"

                    data = text  # ✅ SIEMPRE definir data

                    response = HTTPResponse(
                        request=request,
                        status_code=resp.status,
                        data=data,
                        success=success,
                        error_message=error_msg
                    )

                    RequestLogger.log_request(response, operation)

                    if not success:
                        raise CommandError(
                            f"Splice CMD failed (case {idx}): {error_msg}"
                        )

                    RequestLogger.log_request(response, operation)

                    if not success:
                        raise CommandError(
                            f"Splice CMD failed (case {idx}): {error_msg}"
                        )


                    response = HTTPResponse(
                        request=request,
                        status_code=resp.status,
                        data=data,
                        success=success,
                        error_message=error_msg
                    )

                    RequestLogger.log_request(response, operation)

                    if not success:
                        raise CommandError(
                            f"Splice CMD failed (case {idx}): {error_msg}"
                        )

            except asyncio.TimeoutError as e:
                raise ConnectionError(
                    f"Splice CMD timeout after {self._timeout.total}s"
                ) from e

            except aiohttp.ClientError as e:
                raise ConnectionError(
                    f"Splice CMD connection error: {str(e)}"
                ) from e

            # =============================
            # 2️⃣ get_json_splice.php
            # =============================
            splice_payload = [
                {
                    "x": "2",
                    "y": "2",
                    "data": case["splice"]
                }
            ]

            params = {
                "splice": json.dumps(splice_payload)
            }

            _LOGGER.info(
                "Ejecutando Videowall splice DATA case %d → %s",
                idx, case["splice"]
            )

            try:
                async with self._session.get(
                    splice_url,
                    params=params,
                    headers=self._build_headers(),
                    timeout=self._timeout
                ) as resp:

                    if resp.status != 200:
                        text = await resp.text()
                        raise CommandError(
                            f"Videowall splice DATA failed (case {idx}): "
                            f"HTTP {resp.status} - {text}"
                        )

            except Exception as e:
                raise CommandError(
                    f"Error configurando videowall DATA (case {idx}): {e}"
                )

            _LOGGER.info("Caso %d completado correctamente", idx)

        # -----------------------------
        # 3️⃣ Notificar cambio global
        # -----------------------------
        await self.notify_change()

        _LOGGER.info("Todos los casos de splice y videowall ejecutados exitosamente")


    async def clear_videowall_mode(self) -> None:
        """
        Resetea completamente el 

        http://{{base}}/get_json_splice.php?splice=[]
        """

        url = self._base_url + "get_json_splice.php"
        params = {
            "splice": "[]"
        }

        _LOGGER.info("Reseteando ")

        try:
            async with self._session.get(
                url,
                params=params,
                headers=self._build_headers()
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise CommandError(
                        f"Error limpiando : HTTP {resp.status} - {text}"
                    )
        except Exception as e:
            raise CommandError(f"Error limpiando : {e}")

        _LOGGER.info("videowall reseteado correctamente")

    async def set_all_to_input(self, input_port: int, num_outputs: int) -> None:
        """
        Enruta una entrada a todas las salidas.
        
        Args:
            input_port: Número de entrada (1-indexed)
            num_outputs: Número total de salidas
            
        Raises:
            CommandError: Error al ejecutar comandos
        """
        _LOGGER.info("Setting all num_outputs to input %d", input_port)
        
        errors = []
        for num_outputs_num in range(1, num_outputs + 1):
            try:
                await self.set_route(input_port, num_outputs_num)
            except Exception as e:
                errors.append(f"num_outputs {num_outputs_num}: {str(e)}")
        
        if errors:
            error_msg = "; ".join(errors)
            raise CommandError(f"Failed to set all num_outputs: {error_msg}")
        
        _LOGGER.info("All num_outputs set to input %d", input_port)
    
    async def disconnect_num_outputs(self, num_outputs_port: int) -> None:
        """
        Desconecta una salida (si es soportado por el dispositivo).
        
        Args:
            num_outputs_port: Número de salida (1-indexed)
            
        Note:
            Esta operación puede no estar soportada por todos los dispositivos.
        """
        _LOGGER.warning(
            "Disconnect operation requested for num_outputs %d. "
            "This may not be supported by the device.",
            num_outputs_port
        )
        # Implementación depende del dispositivo específico
        raise NotImplementedError("Disconnect operation not implemented")
    
    # ========================================================================
    # MÉTODOS DE DEPURACIÓN Y UTILIDADES
    # ========================================================================
    
    async def test_connection(self) -> bool:
        """
        Prueba la conexión con el dispositivo.
        
        Returns:
            True si la conexión es exitosa, False en caso contrario
        """
        try:
            await self.get_status()
            _LOGGER.info("Connection test successful")
            return True
        except Exception as e:
            _LOGGER.error("Connection test failed: %s", e)
            return False
    
    def __repr__(self) -> str:
        """Representación string del cliente API."""
        return f"HDTVMatrixApi(base_url='{self._base_url}')"
