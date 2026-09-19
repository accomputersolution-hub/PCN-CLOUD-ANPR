# ANPR pipeline

The engine is a **replaceable module** (`anpr-engine/`). The backend and edge agent depend on interfaces, not on a specific weight file.

## Flow (Phase 6A — image inference)

```
Camera frame (JPEG)
  → VehicleDetector.detect(frame)
  → PlateDetector.detect(frame, vehicle)   # dedicated plate regions — not full-frame OCR
  → padded plate crop
  → multi-pass preprocess (2x/3x upscale, CLAHE, sharpen, deskew, adaptive)
  → OCRProvider.read(each variant)         # PaddleOCR on crop only
  → ensemble select (pattern + confidence + length + consistency)
  → Indian plate normalization / validation
  → structured JSON (raw_text vs normalized_text; ocr_confident flag)
```

**Phase 6A** is image inference only. **Phase 6B** (`ANPR_LIVE_ENABLED=true` on the edge agent) connects live RTSP sampled frames → temporal plate confirmation → cooldown → edge sync → ENTRY/EXIT visits. Mock ANPR remains available when `EDGE_MOCK_MODE=true`.

If OCR is not pattern-confident: `plate_detected=true`, `ocr_confident=false`, `normalized_text=null` (raw text still kept). Debug crops go to `ANPR_OCR_DEBUG_DIR`.

## Interfaces

```python
class VehicleDetector:
    def detect(self, frame) -> list[VehicleDetection]: ...

class PlateDetector:
    def detect(self, frame, vehicle=None) -> list[PlateDetection]: ...

class OCRProvider:
    def read(self, plate_crop) -> OCRResult: ...
```

`ANPRPipeline.process(frame)` — legacy single-best (mocks by default, keeps edge mock oneshot working).  
`ANPRPipeline.process_image(path)` — structured multi-vehicle / multi-plate JSON.  
`pcn_anpr.factory.build_pipeline()` — real OpenCV + PaddleOCR providers from env.

## Default implementations (Phase 6A) and licenses

| Component | Implementation | License |
| --- | --- | --- |
| Vehicle regions (default) | Ultralytics **YOLOv8n** (`yolov8n.pt`) when installed | **AGPL-3.0** — see `anpr-engine/models/README.md` |
| Vehicle regions (fallback) | OpenCV edge/contour heuristics | Apache 2.0 (`opencv-python-headless`) |
| Plate regions | OpenCV morphology / aspect-ratio plate proposals | Apache 2.0 |
| OCR | **PaddleOCR** + PaddlePaddle | **Apache 2.0** |
| Mock providers | Synthetic boxes + `MH12AB1234` | Project code |

**Ultralytics is optional** (`pip install -r anpr-engine/requirements-yolo.txt`). Default env is `ANPR_VEHICLE_DETECTOR=yolo` with automatic OpenCV fallback if Ultralytics is missing. Plate detection remains OpenCV unless you opt into the AI placeholder. Neural **plate** detectors may be added later under `ANPR_MODEL_DIR` — see `anpr-engine/models/README.md`.

## Edge Agent live pipeline (Phase 6B)

```
RTSP → continuous decode → sample (e.g. 2 FPS) → bounded ANPR queue
  → VehicleDetector → PlateDetector → OCR ensemble
  → temporal confirmation (N consistent reads in window)
  → per camera+plate cooldown
  → enqueue edge event (snapshot + crops) → backend ingest → visit match → dashboard WS
```

- Capture thread never blocks on inference; full queue drops **oldest** frames.
- `ANPR_LIVE_ENABLED=false`: detection snapshots only (Phase 5/6A hook path).
- `ANPR_DEBUG_MODE=true`: verbose confidence logs + optional plate-crop files.

See `EDGE_AGENT.md` for env vars.

## CLI

```bash
cd anpr-engine
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # optional, for real OCR

python -m anpr_engine.cli --image "..\edge-agent\data\edge-frames\frame.jpg"
python -m anpr_engine.cli --folder "..\edge-agent\data\edge-frames"
```

Outputs under `./output/`:
- console report
- `results.json`
- `annotated_<filename>.jpg`

## Normalization

`pcn_anpr.normalize` (also re-exported by `backend/app/services/plate.py`):

- Strip spaces/hyphens, uppercase → e.g. `MH12AB1234`
- Patterns: standard Indian (`XX00XX0000` variants) and Bharat series
- Confusable O/0 substitution **off** unless `ANPR_CONFUSABLE_SUBSTITUTION=true` / site setting

## Dev API

`POST /api/v1/anpr/test-image` (auth + `anpr:test` permission) — upload an image, get JSON. **Does not write events.**

## Configuration

See `.env.example`: `ANPR_ENABLED`, `OCR_ENABLED`, `PLATE_DETECTOR_ENABLED`, `ANPR_MIN_*_CONFIDENCE`, `ANPR_MODEL_DIR`, `ANPR_PROVIDER_MODE=real|mock`.

## Low-quality / empty results

The pipeline returns `plate_detected=false` (and empty plates) instead of raising when images are dark, tiny, blurry, or have no plate. CLI and API always complete.

## Tests

```bash
cd anpr-engine
pytest
```
