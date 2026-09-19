# ANPR model directory

Phase 6A plate proposal remains OpenCV morphology (`ANPR_PLATE_DETECTOR=opencv`).
Vehicle proposal defaults to **YOLOv8n** when Ultralytics is installed
(`ANPR_VEHICLE_DETECTOR=yolo`); otherwise OpenCV contour heuristics.

| Component | Package / files | License | Notes |
| --- | --- | --- | --- |
| Vehicle detector (default) | Ultralytics **YOLOv8n** `yolov8n.pt` | **AGPL-3.0** (Ultralytics) + COCO-trained weights | See below |
| Vehicle fallback | OpenCV heuristics (`opencv_vehicle.py`) | Apache 2.0 | `ANPR_VEHICLE_DETECTOR=opencv` |
| Plate region proposal (default) | OpenCV morphology (`opencv_plate.py`) | Apache 2.0 | No weight file |
| Plate region proposal (optional AI) | `AILicensePlateDetector` placeholder | Framework Apache 2.0 | **No weights shipped** — see below |
| OCR | PaddleOCR + PaddlePaddle | Apache 2.0 | Weights auto-downloaded on first OCR run into Paddle hub cache |

## YOLO vehicle detector (YOLOv8n)

| Field | Value |
| --- | --- |
| Model | **YOLOv8n** (nano) — `yolov8n.pt` |
| Default path | `{ANPR_MODEL_DIR}/vehicle/yolov8n.pt` (default `./models/vehicle/yolov8n.pt`) |
| Official source | [Ultralytics YOLOv8 assets](https://github.com/ultralytics/assets/releases) — file `yolov8n.pt` |
| Framework | [Ultralytics](https://github.com/ultralytics/ultralytics) **AGPL-3.0** |
| Classes used | COCO: `car` (2), `motorcycle` (3), `bus` (5), `truck` (7). **Bicycle omitted** (rack/false-positive risk at gates). |
| Size | ~6 MB nano weights — do not substitute large `yolov8x` / undocumented checkpoints |
| Config | `ANPR_VEHICLE_DETECTOR=yolo\|opencv\|mock` |

### AGPL-3.0 notice

Using Ultralytics YOLO pulls **AGPL-3.0**. That is a **strong copyleft** license: distributing a modified version that includes Ultralytics may require releasing corresponding source under AGPL. Prefer OpenCV vehicle detection (`ANPR_VEHICLE_DETECTOR=opencv`) if AGPL is unacceptable for your deployment.

Install explicitly (not part of core `requirements.txt`):

```bash
cd anpr-engine
pip install -r requirements-yolo.txt
# Optional CUDA torch (when GPU kernels support your GPU):
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

On first YOLO run, if `models/vehicle/yolov8n.pt` is missing, Ultralytics may download the **official** `yolov8n.pt` once. Do not drop random third-party `.pt` files here.

Override path:

```bash
export ANPR_VEHICLE_DETECTOR=yolo
export ANPR_YOLO_VEHICLE_WEIGHTS=./models/vehicle/yolov8n.pt
export ANPR_YOLO_VEHICLE_CONF=0.25
```

When YOLO returns no boxes it returns `[]` (no full-frame fallback). The pipeline may still synthesize hosts from unhosted plates.

## AI plate detector PoC (Phase 2 decision — placeholder only)

We evaluated **PaddleDetection / PP-Vehicle** plate detection as a candidate for a
lightweight full-plate bbox before PaddleOCR. **We do not download or bundle those
weights in this repo.**

### Candidate (not shipped)

| Field | Value |
| --- | --- |
| Name | PP-Vehicle plate det / `ch_PP-OCRv3_det_infer` |
| Typical URL (reference only) | PaddleDetection PP-Vehicle model zoo — plate detection det-infer package |
| Framework license | **Apache 2.0** (PaddleOCR / PaddleDetection / PaddlePaddle) |
| Weights provenance | Fine-tuned on **CCPD** (Chinese city parking plates). CCPD repo claims MIT; **commercial redistribution of the fine-tuned PP-Vehicle weights and suitability for Indian plates are not clear enough for blind download** |
| Claimed size | **~3.9 MB** for the det-infer package |
| Domain fit | **Chinese-plate oriented** — unsuitable to ship as the default Indian-plate detector without evaluation |

### Local placement (only if/when approved)

```
models/
  plate_detector/
    ch_PP-OCRv3_det_infer/   # or your approved export
      inference.pdmodel
      inference.pdiparams
      inference.pdiparams.info
```

Set:

```bash
# Still never the production default — default remains opencv
export ANPR_PLATE_DETECTOR=ai          # or compare
export ANPR_AI_PLATE_MODEL_DIR=./models/plate_detector/ch_PP-OCRv3_det_infer
```

### Behaviour without / with model path

| Condition | `detect()` result |
| --- | --- |
| `ANPR_AI_PLATE_MODEL_DIR` missing or invalid | **Empty list** `[]`; warning logged once; AI mode falls back to OpenCV |
| Path present with expected files | Path validated; **this PoC still returns `[]` for AI boxes** until an approved Indian-plate inference backend is hooked (CCPD weights are not auto-run) |
| `ANPR_PLATE_DETECTOR=compare` | Both OpenCV + AI run; timings/bboxes logged; **OpenCV result feeds OCR** (no duplicate events) |
| `ANPR_PLATE_DETECTOR=opencv` (default) | Unchanged OpenCV path |

## Hybrid plate mode

`ANPR_PLATE_DETECTOR=hybrid` — per YOLO vehicle: YOLO plate first; OpenCV only if YOLO
returns no usable (empty or quality-rejected) candidate. Full-frame OpenCV scan skipped.
Default remains **`opencv`**.

Config: `ANPR_PLATE_DETECTOR=yolo` (default remains **`opencv`** until Indian gate validation is complete).

| Field | Value |
| --- | --- |
| Class | `YOLOPlateDetector` (`pcn_anpr/yolo_plate.py`) |
| Weight file | `models/plate/yolov8n_plate_yasirfaizahmed_best.pt` |
| Source URL | https://huggingface.co/yasirfaizahmed/license-plate-object-detection/resolve/main/best.pt |
| Hugging Face repo | [yasirfaizahmed/license-plate-object-detection](https://huggingface.co/yasirfaizahmed/license-plate-object-detection) |
| Upstream filename | `best.pt` |
| Architecture | YOLOv8n fine-tune (Ultralytics) — **not** COCO `yolov8n.pt` |
| Dataset | [keremberke/license-plate-object-detection](https://huggingface.co/datasets/keremberke/license-plate-object-detection) (Roboflow VRP export) |
| Dataset license | **CC BY 4.0** |
| Weight card license | **Apache-2.0** (Hugging Face model card) |
| Runtime | Ultralytics **AGPL-3.0** |
| SHA256 (HF LFS oid) | `d06657407970f80f1a12eb9f340661ecd003bbe44ff8feac3d5bc38845f11a94` |

```bash
export ANPR_PLATE_DETECTOR=yolo   # eval
export ANPR_YOLO_PLATE_WEIGHTS=./models/plate/yolov8n_plate_yasirfaizahmed_best.pt
export ANPR_YOLO_PLATE_CONF=0.25
# Keep vehicle YOLO:
export ANPR_VEHICLE_DETECTOR=yolo
```

On first `yolo` plate run, missing weights are downloaded from the documented HF URL and checksum-verified.

**Do not claim Indian-plate accuracy** until the gate / Indian fixture eval passes. This checkpoint is general VRP (mostly non-Indian). Motorcycle two-line plates may still be missed.

Plate-detector YOLO (separate from vehicle YOLO) remains **evaluation-only** — production default stays OpenCV.

## Install

```bash
cd anpr-engine
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # for real OCR
pip install -r requirements-yolo.txt  # optional AGPL vehicle YOLO
```

## Eval script

```bash
cd anpr-engine
python scripts/eval_plate_detectors.py --out output/plate_det_eval.json
```
