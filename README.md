# Meroño TTS para Render

Bot de síntesis de voz para cheers de StreamElements. La versión actual se ha reescrito desde cero y usa el Custom Widget de StreamElements dentro del OBS local.

## Arquitectura

- `app/`: backend FastAPI, configuración, normalización de texto y cliente de Fish Audio.
- `widget.html`: frontend autocontenido que se pega en el Custom Widget de StreamElements.
- `images/`: fotografías servidas por Render para la burbuja.
- `tests/`: pruebas unitarias del procesamiento de texto.
- `render.yaml`: configuración de despliegue.

El Custom Widget es la única fuente de eventos y reproducción de audio. No se debe colocar otra copia del widget en el OBS local.

## Flujo

```text
StreamElements -> Custom Widget -> POST /api/v1/speech -> Fish Audio
                                             |
                                             -> MP3 y burbuja en OBS local
```

Render mantiene el servicio despierto mediante pings únicamente cuando el widget recibe el estado `live` de StreamElements. Cuando el canal pasa a `offline`, el widget deja de hacer pings y Render puede dormir.

## Configuración de Render

En Render Blueprint se deben configurar estos secretos:

- `FISH_API_KEY`
- `FISH_VOICE_ID`
- `WIDGET_TOKEN`

También se puede fijar `PUBLIC_BASE_URL` con la URL pública del servicio.

Variables de configuración:

| Variable | Valor inicial | Uso |
|---|---|---|
| `FISH_MODEL` | `s2.1-pro-free` | Modelo de Fish Audio |
| `ALLOWED_CHANNEL` | `alimentacionchino` | Canal único permitido |
| `MIN_BITS` | `10` | Bits mínimos para sintetizar |
| `CORS_ORIGINS` | StreamElements | Orígenes permitidos del Custom Widget |
| `AUDIO_CACHE_TTL` | `1800` | Segundos de caché de audio |
| `AUDIO_CACHE_MAX` | `100` | Entradas máximas de caché |
| `TTS_MAX_CONCURRENCY` | `2` | Síntesis simultáneas máximas |
| `TTS_MAX_PENDING` | `8` | Solicitudes pendientes máximas |
| `REQUESTS_PER_MINUTE` | `30` | Límite global de solicitudes |

El token debe ser largo y aleatorio. No debe incluirse en la URL.

## Pegar el Custom Widget

1. Sustituye en `widget.html` los valores `REEMPLAZA_CON_TU_URL_DE_RENDER` y `REEMPLAZA_CON_TU_WIDGET_TOKEN`.
2. Abre el panel de StreamElements del canal `alimentacionchino`.
3. Crea o abre un Overlay y añade un widget Custom.
4. Pega el contenido completo de `widget.html`.
5. Guarda el widget.
6. Añade una fuente Navegador en el OBS local usando la URL del widget Custom de StreamElements.
7. No actives audio de una segunda copia del widget.

El navegador local puede abrir directamente el HTML para inspeccionarlo, pero los cheers solo llegarán cuando el HTML esté dentro de StreamElements.

## Modo de prueba

El frontend incluye tres formas de prueba:

- Añadir `?test=1` a una URL de prueba compatible y esperar ochocientos milisegundos.
- Ejecutar `window.meronoTest()` desde las herramientas de desarrollo del navegador.
- Ejecutar `window.meronoTest("texto de prueba")` con un texto personalizado.

La prueba utiliza el mismo endpoint y la misma configuración que un cheer real. Primero muestra la burbuja y después reproduce el MP3. El mensaje de prueba se procesa aunque el canal esté offline porque la orden es manual.

La prueba se ve y se oye en el OBS local mientras ese browser source esté activo. En la configuración actual, las fuentes del OBS local se replican también cuando Cloud OBS utiliza la entrada del móvil, por lo que la burbuja y el audio del bot continúan formando parte de la emisión durante la transición PC ↔ móvil. El Custom Widget no debe duplicarse en otra fuente del OBS local ni en OBS remoto.

## Desarrollo local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn server:app --host 0.0.0.0 --port 8080
```

Después de configurar las variables, las pruebas se ejecutan con:

```bash
pytest -q
```

## Endpoints

- `GET /health`: estado básico para el health check de Render.
- `GET /ready`: readiness; falla si faltan secretos.
- `POST /api/v1/speech`: devuelve MP3 y requiere `X-Widget-Token`.
- `POST /api/v1/lifecycle`: informa del estado live/offline y requiere `X-Widget-Token`.
- `GET /images/*`: fotografías del overlay.

El servicio no guarda cheer, audio ni datos personales de forma permanente. La deduplicación y la caché viven en memoria y se pierden cuando Render reinicia el proceso.
