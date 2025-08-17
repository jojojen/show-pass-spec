# 專案軟體架構

## 服務簡介
這個服務的核心賣點是提供隱私保護的票根處理工具，讓用戶輕鬆收集活動章戳如同護照般記錄參與歷史，透過自動OCR提取資訊並確保數據不外洩。服務客群主要針對熱愛參與音樂會、展覽或活動的用戶，例如音樂迷、藝術愛好者或旅行者，幫助他們數位化管理個人體驗。

本專案的軟體架構分為三個主要組件：用戶瀏覽器（客戶端）、Flask 後端 API（伺服器端），以及 Railway 部署環境。以下詳細描述每個組件的功能與互動流程。

## 1. 用戶瀏覽器（客戶端）

- **Vue 前端**：處理護照 UI，包括顯示收集章戳的章戳網格，以及過去事件的歷史清單。它還包含使用 Canvas 的票根遮蔽工具，用於本地處理，讓用戶可以拖曳黑塊遮蔽票圖上的敏感區域，而不發送未遮蔽的數據。
- **API 端點 /api/ocr**：上傳遮蔽後的票圖到後端，後端整合 Google Vision OCR 進行處理，並回傳四個欄位（場地 slug、演出者、活動標題、活動日期時間）。
- **API 端點 /api/visit**：提交表單數據到後端，寫入 PostgreSQL，並回傳該場地的累計訪問次數。

## 2. Flask 後端 API（伺服器端）

- **OCR 模組**：整合 Google Vision，用於上傳影像的光學字符識別。
- **輕量解析**：使用正則表達式和關鍵字字典，從 OCR 結果中提取並精煉數據。
- **資料存取**：透過 SQLAlchemy 連接 PostgreSQL，管理資料庫互動，用於儲存和檢索用戶訪問記錄。
- **靜態檔案**：提供場地章戳的 PNG 影像。

## 3. Railway 部署

- **單一容器**：託管 Flask API 和前端建置的靜態檔案（/dist）。
- **PostgreSQL 插件**：透過 DATABASE_URL 提供資料庫連接。
- **機密管理**：儲存敏感憑證，如 DATABASE_URL 和 GOOGLE_APPLICATION_CREDENTIALS（或掛載 vision.json 檔案用於 Google Vision 驗證）。

## 整體流程

流程由客戶端發起：瀏覽器處理本地遮蔽和 UI，呼叫後端 API 進行 OCR 和數據持久化，所有部署均在單一 Railway 容器中管理，以簡化操作。伺服器上不進行持久影像儲存，以確保隱私。

 
 ## venv setting
pyenv local 3.11.9
pyenv rehash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

### exec method 1
python app.py
### exec method 2
gunicorn app:app --bind 0.0.0.0:5000 --workers 2
### exec method 3
export FLASK_APP=app.py
flask run

### check at
http://127.0.0.1:5000/