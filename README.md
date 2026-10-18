# Meroño TTS para Render

Bot de síntesis de voz para cheers de StreamElements. La versión actual se ha reescrito desde cero y usa el Custom Widget de StreamElements dentro del OBS local.

## Arquitectura

- `app/`: backend FastAPI, configuración, normalización de texto y cliente de Fish Audio.
- `widget.html`: frontend autocontenido que se pega en el Custom Widget de StreamElements.
- `dashboard.html`: panel de ajustes de voz (modelo, prosodia, muestreo, audio y monitor).
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
| `FISH_MODEL` | `s2.1-pro-free` | Modelo inicial de Fish Audio (`s2.1-pro-free`, `s2.1-pro`, `s2-pro`, `drama-3`) |
| `FISH_VOICE_MERONO` | vacío | Voz de meroño en diálogo; si está vacío se usa `FISH_VOICE_ID` |
| `FISH_VOICE_SUPIN` | vacío | Voz de supin en diálogo |
| `FISH_VOICE_COLOMBIANA` | vacío | Voz de colombiana en diálogo |
| `ADMIN_TOKEN` | vacío | Protege los cambios del dashboard; si está vacío se usa `WIDGET_TOKEN` |
| `TTS_SETTINGS_PATH` | `settings.json` | Archivo de ajustes del dashboard (efímero en Render free) |
| `ALLOWED_CHANNEL` | `alimentacionchino` | Canal único permitido |
| `MIN_BITS` | `10` | Bits mínimos para sintetizar |
| `CORS_ORIGINS` | StreamElements | Orígenes permitidos del Custom Widget |
| `AUDIO_CACHE_TTL` | `1800` | Segundos de caché de audio |
| `AUDIO_CACHE_MAX` | `100` | Entradas máximas de caché |
| `TTS_MAX_CONCURRENCY` | `2` | Síntesis simultáneas máximas |
| `TTS_MAX_PENDING` | `8` | Solicitudes pendientes máximas |
| `REQUESTS_PER_MINUTE` | `30` | Límite global de solicitudes |

El `WIDGET_TOKEN` debe ser largo y aleatorio y no debe incluirse en la URL del
widget. El `ADMIN_TOKEN` es una credencial aparte y solo se usa en el dashboard.

## Dashboard

El panel está en `https://tu-servicio.onrender.com/dashboard`. Permite elegir el
modelo (incluido **Drama 3**), ajustar prosodia, muestreo, audio y procesamiento,
y ver el monitor de caché y eventos.

Los cambios requieren `ADMIN_TOKEN` (o `WIDGET_TOKEN` si aquel está vacío). El
panel lo pide la primera vez y lo guarda solo en la sesión del navegador.
También admite `?token=...` para el primer acceso; el panel lo elimina de la
barra de direcciones automáticamente.

```text
https://tu-servicio.onrender.com/dashboard?token=TU_ADMIN_TOKEN
```

Los ajustes se guardan en `TTS_SETTINGS_PATH` y se aplican al instante, sin
reiniciar el servicio. En el plan free de Render el disco es efímero: al
reiniciarse el proceso se vuelve al `FISH_MODEL` y a los valores por defecto.
Para fijar un modelo de forma permanente, configura `FISH_MODEL` en Render.

### Drama 3

`drama-3` usa el mismo endpoint y admite direcciones de interpretación en el
texto: `[susurrando]`, `[risa]`, `[pausa]`, o etiquetas como
`<emphasis>importante</emphasis>`. No requiere cambios en el widget.

### Diálogo con varias voces

Con `drama-3` o cualquier modelo de la familia S2, el chat puede cambiar de voz
escribiendo el nombre del personaje seguido de dos puntos:

```text
meroño: hola, soy la voz de siempre supin: yo soy supin colombiana: hola mi amor
```

- Sin etiquetas se usa `FISH_VOICE_ID` (comportamiento actual).
- Con etiquetas se usa `reference_id` en array y las voces
  `FISH_VOICE_MERONO` (o `FISH_VOICE_ID` si está vacía), `FISH_VOICE_SUPIN` y
  `FISH_VOICE_COLOMBIANA`.
- Se acepta `meroño` y `merono`, sin distinguir mayúsculas.
- Si falta alguna voz o el modelo no soporta multi-hablante, se eliminan las
  etiquetas y se usa la voz única.
- Los nombres de los personajes no se muestran en la burbuja.
- Si solo habla un personaje, su foto aparece como avatar principal. Si hablan
  varios, el avatar principal es Meroño cuando participa; si no participa, es
  el primer personaje que habla. Las fotos de los demás se apilan debajo, a la
  izquierda del texto.

## Evitar audio duplicado

El servidor deduplica cada evento por rol (`stream` y `preview`) y por dos
claves: el `_id` de StreamElements y la firma de contenido. Si dos fuentes de
OBS reciben el mismo cheer, solo una recibe el audio; la otra obtiene `409` y no
reproduce nada. Además, la reproducción tiene un turno global por rol: si dos
instancias tienen un audio listo a la vez, la segunda espera a que la primera
termine, así dos cheers seguidos nunca se solapan.

- La fuente de OBS debe usar `?role=stream` si el widget no detecta OBS
  automáticamente. Una vista previa en navegador debe usar `?role=preview`.
- Deja **una sola** fuente de navegador con el overlay en OBS. No dupliques el
  Custom Widget en otra fuente ni lo tengas también en Cloud OBS.
- En OBS, revisa las propiedades de audio de la fuente: usa *Control audio via
  OBS* y evita *Monitor and Output* si también capturas el audio de escritorio,
  porque eso puede sumar la misma señal dos veces.
- El dashboard muestra `Streams` y `Duplicados`. Si `Streams` es mayor que 1,
  hay varias instancias de directo conectadas: elimina la sobrante.
- El backend antiguo de GCP está detenido y deshabilitado. Retira también su
  overlay de OBS/StreamElements para no dejar una fuente muerta.

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
- `GET /dashboard`: panel de ajustes.
- `GET /api/settings`: ajustes actuales y modelos disponibles.
- `POST /api/settings`: guarda ajustes (requiere `X-Admin-Token`).
- `POST /api/settings/reset`: restaura los valores por defecto (requiere `X-Admin-Token`).
- `GET /api/stats`: contadores de caché, mensajes, instancias activas y tiempo activo.
- `GET /api/events?limit=N`: últimos eventos para el monitor (incluye duplicados y rol).
- `POST /api/v1/speech`: devuelve el audio y requiere `X-Widget-Token`.
- `POST /api/v1/lifecycle`: informa del estado live/offline y requiere `X-Widget-Token`.
- `GET /images/*`: fotografías del overlay.

El servicio no guarda cheer, audio ni datos personales de forma permanente. La deduplicación y la caché viven en memoria y se pierden cuando Render reinicia el proceso.
