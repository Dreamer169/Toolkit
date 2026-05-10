# captcha_recognition — CNN 数字图片验证码识别

基于 PyTorch CNN 训练的验证码识别服务，经 ~300 epoch 训练准确率达 **96.3%**。

## 服务信息

- **端口**：8765
- **PM2 服务名**：captcha-api
- **模型路径**：`./model/captcha_recognition.pt`
- **对外路由**：`/api/tools/captcha/...`（经 api-server 代理）

## API 接口

### GET /health
```json
{"ok": true, "model_ready": true}
```

### POST /recognize
识别 base64 编码的验证码图片，返回识别结果。
```bash
curl -X POST http://localhost:8765/recognize \
  -H 'Content-Type: application/json' \
  -d '{"base64": "<base64_png>"}'
# 返回: {"result": "7", "confidence": 0.99}
```

### POST /train/start
触发重新训练（一般由 Replit CNN Training workflow 代劳）。

## 在注册脚本中使用

替代 ddddocr 本地识别，精度更高：
```python
import urllib.request, json, base64

def solve_captcha_api(img_bytes: bytes) -> str:
    b64 = base64.b64encode(img_bytes).decode()
    body = json.dumps({'base64': b64}).encode()
    req = urllib.request.Request(
        'http://localhost:8765/recognize',
        data=body, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())['result']
```

> 当前 `novproxy_register_final.py` 使用 ddddocr 本地识别，
> 可将 `solve_captcha_b64()` 替换为以上调用。

## 模型更新

模型在 Replit 上训练后通过 SCP 自动部署：
```bash
# 在 Replit（CNN Training workflow）：
python3 train.py --skip-gen --resume --start-epoch <N> --best-acc <acc>
python3 deploy_model.py
```

## 训练历史

| 轮次 | Epoch | 准确率 |
|------|-------|--------|
| 第一轮 | 0–100 | 94.9% |
| 第二轮 | 101–200 | 95.7% |
| 第三轮 | 201–300 | **96.3%** |
