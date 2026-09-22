# Meroño TTS (Render, esencial)

Bot de TTS con voz clonada (Fish Audio) para cheers de StreamElements.
Versión mínima: endpoint de síntesis + overlay con burbuja. Sin dashboard,
sin dedup de servidor, sin roles.

## Qué hace

- `POST /streamelements/redeem` → devuelve audio MP3 del texto (caché en
  memoria; el mismo texto no se vuelve a sintetizar).
- `GET /` o `/widget` → overlay listo para StreamElements (inyecta la URL
  pública del servicio, no hay que editar nada).
- `GET /health` → estado (lo usa Render como health check).

Cada widget no repite cheers ya vistos (localStorage, 300 s): el overlay
de pruebas y el OBS suenan a la vez sin silenciarse, y el reenvío de
StreamElements (~60-120 s después) no duplica.

## Desplegar en Render (Blueprint)

1. Sube esta carpeta a un repo de GitHub.
2. Render → **New → Blueprint** → elige el repo (usa `render.yaml`).
3. En **Environment**, añade los secretos:
   - `FISH_API_KEY` = tu clave de Fish Audio
   - `FISH_VOICE_ID` = tu voz clonada
4. Deploy. La URL será `https://merono-tts.onrender.com` (o la que elijas).

> Plan Free: el servicio se duerme tras inactividad; el primer cheer tras
> el reposo tarda más (cold start). El widget hace ping a `/health` cada
> 4 min mientras hay directo para mantenerlo despierto.

## Conectar StreamElements / OBS

1. Abre `https://TU-APP.onrender.com/` en el navegador → ver/ver código
   del overlay (ya lleva la URL pública inyectada).
2. En StreamElements → overlay del canal → widget **Custom** → pega ese
   HTML. Opcional: añade `?test=1` a la URL de prueba para ver la burbuja
   4 s sin bits.
3. OBS → fuente Navegador con la URL del overlay de StreamElements,
   **encima** de la captura del juego. Clic derecho → *Actualizar
   navegador* tras cada cambio.

## Desarrollo local

```bash
cp .env.example .env   # y rellena FISH_API_KEY / FISH_VOICE_ID
pip install -r requirements.txt
uvicorn server:app --port 8080
```

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `FISH_API_KEY` | — | Clave de Fish Audio (obligatoria) |
| `FISH_VOICE_ID` | — | Voz clonada (obligatoria) |
| `FISH_MODEL` | `s2.1-pro-free` | Modelo |
| `PUBLIC_BASE_URL` | (auto) | Fija la URL pública inyectada en el widget |
| `REDEEM_RATE_LIMIT` / `REDEEM_RATE_WINDOW` | `20` / `60` | Rate limit por IP |
| `AUDIO_CACHE_TTL` | `1800` | TTL de la caché de audio (s) |
| `PORT` | `8080` | Puerto (Render pone el suyo con `$PORT`) |
