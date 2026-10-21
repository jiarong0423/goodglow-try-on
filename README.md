# 好氣色試妝（Good Glow Try-On）

YouCam API Skin AI & eCommerce VTO 黑客松作品，**eCommerce VTO 賽道**（截止頁面顯示 2026-11-03 00:45，時區未確認）。
English: [README.en.md](README.en.md)。

為**白化症、白斑症**設計的試妝 PWA，同一個 App 兩個入口：拍一張像照鏡子的自拍 → 自動取膚色（白斑症另取白斑）→
**先對到自己的膚色色卡，再依色卡深淺與冷暖排出專櫃色號** → 整臉試妝（底妝、眉、睫毛、腮紅、唇、修容）→ 存成配色卡帶去專櫃。
**只加妝（YouCam 磨皮固定 0；其中一個定案另有標明的本機柔焦）；保留原樣永遠是第一個選項；妝感分「理容／自然／精緻」，不問性別；紅唇只在「精緻」。**
大字、暗色模式；自動拍照用 YouCam Camera Kit，即時鏡頭預覽需要 HTTPS、尚未實測；短邊不足 1080 px 的照片會被退回。光太亮時可以改成暗色畫面。

設計依據、研究筆記與實測紀錄是內部資料，不在公開 repo。

## 流程

| 步驟 | 白化症 | 白斑症 |
|---|---|---|
| 取色 | 臉部定位自動取受光側臉頰、眉色 | 臉部定位自動分「原本膚色／白斑」兩群、取眉色；都能手動點選修正 |
| 膚色色卡 | 深淺 10 級 × 冷暖 3 種；極淺膚色彩度太低時冷暖判中性、請本人確認 | 原本膚色、白斑交界色各對一格 |
| 專櫃色號 | 底妝：依色卡排序的色號；同深淺沒有時給一淺一深 1:1 混合 | 底妝、遮瑕打底、修邊色、（偏粉時）修色＋遮瑕步驟（點壓不搓、照光日不上妝） |
| 試妝（Makeup VTO） | 眉（照原眉色補一點／略深加立體）、睫毛、腮紅、唇 | 整臉底妝、眉、唇、睫毛、修容／打亮（試妝做不到只在白斑上補色，所以只在淺色區塊上的遮瑕由本機程式模擬並標「模擬」，另附色號與步驟說明） |
| 配色卡 | 先保養、再保護、最後才上妝的步驟＋各項色號，給櫃姐看、存圖 | 同左（白斑版步驟） |

## 為什麼先對膚色色卡

照片裡的皮膚受光線、相機影響，比產品色票灰、偏粉（在一張實測照片上，膚色彩度 19、色相 43°；同亮度的產品色票彩度 33–49、
色相 58–70°），直接比色差會全部「沒有接近色」。所以兩邊各自分級：照片膚色看亮度與色相；專櫃色號看色票亮度與
**色號本身的冷暖標示**（色號字母或官網說明裡的冷／暖／中性），再依色卡深淺與冷暖、色差排序（是軟排序，不是只限同一格）。程式：`app/skin_card.py`。

## 證據頁：一套妝是怎麼一層一層做出來的

[`evidence/`](evidence/README.md)（只放在 GitHub，App 裡不連過去、Docker 映像也不含）：四位示範對象各一條漸進式滑桿（本機逐層 11、4、17 層與階段圖，加上 YouCam 試妝步驟），可切換「這一層改了哪裡」熱圖，每步附做法、用到的技術與交叉驗證；每張圖標「模擬」。資料 `evidence/process.json` 由 `tools/export_process_demo.py` 從本機 runs 匯出（runs 不進版控）。

## 色號資料

公開版只附**虛構示範型錄**（`app/catalog.json`：品牌與色號全部虛構、色碼是示意值）。
程式可匯入品牌提供的色號資料（每列：品牌、產品、類別、色號、色碼、說明），放在 `app/partner_shades_official.json` 就會優先使用；
開發時用來驗證流程的真實品牌資料不放進公開 repo（比賽規則不得含未經許可的第三方商標）。色碼是網頁顯示色，不等於實際產品顏色，畫面一律提醒以專櫃自然光試色為準。

## 隱私

- 照片只在記憶體內處理（轉正方向、縮圖、重新編碼去掉 EXIF），試妝後就丟；結果圖直接回瀏覽器，伺服器不留。
- 臉部定位（MediaPipe Face Landmarker，從 jsDelivr／Google 載入模型；拍照用 YouCam Camera Kit，從 YouCam CDN 載入）在手機瀏覽器裡跑，這一步照片不上傳；載入失敗就改手動點選。
- 淺色區塊遮罩由伺服器計算（照片只放記憶體、不存檔）；真實模式下試妝會把照片送到 YouCam。
- 膚色色卡、色號比對都在本機算，不扣點、不外送。
- 妝感、冷暖調、光線指紋（只有亮度與色溫數字）存在手機瀏覽器。
- API key 只在伺服器端讀，不會進瀏覽器。`photos/`、`runs/`、`.env` 都不進版控。

