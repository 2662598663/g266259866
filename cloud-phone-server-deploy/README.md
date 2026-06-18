# Cloud Phone Server Deploy

这个目录是最小可上传 GitHub 的公网部署包。

## 直接部署到 Render
1. 新建一个 GitHub 仓库
2. 把这个目录全部上传到仓库根目录
3. 打开 Render
4. New + -> Web Service
5. 选择你的 GitHub 仓库
6. Render 会自动识别 `render.yaml`
7. 等待部署完成
8. 拿到公网 URL 后，填到 APK 的 `Server URL`

## 本地运行
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000

## 健康检查
/health
