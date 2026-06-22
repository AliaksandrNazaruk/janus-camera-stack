# Deployment instructions for 192.168.1.55 (depth camera Pi)

## Goal

Add an HTTP endpoint `/depth/color_frame` that returns a **real RGB frame** from the RealSense D435 color sensor (not the depth colormap from `rs.colorizer()`).

The current endpoint `/depth/frame_color_overlay` returns a **depth colormap** (a colorized depth map), NOT real colors. We need a new endpoint with actual RGB pixels.

## Architecture

Two services run on the Pi (192.168.1.55):

1. **realsense_mux** (port 8000) — a Python script that:
   - Starts the RealSense pipeline (color + depth)
   - Writes frames to FIFO files for Janus WebRTC
   - Serves a FastAPI app on port 8000 with a single endpoint `GET /depth?x=&y=`

2. **cam-control** (port 8900) — a FastAPI application (`janus_camera_page/`):
   - Camera management, configuration, Janus proxy
   - Proxies `GET /depth` to localhost:8000
   - Implements `GET /depth/frame` and `GET /depth/frame_color_overlay`

## What needs to be done

### File 1: `realsense_mux.py`

You need to find the `realsense_mux.py` file (most likely under `/opt/janus_camera_page/`, `/opt/camera/`, or a similar path).

#### Change 1a: Extend the `CameraService` class

In the `CameraService` class (right after `self._updated_at = 0.0` in `__init__`), add storage for the color frame:

```python
class CameraService:
    """
    Stores the *already-rotated* depth frame (float32, meters) and returns depth by normalized coordinates [0..1].
    Also stores the latest color frame (RGB24, uint8) for colour-overlay.
    """
    def __init__(self, rotate="none", flip_x=False, flip_y=False):
        self.rotate = rotate
        self.flip_x = flip_x
        self.flip_y = flip_y
        self._lock = threading.Lock()
        self._depth_m = None
        self._shape = None
        self._updated_at = 0.0
        # ---- NEW: Real RGB colour frame ----
        self._color_rgb: Optional[np.ndarray] = None
        self._color_shape: Optional[tuple] = None
        self._color_updated_at: float = 0.0
```

Add three new methods (after the existing `update_depth_from_z16`):

```python
    def update_color_rgb(self, rgb: np.ndarray):
        """Store the latest colour frame (uint8 HxWx3, already rotated)."""
        with self._lock:
            self._color_rgb = rgb
            self._color_shape = rgb.shape[:2]
            self._color_updated_at = time.time()

    def get_color_frame(self) -> Optional[np.ndarray]:
        """Return the latest colour frame or None."""
        with self._lock:
            return self._color_rgb.copy() if self._color_rgb is not None else None

    def get_color_timestamp(self) -> float:
        with self._lock:
            return self._color_updated_at
```

> **Important:** `Optional` may not be imported yet — check that `from typing import ..., Optional` is present in the file.

#### Change 1b: Store the color frame in the pipeline loop

In the main `while running:` loop, find the block that handles the color frame:

```python
# WAS:
            if color_mode:
                cf = frames.get_color_frame()
                if cf:
                    color_count += 1
                    if color_writer:
                        img = np.asanyarray(cf.get_data())
                        img = rotate_img(img, rotate)
                        img = np.ascontiguousarray(img)
                        color_writer.write(img.tobytes())
```

Replace it with (now `img` is created ALWAYS, not only when a writer is present):

```python
# NOW:
            if color_mode:
                cf = frames.get_color_frame()
                if cf:
                    color_count += 1
                    img = np.asanyarray(cf.get_data())
                    img = rotate_img(img, rotate)
                    img = np.ascontiguousarray(img)
                    # Store latest colour frame for HTTP endpoint
                    service.update_color_rgb(img)
                    if color_writer:
                        color_writer.write(img.tobytes())
```

#### Change 1c: Add the `/color_frame` endpoint in `make_fastapi`

Inside the `make_fastapi(service)` function, before the `app.include_router(router)` line, add:

