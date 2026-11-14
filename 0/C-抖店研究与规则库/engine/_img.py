# -*- coding: utf-8 -*-
from PIL import Image
paths = [
    r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3\images\1391\main\image_9197742a0f55.jpg",
    r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3\images\1391\main\image_3f4a9183e9a6.jpg",
    r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3\images\1391\main\image_e1ecb12c09fa.jpg",
    r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3\images\1391\detail\image_bff6f0a21b10.jpg",
]
for p in paths:
    try:
        im = Image.open(p)
        w, h = im.size
        print(f"{w}x{h} ratio={w/h:.3f} {'OK' if abs(w/h-1)<0.01 else 'NOT-1:1'}  {p.split(chr(92))[-1]}")
    except Exception as e:
        print("ERR", p, e)
