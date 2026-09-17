# ANPR model directory

Phase 6A **does not ship neural plate-detector weights**.

| Component | Package / files | License | Notes |
| --- | --- | --- | --- |
| Vehicle region proposal | OpenCV heuristics (`opencv_vehicle.py`) | Apache 2.0 | No weight file |
| Plate region proposal | OpenCV morphology (`opencv_plate.py`) | Apache 2.0 | No weight file |
| OCR | PaddleOCR + PaddlePaddle | Apache 2.0 | Weights auto-downloaded on first OCR run into Paddle hub cache |

## Optional future neural plate detector

Place commercially licensed weights here when available, e.g.:

```
models/
  plate_detector/
    README.txt   # license + source
    model.onnx
```

Set `ANPR_MODEL_DIR=./models` (relative path). Do **not** add Ultralytics AGPL YOLO as a default dependency without explicit approval.

## Install

```bash
cd anpr-engine
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # for real OCR
```
