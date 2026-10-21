'use strict';
// 示範頁：一套妝是怎麼一層一層做出來的（漸進式，每一步都是累積圖，全部標「模擬」）。
// 證據頁（GitHub，不在 App 裡）。資料：process.json（tools/export_process_demo.py 由本機逐層引擎的累積圖匯出）。中英文都在 JSON／本檔，語言跟 i18n.js 一致。
// 所有文字用 textContent 放進畫面（不經 innerHTML）。
(function () {
  const L = () => (window.I18N && window.I18N.lang() === 'en' ? 'en' : 'zh');
  const $ = (id) => document.getElementById(id);

  const UI = {
    zh: {
      logo: '好氣色試妝', back: '回到專案說明', title: '一套妝是怎麼一層一層做出來的',
      lead: '拖動滑桿或按「播放」，一層一層看：每一步都疊在上一步的結果上，底下寫這一層做了什麼、用什麼技術。',
      sim: '以下所有圖片都是模擬，不是實際上妝照片',
      pills: ['先看起點：永遠從你的照片開始', '離線逐層引擎＋YouCam 試妝', '每一層都量色差'],
      tiersH: '哪些能在線上試、哪些是預先算好的',
      prev: '上一步', next: '下一步', play: '播放', pause: '暫停', diffOn: '看這一層改了哪裡', diffOff: '回到完整圖',
      start: '起點', step: (n, t) => `第 ${n} 步，共 ${t} 步`, startCount: (t) => `起點，之後共 ${t} 步`,
      local: '離線逐層（預先算好）', youcam: 'YouCam 試妝', final: '定案', badge: '模擬', startTag: '起點',
      pLocal: (n) => (n == null ? '離線逐層（層數未留存）' : `${n} 層離線逐層`), pYoucam: (n) => `${n} 步 YouCam 試妝`,
      metricShift: (v) => `這一層讓它所在範圍的平均顏色改變 ΔE ${v}`, metricPatch: (a, b) => `淺色區塊與目標的色差：ΔE ${a} → ${b}`,
      assistH: 'YouCam 做不到的，我們怎麼補',
      techH: '用到的技術', crossH: '交叉驗證：怎麼確認沒做歪', credit: '照片來源：', open: '回到專案說明，照步驟自己試試',
      foot: '這一頁只說明方法，App 裡「保留原樣」永遠是第一個選項。色差 ΔE 越小，兩個顏色越接近。',
      thumbStart: '起點',
    },
    en: {
      logo: 'Good Glow Try-On', back: 'Back to the project page', title: 'How a look is built, layer by layer',
      lead: 'Drag the slider or press Play. Every step is laid on top of the previous one, and the text under the image says what that layer does and which technique it uses.',
      sim: 'Every image on this page is simulated, not a photo of actual makeup',
      pills: ['Starting point first: always your own photo', 'Offline layer engine + YouCam try-on', 'Color difference measured at every step'],
      tiersH: 'What you can try online, and what is pre-computed',
      prev: 'Previous', next: 'Next', play: 'Play', pause: 'Pause', diffOn: 'Show what this layer changed', diffOff: 'Back to the full image',
      start: 'Start', step: (n, t) => `Step ${n} of ${t}`, startCount: (t) => `Starting point, then ${t} steps`,
      local: 'Offline layers (pre-computed)', youcam: 'YouCam try-on', final: 'Final look', badge: 'Simulated', startTag: 'Start',
      pLocal: (n) => (n == null ? 'offline layers (count not kept)' : `${n} offline layers`), pYoucam: (n) => `${n} YouCam step${n === 1 ? '' : 's'}`,
      metricShift: (v) => `This layer moves the average color of its area by ΔE ${v}`, metricPatch: (a, b) => `Color difference between the lighter area and the target: ΔE ${a} → ${b}`,
      assistH: 'What YouCam cannot do alone, and how we fill the gap',
      techH: 'Technology we use', crossH: 'Cross-checks: how we know it did not drift', credit: 'Photo source: ', open: 'Back to the project page and try it yourself',
      foot: 'This page only explains the method. In the app, "Keep as is" is always the first option. A smaller color difference ΔE means two colors are closer.',
      thumbStart: 'Start',
    },
  };

  const TECH = {
    zh: [
      ['手機上：臉部定位', '用 MediaPipe 臉部地標（478 點）在瀏覽器裡找出臉頰、額頭、眉毛、嘴唇的位置，照片不上傳。'],
      ['色彩：Lab 與 CIEDE2000', '顏色從 sRGB 轉到 CIE Lab（D65）再算 CIEDE2000 色差；膚色先對到 10 級 × 冷暖的膚色色卡，再依色卡深淺與冷暖、再依色差排出色號。'],
      ['本機逐層引擎', '每一層是一個遮罩加上限的混色（每層有不透明度上限，多數在 0.22–0.75，遮罩有漸層或分區），一次只做一個部位；細紋理從原圖加回去，量到保留約 87–99%（依示範而不同），不是 100%。這些圖是離線預先算好的。'],
      ['淺色區塊遮罩', '用「彩度 ÷ 亮度」在多個尺度上找出比周圍淺的區塊，再用連通色差長出遮罩邊界（不畫直線）；上完妝再偵測一次，只補殘留。'],
      ['YouCam Makeup VTO', '整臉試妝（底妝、眉、睫毛、腮紅、唇、修容）由 YouCam Makeup VTO 負責，YouCam 的磨皮固定 0；Face Attribute 只在使用者按了才跑，給臉型與眉形提醒。'],
      ['隱私', '照片只在記憶體處理，不寫入磁碟；結果直接回到瀏覽器；稽核只留雜湊。'],
    ],
    en: [
      ['On the phone: face landmarks', 'MediaPipe face landmarks (478 points) run in the browser and locate the cheeks, forehead, brows and lips. The photo is not uploaded for this.'],
      ['Color: Lab and CIEDE2000', 'Colors go from sRGB to CIE Lab (D65), and differences are measured with CIEDE2000. Skin is matched to a 10-level × cool/warm skin tone card first, then shades are ranked by card level and undertone, then by color difference.'],
      ['Local layer engine', 'Each layer is a masked color blend with its own opacity cap (mostly 0.22–0.75) and a gradient or zone mask, one area at a time. Fine texture is added back from the original; measured texture kept is about 87–99% depending on the example, not 100%. These images are pre-computed offline.'],
      ['Lighter-area masks', 'Chroma ÷ lightness finds areas lighter than their surroundings at several scales, and the mask edge grows by connected color difference (no straight lines). After each pass it detects again and only fills what is left.'],
      ['YouCam Makeup VTO', 'The whole-face try-on (base, brows, lashes, blush, lips, contour) comes from YouCam Makeup VTO, with YouCam smoothing fixed at 0. Face Attribute runs only when the user taps it, for face-shape and brow tips.'],
      ['Privacy', 'Photos are handled in memory and never written to disk; results go straight back to the browser; audits keep hashes only.'],
    ],
  };
  const TIERS = {
    zh: [
      ['第一層：線上就能試（約 30 秒）', '用示範照或你自己的照片：膚色對到色卡與色號、手機上即時的底妝填色預覽、淺色區塊的局部遮瑕模擬；YouCam 試妝在展示模式下顯示預先存好的真實結果（選項剛好有錄過就是那一組的結果，否則用最接近的一張）。顏色計算在瀏覽器，色號排序與淺色區塊遮罩在示範伺服器（照片只放記憶體、不存檔）；不用登入、不扣點。'],
      ['第二層：看過程與證據（約 3 分鐘）', '就是這一頁：四組示範的逐層疊加過程、每一層改了哪裡的熱圖，以及可以自己重跑的對照（demo/repro）。'],
      ['第三層：離線逐層引擎（無法在線上即時跑）', '下面逐層圖是離線引擎預先算好的：一張約 2 分鐘、需要額外的影像套件，所以不放在公開的線上版。結果附 sha256 清單（demo/final/SHA256SUMS.txt），可以核對。你的照片在線上只會走第一層的輕量版。'],
    ],
    en: [
      ['Tier 1: try it online (about 30 seconds)', 'With a demo photo or your own: skin tone matched to the card and shades, a live foundation fill preview on your phone, and a simulated local concealing of lighter patches (the patch mask is computed by the app server, in memory and not stored). In demo mode YouCam try-on shows saved results of real calls (the one recorded for your exact choices when it exists, otherwise the closest). No login, no credits.'],
      ['Tier 2: see the process and the evidence (about 3 minutes)', 'This page (it lives on GitHub, not in the app): the layer-by-layer build of four demos, a heat map of what each layer changed, and a comparison you can rerun yourself (demo/repro).'],
      ['Tier 3: the offline layer engine (cannot run live online)', 'The layer images below were pre-computed by an offline engine: about 2 minutes per photo and extra image libraries, so it is not part of the public online version. Results come with a sha256 manifest (demo/final/SHA256SUMS.txt) so they can be checked. Your own photo online only goes through the lighter Tier 1.'],
    ],
  };
  const ASSIST = {
    zh: [
      ['分工', '線上 App 裡：YouCam Makeup VTO 負責整臉試妝（底妝、眉、睫毛、腮紅、唇、修容），YouCam 的磨皮固定 0；它沒有區域參數，只在一塊淺色區塊上遮瑕這一步由我們的本機程式負責。下面這些示範的做法不同：離線逐層引擎畫了大部分的層，YouCam 只加一兩個官方步驟；其中一個定案（膚色很淺 B）另有一道標明的本機柔焦。畫面上每一步都標明是哪一邊。'],
      ['為什麼要補', 'Makeup VTO 沒有區域參數，不能只塗在某一塊皮膚上。同一張示範照、同一個量法（淺色區塊與周圍膚色的亮度差）：YouCam 全臉粉底約縮小 5–6%，我們的逐層填色約縮小 42%（離線引擎的結果）。這是我們設計的填色，所以是確認它做到，不是獨立盲測。'],
      ['可以自己重現', '照片、YouCam 回傳圖、本機結果與遮罩都在 demo/repro/，執行 python3 -B tools/repro_region_gap.py 就會印出同一張表。'],
      ['YouCam 處理不了時', '角度太斜、臉太小、眉毛太淡等情況，YouCam 會回錯誤；App 不中斷，改顯示本機模擬（只有底妝與局部遮瑕），並說明原因與來源。可用 app/server.py --mock --simulate-youcam-error 重現。'],
    ],
    en: [
      ['Division of work', 'In the live app, YouCam Makeup VTO does the whole-face try-on (base, brows, lashes, blush, lips, contour), with YouCam smoothing fixed at 0; it has no region parameter, so concealing only on a patch is done by our local code. The demos below were built differently: our offline layer engine painted most layers and YouCam added one or two official steps; one final (very light skin B) also has a labelled local soft-focus pass. Every step on screen says which side did it.'],
      ['Why we fill the gap', 'Makeup VTO has no region parameter, so it cannot paint only one area of skin. On the same demo photo with the same measure (lightness gap between a lighter patch and the skin around it), YouCam whole-face foundation shrinks the gap by about 5–6%, and our layered fill by about 42% (the offline engine result). The fill was designed to do this, so this confirms it works; it is not a blind test.'],
      ['Reproduce it yourself', 'The photo, YouCam outputs, local result and mask are in demo/repro/. Run python3 -B tools/repro_region_gap.py to print the same table.'],
      ['When YouCam cannot process a step', 'If the angle is too tilted, the face too small or the brows too faint, YouCam returns an error. The app keeps going and shows a local simulation (foundation and local concealing only), with the reason and the source. Reproduce with app/server.py --mock --simulate-youcam-error.'],
    ],
  };
  const CROSS = {
    zh: [
      ['兩個臉部模型交叉比對', '淺色區塊編輯時，臉部位置用兩個獨立模型（MediaPipe 與 YuNet）交叉比對；兩者差太多時畫面會提醒，上妝範圍可能偏掉，建議換一張正面照。這只在該編輯畫面出現，是提醒、不是閘門。'],
      ['每一層量色差', '每一層做完都量「上一層 → 這一層」的色差。我們另外為每個階段訂了驗收目標（例如遮罩內色差、紋理保留比例），由另一個 AI 代理獨立重算；有些部位在重算後還沒達標，記錄裡照實寫，不修飾。'],
      ['官方分析離線對照', 'YouCam Skin Analysis 與 Skin Tone Analysis 在開發階段用來和本機結果交叉核對，不放在使用流程裡。'],
      ['獨立重算', '每個階段由另一個 AI 代理重算數字與檔案雜湊，數字對不上就退回重做。'],
      ['誠實標示', '每張圖標「模擬」；沒有保留的中間圖就明講；只放有紀錄的步驟，各步的數字與說明已對照紀錄檢查過，仍可能有出入，歡迎指正。'],
    ],
    en: [
      ['Two face models cross-check each other', 'When the lighter-area editor opens, face positions are compared between two independent models (MediaPipe and YuNet); this check exists only in that editor and is a warning, not a gate. If they disagree, the screen warns that the makeup area may be off and suggests a different front-facing photo.'],
      ['Color difference at every layer', 'After each layer we measure the color change from the previous layer. We also set acceptance targets per stage (for example color difference inside the mask and the share of texture kept), and another AI agent recounts them independently. Some areas were still above target after the recount, and the records say so plainly.'],
      ['Official analysis, offline', 'YouCam Skin Analysis and Skin Tone Analysis were used during development to cross-check our local numbers; they are not part of the user flow.'],
      ['Independent recount', 'Another AI agent recomputes the numbers and file hashes after every stage; if they do not match, the stage is redone.'],
      ['Honest labelling', 'Every image says Simulated; intermediate images that were not kept are stated as such; only steps with a record are shown, and their numbers were checked against the records, though mistakes may remain.'],
    ],
  };

  let data = null, cur = 0, idx = 0, showDiff = false, timer = null, front = 'a';

  const ui = () => UI[L()];
  const pick = (o, k) => o[k + '_' + L()] || o[k + '_zh'] || '';

  function setText(id, t) { $(id).textContent = t; }

  function cards(boxId, rows) {
    const box = $(boxId);
    box.textContent = '';
    for (const [h, p] of rows) {
      const c = document.createElement('div'); c.className = 'pr-card';
      const b = document.createElement('b'); b.textContent = h;
      const q = document.createElement('p'); q.textContent = p;
      c.append(b, q); box.append(c);
    }
  }

  function renderStatic() {
    const u = ui();
    document.title = u.title + ' · ' + u.logo;
    setText('pr-logo', u.logo); setText('pr-title', u.title); setText('pr-lead', u.lead); setText('pr-sim', u.sim);
    setText('pr-badge', u.badge); setText('pr-tech-h', u.techH); setText('pr-cross-h', u.crossH);
    setText('pr-foot', u.foot); setText('pr-open', u.open); setText('pr-assist-h', u.assistH); setText('pr-tiers-h', u.tiersH);
    const pills = $('pr-pills'); pills.textContent = '';
    u.pills.forEach((t) => { const e = document.createElement('span'); e.className = 'pr-pill'; e.textContent = t; pills.append(e); });
    $('pr-back').setAttribute('aria-label', u.back);
    $('pr-prev').textContent = u.prev; $('pr-next').textContent = u.next;
    cards('pr-tiers', TIERS[L()]); cards('pr-assist-cards', ASSIST[L()]); cards('pr-tech-cards', TECH[L()]); cards('pr-cross-cards', CROSS[L()]);
  }

  function renderTabs() {
    const box = $('pr-tabs');
    box.textContent = '';
    data.subjects.forEach((s, i) => {
      const b = document.createElement('button');
      b.className = 'chip'; b.type = 'button'; b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(i === cur));
      b.textContent = pick(s, 'title');
      b.onclick = () => { stop(); cur = i; idx = 0; renderSubject(); };
      box.append(b);
    });
  }

  function renderSubject() {
    const s = data.subjects[cur], u = ui();
    renderTabs();
    const summary = $('pr-summary'); summary.textContent = '';
    const pill = (cls, t) => { const e = document.createElement('span'); e.className = 'pr-pill ' + cls; e.textContent = t; summary.append(e); };
    pill('local', u.pLocal(s.local_layers)); pill('youcam', u.pYoucam(s.youcam_steps));
    const extra = pick(s, 'extra'); if (extra) pill('', extra);
    const thumbs = $('pr-thumbs'); thumbs.textContent = '';
    s.steps.forEach((st, i) => {
      const b = document.createElement('button');
      b.className = 'pr-thumb ' + st.kind; b.type = 'button'; b.setAttribute('role', 'option');
      const im = document.createElement('img'); im.src = st.img; im.alt = ''; im.loading = 'lazy';
      const sp = document.createElement('span'); sp.textContent = i === 0 ? u.thumbStart : String(i);
      b.append(im, sp); b.onclick = () => { stop(); go(i); };
      thumbs.append(b);
    });
    const r = $('pr-range'); r.max = String(s.steps.length - 1); r.value = String(idx);
    setText('pr-note', pick(s, 'note'));
    setText('pr-credit', u.credit + (pick(s, 'credit') || s.credit));
    go(idx, true);
  }

  function metricText(st) {
    const u = ui();
    if (st.delta == null) return '';
    if (Array.isArray(st.delta)) return '';        //  裁示 4a-A：不顯示「色差 a → b」（像改善數字）；只留單層顏色改變量
    return u.metricShift(Number(st.delta).toFixed(2));
  }

  function go(i, instant) {
    const s = data.subjects[cur], u = ui();
    idx = Math.max(0, Math.min(s.steps.length - 1, i));
    const st = s.steps[idx];
    const src = showDiff && st.diff ? st.diff : st.img;
    const a = $('pr-img-a'), b = $('pr-img-b');
    const next = front === 'a' ? b : a, prev = front === 'a' ? a : b;
    next.src = src;
    const swap = () => { next.classList.add('on'); prev.classList.remove('on'); front = front === 'a' ? 'b' : 'a'; };
    if (instant) { prev.src = src; next.classList.remove('on'); prev.classList.add('on'); } else if (next.complete) swap(); else next.onload = swap;   // 交叉淡入
    const total = s.steps.length - 1;
    setText('pr-count', idx === 0 ? u.startCount(total) : u.step(idx, total));
    setText('pr-step', L() === 'en' ? st.en : st.zh);
    setText('pr-tech', L() === 'en' ? st.tech_en : st.tech_zh);
    setText('pr-metric', metricText(st));
    const tag = $('pr-tag');
    const kind = st.kind;
    tag.className = 'pr-tag ' + kind;
    tag.textContent = kind === 'local' ? u.local : kind === 'youcam' ? u.youcam : kind === 'final' ? u.final : u.startTag;
    $('pr-range').value = String(idx);
    $('pr-prev').disabled = idx === 0; $('pr-next').disabled = idx === total;
    [...$('pr-thumbs').children].forEach((el, k) => el.setAttribute('aria-selected', String(k === idx)));
    const sel = $('pr-thumbs').children[idx]; if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: 'nearest', inline: 'center', behavior: instant ? 'auto' : 'smooth' });
    const d = $('pr-diff'); d.hidden = !st.diff; d.setAttribute('aria-pressed', String(showDiff));
    d.textContent = showDiff ? u.diffOff : u.diffOn;
  }

  function play() {
    const s = data.subjects[cur];
    if (timer) return stop();
    if (idx >= s.steps.length - 1) go(0);
    $('pr-play').textContent = ui().pause;
    timer = setInterval(() => {
      if (idx >= s.steps.length - 1) return stop();
      go(idx + 1);
    }, 1400);
  }
  function stop() { if (timer) clearInterval(timer); timer = null; $('pr-play').textContent = ui().play; }

  async function init() {
    try {
      const r = await fetch('process.json', { cache: 'no-cache' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      data = await r.json();
    } catch (e) {
      document.getElementById('pr-lead').textContent = 'ERROR process.json: ' + e.message;
      return;
    }
    renderStatic();
    $('pr-prev').onclick = () => { stop(); go(idx - 1); };
    $('pr-next').onclick = () => { stop(); go(idx + 1); };
    $('pr-play').onclick = play;
    $('pr-range').oninput = (e) => { stop(); go(Number(e.target.value)); };
    $('pr-diff').onclick = () => { showDiff = !showDiff; go(idx, true); };
    const q = new URLSearchParams(location.search).get('set');
    if (q) { const k = data.subjects.findIndex((s) => s.id === q); if (k >= 0) cur = k; }
    $('pr-play').textContent = ui().play;
    renderSubject();
  }
  init();
})();
