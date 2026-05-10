#!/usr/bin/env python3
"""
train.py -- 训练文字验证码识别 CNN 模型
用法:
  cd /root/Toolkit/scripts/captcha_recognition
  python3 train.py            # 先生成数据再训练
  python3 train.py --skip-gen # 跳过数据生成直接训练
"""
import argparse, json, os, sys
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import transforms
from dataset import CaptchaDataset
from generate import generate_data


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


def train():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-gen", action="store_true")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    if not args.skip_gen:
        for p in [cfg["train_data_path"], cfg["test_data_path"]]:
            os.makedirs(p, exist_ok=True)
        print("[train] 生成训练数据...", flush=True)
        generate_data(cfg["train_num"], cfg["digit_num"], cfg["characters"],
                      cfg["train_data_path"], cfg["img_width"], cfg["img_height"])
        print("[train] 生成测试数据...", flush=True)
        generate_data(cfg["test_num"], cfg["digit_num"], cfg["characters"],
                      cfg["test_data_path"], cfg["img_width"], cfg["img_height"])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[train] device={device}", flush=True)

    tf = transforms.Compose([
        transforms.Resize((cfg["resize_height"], cfg["resize_width"])),
        transforms.ToTensor(),
    ])
    train_ds = CaptchaDataset(cfg["train_data_path"], tf, cfg["characters"])
    test_ds  = CaptchaDataset(cfg["test_data_path"],  tf, cfg["characters"])
    train_dl = DataLoader(train_ds, batch_size=cfg["batch_size"], shuffle=True,  num_workers=2)
    test_dl  = DataLoader(test_ds,  batch_size=cfg["batch_size"], shuffle=False, num_workers=2)

    num_classes = len(cfg["characters"])
    model = CaptchaCNN(num_classes, cfg["digit_num"]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg["learning_rate"])
    criterion = nn.CrossEntropyLoss()
    os.makedirs(cfg["model_save_path"], exist_ok=True)

    best_acc = 0.0
    for epoch in range(cfg["epoch_num"]):
        model.train()
        total_loss = 0.0
        for imgs, labels in train_dl:
            imgs, labels = imgs.to(device), labels.to(device)
            optimizer.zero_grad()
            out = model(imgs)
            loss = sum(criterion(out[:, i, :], labels[:, i]) for i in range(cfg["digit_num"]))
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        if (epoch + 1) % 10 == 0:
            model.eval()
            correct = total = 0
            with torch.no_grad():
                for imgs, labels in test_dl:
                    imgs, labels = imgs.to(device), labels.to(device)
                    out = model(imgs)
                    preds = out.argmax(dim=2)
                    correct += (preds == labels).all(dim=1).sum().item()
                    total += labels.size(0)
            acc = correct / total
            print(f"[train] epoch={epoch+1}/{cfg['epoch_num']}  loss={total_loss/len(train_dl):.4f}  acc={acc:.4f}", flush=True)
            if acc > best_acc:
                best_acc = acc
                save_path = os.path.join(cfg["model_save_path"], cfg["model_name"] + ".pt")
                torch.save(model.state_dict(), save_path)
                print(f"[train] 模型已保存 -> {save_path}  (best_acc={best_acc:.4f})", flush=True)
        elif (epoch + 1) % 5 == 0:
            print(f"[train] epoch={epoch+1}/{cfg['epoch_num']}  loss={total_loss/len(train_dl):.4f}", flush=True)

    print(f"[train] 完成  best_acc={best_acc:.4f}", flush=True)


if __name__ == "__main__":
    train()