## 結構

| 路徑 | 用途 |
|---|---|
| `web/` | PWA：開始（入口、妝感、冷暖調）、取景（Camera Kit＋光線指紋＋拒收低解析度）、確認照片（自動取色 `face_auto.js`）、白斑膚色分析、快速三選、逐項微調、配色卡 |
| `app/server.py` | 本機伺服器：提供 `web/`，代理 YouCam Makeup VTO；`--mock` 不扣點；`--budget-cap` 點數總預算 |
| `app/skin_card.py` | 膚色色卡：照片膚色 → 色卡格子 → 依色卡與色差排序的色號、兩色混合 |
| `app/color_engine.py` | Lab／CIEDE2000、型錄載入、白化症底妝與白斑兩色方案、眉色依原眉色、保養保護步驟 |
| `app/recommend.py` | 膚色匹配 → 整套推薦（色卡、底妝、兩款妝、步驟） |
| `app/face_rules.py` | 依五官（YouCam Face Attribute）給腮紅、補眉、睫毛、唇妝建議；只建議不自動改 |
| `app/presets.json`、`app/looks.py` | 妝感、冷暖調、各項選項；依「妝感＋選項 id」組 Makeup VTO 參數並檢查允許範圍 |
| `app/budget.py` | 點數帳本：先預留、YouCam 回成功才記帳，跨次啟動累計 |
| `tools/` | YouCam 客戶端與探測工具、翻譯表建置、重現檢查、示範資料匯出（用法見 `docs/TOOLS_AND_USAGE.md`） |
| `tests/` | 離線測試（假 YouCam，不連網、不扣點） |

## API key

二選一，不要貼到對話或程式碼裡：環境變數 `YOUCAM_API_KEY`，或專案根目錄 `.env` 寫一行 `YOUCAM_API_KEY=...`（只放 API Key，不放 Secret）。

## 執行

展示模式（不扣點；示範照片顯示預先存好的真實 YouCam 結果，選項剛好有錄過（共 80 組）就是那一組的結果，否則用最接近的一張，畫面會註明；自己的照片會回原圖並標示展示模式）：

```bash
python3 -m pip install -r requirements.txt
python3 -B app/server.py --mock
```

正式模式（每次試妝 1 點；本次啟動最多 20 次；帳本總預算 60 點）：

```bash
python3 -B app/server.py --max-calls 20 --budget-cap 60
```

打開 http://127.0.0.1:8787 。

**用手機錄示範影片**（不需要公開部署）：電腦和手機連同一個 Wi-Fi，伺服器改聽區網：

```bash
python3 -B app/server.py --mock --host 0.0.0.0
```

手機開 `http://<電腦的區網 IP>:8787`（英文介面加 `?lang=en`）。錄影片、截圖一律加 `--catalog demo`：只顯示虛構品牌的示範型錄，畫面不出現任何第三方品牌名稱或品牌色號。`--brand-alias`（需要本機品牌資料與 `app/brand_alias.json`）只給自己在本機檢查用，不用於對外素材。沒有 HTTPS 時手機瀏覽器不能開相機預覽，**按「相簿」選照片**即可走完整個流程；
「先用示範照片看看」會重播 `demo/` 裡錄好的真實試妝結果。

## 示範素材

`demo/`：Pexels 公開照片與用它們實跑的 YouCam 結果，展示模式重播用；`demo/photos/standard4/` 是四人標準組（白化症男女、白斑症男女）；來源與授權見 `demo/SOURCES.md`。
`photos/`、`runs/` 是實測用的本機資料，不進版控。

## 點數

官方價目（`GET /s2s/v2.0/credit/feature-cost` 實查）：Makeup VTO 1 點、Face Attribute 1–5 項 10 點、Skin Analysis HD 1–4 項 12 點、Facial Color Tones 20 點。
一次完整體驗約 2（快速三選）＋每次微調 1（Makeup VTO；「逐層看」每層各打一次，最多 7 點）；「依五官給建議」是本人按了才跑的 Face Attribute（5 項＝10 點）；取色、色卡、色號比對都不扣點。查餘額：

```bash
python3 -I -B tools/noise_test.py credit
```

## 測試

```bash
python3 -B -m unittest discover -s tests -v
```

## 授權

程式碼：MIT（見 `LICENSE`）。`demo/` 的照片不適用 MIT，依各自來源授權（見 `demo/SOURCES.md`）。本專案使用 YouCam API（Perfect Corp.），需自備 API key。
