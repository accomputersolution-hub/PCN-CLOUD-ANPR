from __future__ import annotations

"""ANPR engine configuration from environment variables."""

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ANPRSettings:
    anpr_enabled: bool = True
    ocr_enabled: bool = True
    plate_detector_enabled: bool = True
    vehicle_detector_enabled: bool = True
    min_plate_confidence: float = 0.25
    min_ocr_confidence: float = 0.30
    confusable_substitution: bool = False
    model_dir: str = "./models"
    # "real" | "mock" — mock keeps legacy synthetic providers
    provider_mode: str = "real"
    output_dir: str = "./output"
    # OCR crop / multi-pass
    plate_pad_ratio: float = 0.20
    ocr_upscale_2x: bool = True
    ocr_upscale_3x: bool = True
    ocr_save_debug_crops: bool = True
    ocr_debug_dir: str = "./output/ocr_debug"
    # Plate detector: opencv (default) | yolo (eval) | hybrid | ai | compare
    plate_detector: str = "opencv"
    # Local weights dir for AI plate detector (never auto-downloaded)
    ai_plate_model_dir: str = ""
    # Vehicle detector: yolo (preferred) | opencv | mock
    vehicle_detector: str = "yolo"
    # Optional explicit YOLO weights path (default: {model_dir}/vehicle/yolov8n.pt)
    yolo_vehicle_weights: str = ""
    yolo_vehicle_conf: float = 0.35
    # Plate YOLO (EVAL): {model_dir}/plate/yolov8n_plate_yasirfaizahmed_best.pt
    yolo_plate_weights: str = ""
    yolo_plate_conf: float = 0.25


@lru_cache
def get_anpr_settings() -> ANPRSettings:
    mode = os.getenv("ANPR_PROVIDER_MODE", "real").strip().lower()
    if mode not in {"real", "mock"}:
        mode = "real"
    plate_mode = os.getenv("ANPR_PLATE_DETECTOR", "opencv").strip().lower()
    if plate_mode not in {"opencv", "yolo", "hybrid", "ai", "compare"}:
        plate_mode = "opencv"
    vehicle_mode = os.getenv("ANPR_VEHICLE_DETECTOR", "yolo").strip().lower()
    if vehicle_mode not in {"yolo", "opencv", "mock"}:
        vehicle_mode = "yolo"
    return ANPRSettings(
        anpr_enabled=_bool("ANPR_ENABLED", True),
        ocr_enabled=_bool("OCR_ENABLED", True),
        plate_detector_enabled=_bool("PLATE_DETECTOR_ENABLED", True),
        vehicle_detector_enabled=_bool("VEHICLE_DETECTOR_ENABLED", True),
        min_plate_confidence=_float("ANPR_MIN_PLATE_CONFIDENCE", 0.25),
        min_ocr_confidence=_float("ANPR_MIN_OCR_CONFIDENCE", 0.30),
        confusable_substitution=_bool("ANPR_CONFUSABLE_SUBSTITUTION", False),
        model_dir=os.getenv("ANPR_MODEL_DIR", "./models"),
        provider_mode=mode,
        output_dir=os.getenv("ANPR_OUTPUT_DIR", "./output"),
        plate_pad_ratio=_float("ANPR_PLATE_PAD_RATIO", 0.20),
        ocr_upscale_2x=_bool("ANPR_OCR_UPSCALE_2X", True),
        ocr_upscale_3x=_bool("ANPR_OCR_UPSCALE_3X", True),
        ocr_save_debug_crops=_bool("ANPR_OCR_SAVE_DEBUG_CROPS", True),
        ocr_debug_dir=os.getenv("ANPR_OCR_DEBUG_DIR", "./output/ocr_debug"),
        plate_detector=plate_mode,
        ai_plate_model_dir=os.getenv("ANPR_AI_PLATE_MODEL_DIR", "").strip(),
        vehicle_detector=vehicle_mode,
        yolo_vehicle_weights=os.getenv("ANPR_YOLO_VEHICLE_WEIGHTS", "").strip(),
        yolo_vehicle_conf=_float("ANPR_YOLO_VEHICLE_CONF", 0.35),
        yolo_plate_weights=os.getenv("ANPR_YOLO_PLATE_WEIGHTS", "").strip(),
        yolo_plate_conf=_float("ANPR_YOLO_PLATE_CONF", 0.25),
    )


def clear_anpr_settings_cache() -> None:
    get_anpr_settings.cache_clear()
