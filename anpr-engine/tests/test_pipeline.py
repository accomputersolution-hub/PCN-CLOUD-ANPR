from pcn_anpr.pipeline import ANPRPipeline


def test_pipeline_returns_ocr() -> None:
    result = ANPRPipeline().process(frame=None)
    assert result.ocr is not None
    assert result.ocr.confidence >= 0.9
    assert result.vehicle is not None
    assert result.plate is not None