```python
    @router.get("/color_frame")
    async def get_color_frame(
        format: str = "json",
        service: CameraService = Depends(get_camera_service),
    ):
        """Return latest D435 color frame as base64 RGB24 JSON."""
        import base64 as b64mod
        frame = service.get_color_frame()
        if frame is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"detail": "no color frame yet"})
        h, w = frame.shape[:2]
        raw_bytes = frame.tobytes()
        if format == "raw":
            return Response(content=raw_bytes, media_type="application/octet-stream",
                            headers={"X-Width": str(w), "X-Height": str(h), "X-Dtype": "uint8-rgb24"})
        encoded = b64mod.b64encode(raw_bytes).decode("ascii")
        return {"width": w, "height": h, "dtype": "uint8-rgb24",
                "timestamp": service.get_color_timestamp(), "data": encoded}
```

> **Note:** the `Response` import from FastAPI is required. Check that the start of `make_fastapi` or the file has:
> `from fastapi.responses import Response` (or that it is already available through other imports).

### File 2: `app/routes/system.py` (cam-control service)

You need to find the routes file (usually `app/routes/system.py`).

#### Change 2a: Add the proxy route `/depth/color_frame`

Find the `if CAM_TYPE == "depth_camera":` block and, inside it, **after** the existing `get_depth` route, add:

```python
    color_frame_description = (
        "Returns the latest D435 colour (RGB24) frame from the RealSense color sensor. "
        "Use format=json for base64-encoded JSON payload, or format=raw for raw bytes."
    )

    @router.get(
        f"/api/v1/{CAM_TYPE}/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    @router.get(
        "/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    async def get_depth_color_frame(format: str = "json"):
        import requests as _req
        try:
            url = f"http://localhost:8000/color_frame?format={format}"
            resp = _req.get(url, timeout=5)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            if format == "raw":
                return Response(
                    content=resp.content,
                    media_type="application/octet-stream",
                    headers={
                        "X-Width": resp.headers.get("X-Width", ""),
                        "X-Height": resp.headers.get("X-Height", ""),
                        "X-Dtype": resp.headers.get("X-Dtype", "uint8-rgb24"),
                    },
                )
            return JSONResponse(content=resp.json())
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Depth color frame proxy error: {e}")
```

> **Important:** `JSONResponse` and `Response` must be imported — they are already present in the file:
> `from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response`

## Restart

After making the changes, restart both services:

```bash
# Restart realsense_mux (port 8000):
sudo systemctl restart realsense-mux  # or whatever the service is named

# Restart cam-control (port 8900):
sudo systemctl restart cam-control    # or whatever the service is named
```

> Find the systemd service names with:
> ```bash
> systemctl list-units --type=service --state=running | grep -iE 'cam|janus|realsense|mux'
> ```

## Verification

```bash
# 1. Verify that realsense_mux returns a color frame:
curl -s "http://localhost:8000/color_frame?format=json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('Keys:', list(d.keys()))
print('Width:', d.get('width'), 'Height:', d.get('height'))
print('Dtype:', d.get('dtype'))
print('Data length:', len(d.get('data', '')))
"

# 2. Verify that cam-control proxies it:
curl -s "http://localhost:8900/depth/color_frame?format=json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('OK! Width:', d.get('width'), 'Height:', d.get('height'), 'Dtype:', d.get('dtype'))
"

# 3. Verify that these are real RGB (not a colormap):
curl -s "http://localhost:8900/depth/color_frame?format=json" | python3 -c "
import json, sys, base64
d = json.load(sys.stdin)
raw = base64.b64decode(d['data'])
w, h = d['width'], d['height']
# Sample center pixel
ci = (h//2 * w + w//2) * 3
r, g, b = raw[ci], raw[ci+1], raw[ci+2]
print(f'Center pixel: R={r} G={g} B={b}')
print(f'Size: {w}x{h}, bytes: {len(raw)}, expected: {w*h*3}')
# Values should look natural (not colormap pattern)
print('Looks like real RGB!' if max(r,g,b) > 30 else 'WARNING: very dark, check lighting')
"
```

## Expected result

After deployment, the frontend (the "Shot Color" button in the 3D viewer) will request
`/api/v1/depth_camera/depth/color_frame?format=json` through the API gateway,
and the point cloud points will be colored with the camera's real colors.

The "Shot" button still draws monochrome points (blue) without requesting color.
