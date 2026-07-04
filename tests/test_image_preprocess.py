import numpy as np

from belener.image_preprocess import _segment_xyxy


def test_segment_xyxy_nested():
    seg = np.array([[[10, 20, 30, 40]]])
    assert _segment_xyxy(seg) == (10, 20, 30, 40)


def test_segment_xyxy_flat():
    seg = np.array([10, 20, 30, 40], dtype=np.int32)
    assert _segment_xyxy(seg) == (10, 20, 30, 40)


def test_segment_xyxy_row():
    seg = np.array([[10, 20, 30, 40]])
    assert _segment_xyxy(seg) == (10, 20, 30, 40)
