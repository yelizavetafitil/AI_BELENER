"""Конфиг Paddle/YOLO зон."""

from belener.paddle_ocr import paddle_zone_match
from belener.yolo_zones import YOLO_CLASS_NAMES


def test_paddle_zone_match():
    assert paddle_zone_match("spec_right")
    assert paddle_zone_match("spec_left")
    assert paddle_zone_match("stamp_frame")
    assert not paddle_zone_match("legend_table")
    assert not paddle_zone_match("body")


def test_yolo_class_names():
    assert len(YOLO_CLASS_NAMES) == 3
    assert "spec_table" in YOLO_CLASS_NAMES
