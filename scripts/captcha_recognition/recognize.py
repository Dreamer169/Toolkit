#!/usr/bin/env python3
"""
recognize.py -- 验证码推理接口（供 Toolkit API 调用）
用法:
  python3 recognize.py --image /path/to/captcha.png
  python3 recognize.py --base64 <base64_image_string>

返回 JSON: {"text": "3", "confidence": 0.98}
"""
import argparse, base64, io, json, os, sys
import torch
import torch.nn as nn
from PIL import Image
from torchvision import transforms

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class CaptchaCNN(nn.Module):
    def __init__(self, num_classes, digit_num):
        super().__init__()
        self.digit_num = digit_num
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.fc = nn.Sequential(
            nn.Dropout(0.5),
            nn.Linear(128 * 16 * 16, 512),
            nn.ReLU(),
            nn.Linear(512, num_classes * digit_num),
        )

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x.view(x.size(0), self.digit_num, -1)


_model = None
_cfg   = None


def _load_model():
    global _model, _cfg
    if _model is not None:
        return _model, _cfg
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    with open(cfg_path) as f:
        _cfg = json.load(f)
    model_path = os.path.join(SCRIPT_DIR, _cfg["model_save_path"], _cfg["model_name"] + ".pt")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"模型文件不存在: {model_path} -- 请先运行 train.py")
    num_classes = len(_cfg["characters"])
    _model = CaptchaCNN(num_classes, _cfg["digit_num"])
    _model.load_state_dict(torch.load(model_path, map_location="cpu"))
    _model.eval()
    return _model, _cfg


def recognize_image(img: Image.Image) -> dict:
    model, cfg = _load_model()
    tf = transforms.Compose([
        transforms.Resize((cfg["resize_height"], cfg["resize_width"])),
        transforms.ToTensor(),
    ])
    x = tf(img.convert("L")).unsqueeze(0)
    with torch.no_grad():
        out = model(x)
    probs  = torch.softmax(out, dim=2)
    preds  = probs.argmax(dim=2)[0]
    chars  = cfg["characters"]
    text   = "".join(chars[i] for i in preds.tolist())
    confidence = float(probs[0, range(cfg["digit_num"]), preds].prod())
    return {"text": text, "confidence": round(confidence, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image",  default="")
    ap.add_argument("--base64", default="")
    args = ap.parse_args()

    try:
        if args.base64:
            raw = base64.b64decode(args.base64)
            img = Image.open(io.BytesIO(raw))
        elif args.image:
            img = Image.open(args.image)
        else:
            ap.print_help()
            sys.exit(1)
        result = recognize_image(img)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
