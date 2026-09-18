# ANPR model directory

Phase 6A **does not ship neural plate-detector weights**. Default plate proposal remains
OpenCV morphology (`ANPR_PLATE_DETECTOR=opencv`).

| Component | Package / files | License | Notes |
| --- | --- | --- | --- |
| Vehicle region proposal | OpenCV heuristics (`opencv_vehicle.py`) | Apache 2.0 | No weight file |
| Plate region proposal (default) | OpenCV morphology (`opencv_plate.py`) | Apache 2.0 | No weight file |
| Plate region proposal (optional AI) | `AILicensePlateDetector` placeholder | Framework Apache 2.0 | **No weights shipped** — see below |
| OCR | PaddleOCR + PaddlePaddle | Apache 2.0 | Weights auto-downloaded on first OCR run into Paddle hub cache |

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

Do **not** add Ultralytics AGPL YOLO or unknown commercial models as a default dependency.

## Install

```bash
cd anpr-engine
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # for real OCR
```

## Eval script

```bash
cd anpr-engine
python scripts/eval_plate_detectors.py --out output/plate_det_eval.json
```
