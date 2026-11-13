"use strict";

/**
 * TFT Comp Analyzer — web arayüzü (SPEC-WEB.md).
 *
 * Tek dosya (SPEC-WEB.md section 2 — bölünmüyor). Yerleşim section 5:
 * header/logo, sol "önerilen comp'lar",
 * orta board + aktif trait paneli, sağ sonuç paneli, altta yatay unit
 * paleti. Etkileşim: section 5/6 (board + DnD + tıklama), section 7.1
 * (aktif trait), section 7.3 (comp eşleştirme), section 8 (sonuç paneli,
 * section 8.2'nin sadeleştirilmiş dili ile).
 */

const DATA_FILES = {
  meta: "data/meta.json",
  units: "data/units.json",
  traits: "data/traits.json",
  comps: "data/comps.json",
  matchups: "data/matchups.json",
  compTraits: "data/comp_traits.json",
  items: "data/items.json",
  selftest: "data/_selftest.json",
};

const BOARD_SIZE = 10;
const DEFAULT_STAR = 2; // SPEC-WEB.md section 5.2 — veride en sık görülen durum
const TOP_K = 5; // src/cli.py ile aynı sabit
const MIN_UNITS_FOR_MATCH = 4; // SPEC-WEB.md section 7.3 / src/cli.py MIN_UNITS
const SUGGESTION_COUNT = 8; // section 5.3 — spec'in verdiği "5-10" aralığından seçilen sabit

// Spec bu ikisi için sayısal bir sınır vermiyor (section 8, 8.1) — kendi
// seçtiğimiz, açıkça yorumlanmış eşikler (bkz. SPEC-WEB.md section 8/8.1):
const WEAK_MATCH_RATIO = 0.9; // mesafe eşiğin %90'ından fazlaysa "zayıf eşleşme"
const WIDE_CI_WIDTH = 0.15; // CI genişliği (üst-alt) 15 puanı aşarsa "düşük güven"

const state = {};
let board = {}; // { unitId: { star: 1|2|3 } }
let boardOrder = []; // slot sırasını korumak için unitId listesi, en fazla BOARD_SIZE
let activeCostTab = "1"; // section 5.4 — varsayılan açık sekme: 1 maliyet

// src/build_standalone.py'nin ürettiği tek-dosya sürümünde mi çalışıyoruz —
// loadData() en az bir DATA_FILES girdisini gömülü olarak bulunca true olur.
// Ayrı bir bayrak/parametre taşımak yerine "gerçekten embedded veri kullanıldı
// mı" sorusunun kendisi bunun tanımı (section 9'un kısıtlar kutusundaki
// ikon/internet notu bu bayrağa bakıyor, renderConstraints()).
let dataWasEmbedded = false;

// ---------------------------------------------------------------------------
// Veri yükleme
// ---------------------------------------------------------------------------

/** build_standalone.py, her DATA_FILES girdisini `<script type="application/
 * json" id="embedded-data-<key>">` olarak HTML'e gömüyor — burada varsa
 * onu kullan, yoksa (normal sunucu modu) hiçbir şey değişmez. */
function readEmbeddedData(key) {
  const el = document.getElementById(`embedded-data-${key}`);
  return el ? JSON.parse(el.textContent) : null;
}

async function loadData() {
  const entries = Object.entries(DATA_FILES);
  const results = await Promise.all(
    entries.map(async ([key, path]) => {
      const embedded = readEmbeddedData(key);
      if (embedded !== null) {
        dataWasEmbedded = true;
        return embedded;
      }
      const res = await fetch(path);
      if (!res.ok) {
        throw new Error(`${path} yüklenemedi (HTTP ${res.status})`);
      }
      return res.json();
    })
  );
  entries.forEach(([key], i) => {
    state[key] = results[i];
  });
}

function showFetchError(err) {
  const banner = document.getElementById("fetch-error-banner");
  banner.innerHTML =
    `<strong>Veri yüklenemedi:</strong> ${err.message}<br>` +
    "Sayfayı <code>file://</code> ile (index.html'e çift tıklayarak) açtıysanız bu beklenen bir durum — " +
    "tarayıcı JSON dosyalarını CORS nedeniyle yükleyemiyor. Bir terminalde <code>cd web && python -m http.server 8000</code> " +
    "çalıştırıp <code>http://localhost:8000</code> adresini açın.";
  banner.hidden = false;
}

function showSelfTestError(failures) {
  const banner = document.getElementById("selftest-banner");
  const items = failures.map((f) => `<li><code>${f.case}</code>: ${f.reason}</li>`).join("");
  banner.innerHTML =
    "<strong>Self-test başarısız — arayüz durduruldu.</strong> " +
    "Board vektörü pipeline'ınkiyle tutmuyor demek, üstüne bir şey inşa etmenin anlamı yok " +
    "(SPEC-WEB.md section 7.2). Konsola bakın; başarısız case'ler:" +
    `<ul>${items}</ul>`;
  banner.hidden = false;
}

// ---------------------------------------------------------------------------
// İstemci tarafı hesaplamalar (SPEC-WEB.md section 7) — SELF-TEST BUNLARA
// BAĞLI, davranışlarını değiştirme.
// ---------------------------------------------------------------------------

/** Board'daki her unit'in trait'lerini benzersiz say (yıldız seviyesi
 * sayıma girmez — `board` zaten unit id'sine göre anahtarlanmış, aynı unit
 * iki kez sayılamaz). */
function computeTraitCounts(board, units) {
  const counts = {};
  for (const unitId of Object.keys(board)) {
    const unitInfo = units[unitId];
    if (!unitInfo) continue;
    for (const traitId of unitInfo.traits) {
      counts[traitId] = (counts[traitId] || 0) + 1;
    }
  }
  return counts;
}

/** section 7.1: breakpoint'lerle karşılaştır, ulaşılan en yüksek eşiği aktif
 * tier olarak al. */
function computeActiveTraits(board, units, traits) {
  const counts = computeTraitCounts(board, units);
  const activeTraits = {};
  for (const [traitId, count] of Object.entries(counts)) {
    const breakpoints = traits[traitId] ? traits[traitId].breakpoints : [];
    let tier = 0;
    for (const bp of breakpoints) {
      if (count >= bp) tier += 1;
    }
    if (tier > 0) activeTraits[traitId] = tier;
  }
  return activeTraits;
}

/** section 7.2 — meta.json'daki kod parçasının birebir uygulanması. */
function buildBoardVector(board, meta, activeTraits) {
  const vec = meta.unit_order.map((id) => (board[id] ? board[id].star : 0) || 0);

  if (meta.vector_includes_traits) {
    vec.push(...meta.trait_order.map((t) => activeTraits[t] || 0));
  }

  const norm = Math.hypot(...vec);
  return norm > 0 ? vec.map((v) => v / norm) : vec;
}

/** section 7.3 — cosine mesafe (vektörler L2-unit-norm, düz nokta çarpımı),
 * off_meta/_metadata hariç her centroid'e karşı, en yakını seç. */
function nearestComp(vector, comps, matchThreshold) {
  const distances = [];
  for (const [compId, info] of Object.entries(comps)) {
    // "c_NN" olmayan her şeyi atla — off_meta'nın centroid'i yok, _metadata
    // (avg_placement_tiers) hiç bir comp değil.
    if (!compId.startsWith("c_")) continue;
    const centroid = info.centroid;
    let dot = 0;
    for (let i = 0; i < vector.length; i++) dot += vector[i] * centroid[i];
    distances.push({ compId, distance: 1 - dot });
  }
  distances.sort((a, b) => a.distance - b.distance);

  const nearest = distances[0];
  const matchedId = nearest && nearest.distance <= matchThreshold ? nearest.compId : null;

  return {
    nearestId: nearest ? nearest.compId : null,
    distance: nearest ? nearest.distance : Infinity,
    matchedId,
    ranked: distances,
  };
}

// ---------------------------------------------------------------------------
// Self-test (SPEC-WEB.md section 7.2 — "projedeki en sinsi hata sınıfına
// karşı tek savunma")
// ---------------------------------------------------------------------------

function runSelfTest() {
  const { meta, units, traits, comps, selftest } = state;
  const tolerance = selftest.suggested_vector_tolerance || 1e-4;
  const failures = [];

  for (const testCase of selftest.cases) {
    const testBoard = {};
    for (const u of testCase.input_units) {
      testBoard[u.id] = { star: u.star };
    }

    const activeTraits = computeActiveTraits(testBoard, units, traits);
    const vector = buildBoardVector(testBoard, meta, activeTraits);
    const expected = testCase.expected_vector;

    if (vector.length !== expected.length) {
      failures.push({ case: testCase.id, reason: `vektör boyutu ${vector.length}, beklenen ${expected.length}` });
      continue;
    }

    let maxDiff = 0;
    let maxDiffIndex = -1;
    for (let i = 0; i < vector.length; i++) {
      const diff = Math.abs(vector[i] - expected[i]);
      if (diff > maxDiff) {
        maxDiff = diff;
        maxDiffIndex = i;
      }
    }
    if (maxDiff > tolerance) {
      failures.push({
        case: testCase.id,
        reason:
          `vektör boyut ${maxDiffIndex}'te uyuşmuyor: hesaplanan=${vector[maxDiffIndex]}, ` +
          `beklenen=${expected[maxDiffIndex]}, fark=${maxDiff.toExponential(3)} (tolerans=${tolerance})`,
      });
      continue;
    }

    const { nearestId, distance, matchedId } = nearestComp(vector, comps, meta.match_threshold);

    if (nearestId !== testCase.nearest_comp) {
      failures.push({
        case: testCase.id,
        reason: `en yakın comp uyuşmuyor: hesaplanan=${nearestId}, beklenen=${testCase.nearest_comp}`,
      });
      continue;
    }
    if (Math.abs(distance - testCase.distance) > tolerance) {
      failures.push({ case: testCase.id, reason: `mesafe uyuşmuyor: hesaplanan=${distance}, beklenen=${testCase.distance}` });
      continue;
    }
    if (matchedId !== testCase.matched_comp) {
      failures.push({
        case: testCase.id,
        reason: `eşleşme kararı uyuşmuyor: hesaplanan=${matchedId}, beklenen=${testCase.matched_comp}`,
      });
    }
  }

  if (failures.length === 0) {
    console.log(
      `[selftest] PASSED — ${selftest.cases.length}/${selftest.cases.length} case tutuyor ` +
        `(vektör + en yakın comp + mesafe + eşleşme kararı).`
    );
    return { passed: true, failures: [] };
  }

  console.error(`[selftest] FAILED — ${failures.length}/${selftest.cases.length} case tutmuyor:`);
  for (const f of failures) console.error(`  - ${f.case}: ${f.reason}`);
  return { passed: false, failures };
}

// ---------------------------------------------------------------------------
// Ortak yardımcılar — görsel fallback (section 4), küçük dekoratif ikonlar
// ---------------------------------------------------------------------------

function unitInitials(name) {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();
}

function makeFallbackBox(name, className) {
  const box = document.createElement("div");
  box.className = className;
  box.textContent = unitInitials(name);
  return box;
}

/** Küçük, set verisinden bağımsız bir "altın sikke" ikonu — section 5.2'nin
 * "maliyet (altın bir ikon + sayı)" isteği için. Oyun/set içeriği değil,
 * dekoratif bir arayüz öğesi olduğu için inline SVG (section 10'un "set
 * verisini koda gömme" kuralı unit/trait/item/augment kimlikleri içindir). */
function goldCoinSvg() {
  return '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="#e0a83a" stroke="#8a5a10" stroke-width="1"/></svg>';
}

// ---------------------------------------------------------------------------
// Unit kartı — TFT kart estetiği, palet ve board slot'unda ortak
// (section 4, 5.2, 6)
// ---------------------------------------------------------------------------

function createUnitCard(unitId, mode) {
  const info = state.units[unitId];
  const card = document.createElement("div");
  card.className = `tft-card cost-${info.cost}`;
  card.draggable = true;
  card.dataset.unitId = unitId;

  // Portre arka plan olarak — GERÇEK bir <img> (section 5.2'nin uyarısı:
  // CSS background-image ile onerror hiç tetiklenmez).
  const img = document.createElement("img");
  img.className = "tft-card-portrait";
  img.src = info.icon;
  img.loading = "lazy"; // section 4 — palette 60+ görsel var
  img.alt = info.name;
  img.onerror = () => {
    img.replaceWith(makeFallbackBox(info.name, "tft-card-fallback"));
  };
  card.appendChild(img);

  // Sol altta, dikey sıralı trait ikonları + adları (section 5.2).
  const traitsBox = document.createElement("div");
  traitsBox.className = "tft-card-traits";
  for (const traitId of info.traits) {
    const traitInfo = state.traits[traitId];
    if (!traitInfo) continue;
    const chip = document.createElement("div");
    chip.className = "trait-chip";
    const traitImg = document.createElement("img");
    traitImg.src = traitInfo.icon;
    traitImg.loading = "lazy";
    traitImg.alt = traitInfo.name;
    traitImg.onerror = () => {
      const fb = document.createElement("span");
      fb.className = "trait-fallback";
      fb.textContent = "•";
      traitImg.replaceWith(fb);
    };
    chip.appendChild(traitImg);
    const label = document.createElement("span");
    label.textContent = traitInfo.name;
    chip.appendChild(label);
    traitsBox.appendChild(chip);
  }
  card.appendChild(traitsBox);

  // Alt şerit: solda ad, sağda maliyet (altın ikon + sayı).
  const footer = document.createElement("div");
  footer.className = "tft-card-footer";
  const nameEl = document.createElement("span");
  nameEl.className = "tft-card-name";
  nameEl.textContent = info.name;
  const costEl = document.createElement("span");
  costEl.className = "tft-card-cost";
  costEl.innerHTML = `${goldCoinSvg()}${info.cost}`;
  footer.append(nameEl, costEl);
  card.appendChild(footer);

  // section 6.2: dragstart içinde dataTransfer.setData ile unit ID'sini taşı.
  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", unitId);
    if (mode === "board") {
      // section 6.1: "sürükleyip dışarı atma da çalışsın" — board kökenli
      // sürüklemeyi palet kökenlisinden ayırt etmek için ikinci bir tip.
      e.dataTransfer.setData("application/x-tft-board-unit", unitId);
    }
  });

  if (mode === "palette") {
    const onBoard = Object.prototype.hasOwnProperty.call(board, unitId);
    if (onBoard) {
      // section 5.2: "Palette zaten eklenmiş unit'i soluklaştır."
      card.classList.add("dimmed");
    } else {
      // section 6.1: tıklama, sürükle-bırakın ZORUNLU alternatifi.
      card.addEventListener("click", () => addUnitToBoard(unitId));
    }
  } else {
    const stars = document.createElement("div");
    stars.className = "star-selector";
    for (const level of [1, 2, 3]) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = String(level);
      if (board[unitId].star === level) btn.classList.add("active");
      btn.addEventListener("click", (e) => {
        e.stopPropagation(); // kartın kendi "tıkla-çıkar" davranışını tetiklemesin
        setUnitStar(unitId, level);
      });
      stars.appendChild(btn);
    }
    card.appendChild(stars);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-btn";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `${info.name} çıkar`);
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      removeUnitFromBoard(unitId);
    });
    card.appendChild(removeBtn);

    // section 6.1: "board'daki unit'e tıklamak ... çıkarsın" — x butonuna ek,
    // tek yol değil.
    card.addEventListener("click", () => removeUnitFromBoard(unitId));
  }

  return card;
}

// ---------------------------------------------------------------------------
// Palet — yatay panel, maliyet sekmeleri (section 5.4)
// ---------------------------------------------------------------------------

/** Trait filtresi seçeneklerini traits.json'dan doldurur — section 10'un
 * "set verisini koda gömme" kuralı: hiçbir trait adı/ID'si burada sabit
 * yazılmıyor, hepsi veriden geliyor. Veri yüklendikten sonra bir kez çağrılır. */
function populateTraitFilterOptions() {
  const select = document.getElementById("palette-trait-filter");
  const traitIds = Object.keys(state.traits).sort((a, b) => state.traits[a].name.localeCompare(state.traits[b].name));
  for (const traitId of traitIds) {
    const option = document.createElement("option");
    option.value = traitId;
    option.textContent = state.traits[traitId].name;
    select.appendChild(option);
  }
}

function renderPalette() {
  const grid = document.getElementById("palette-grid");
  grid.innerHTML = "";

  const search = document.getElementById("palette-search").value.trim().toLowerCase();
  const traitFilter = document.getElementById("palette-trait-filter").value;
  const searching = search.length > 0;

  // section 5.4: arama aktifken maliyet sekmesi devre dışı — hiçbir sekme
  // "seçili" görünmemeli, sonuçlar tüm maliyetlerden gelsin.
  document.querySelectorAll(".cost-tab").forEach((btn) => {
    btn.classList.toggle("active", !searching && btn.dataset.cost === activeCostTab);
  });

  const unitIds = Object.keys(state.units).sort((a, b) => state.units[a].name.localeCompare(state.units[b].name));

  for (const unitId of unitIds) {
    const info = state.units[unitId];
    if (!searching && String(info.cost) !== activeCostTab) continue;
    if (search && !info.name.toLowerCase().includes(search)) continue;
    if (traitFilter && !info.traits.includes(traitFilter)) continue;
    grid.appendChild(createUnitCard(unitId, "palette"));
  }
}

function setupPaletteFilters() {
  document.getElementById("palette-search").addEventListener("input", renderPalette);
  document.getElementById("palette-trait-filter").addEventListener("change", renderPalette);
}

function setupCostTabs() {
  document.querySelectorAll(".cost-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeCostTab = btn.dataset.cost;
      // Bir sekmeye tıklamak, sekmeyi aşan bir aramayı temizler — yoksa
      // sekme "seçili" görünür ama arama onu görmezden gelmeye devam eder,
      // kafa karıştırıcı olur.
      document.getElementById("palette-search").value = "";
      renderPalette();
    });
  });
}

function setupPaletteToggle() {
  const toggle = document.getElementById("palette-toggle");
  toggle.addEventListener("click", () => {
    const grid = document.getElementById("palette-grid");
    const expanded = grid.classList.toggle("expanded");
    toggle.textContent = expanded ? "Daralt ▴" : "Genişlet ▾";
  });
}

/** section 5.5: sonuç paneli artık sağdan kayan bir overlay. Buton her zaman
 * tıklanabilir (board boş / comp eşleşmemiş olsa da) — panelin açık/kapalı
 * durumu içeriğin güncelliğinden bağımsız, updateResult() board her
 * değiştiğinde #result-content'i zaten dolduruyor, sekme sadece görünürlüğü
 * değiştiriyor. */
function setupResultPanelToggle() {
  const panel = document.getElementById("result-panel");
  const toggle = document.getElementById("result-toggle");
  toggle.addEventListener("click", () => {
    const open = panel.classList.toggle("open");
    toggle.textContent = open ? "›" : "‹";
    toggle.setAttribute("aria-expanded", String(open));
  });
}

// ---------------------------------------------------------------------------
// Önerilen comp'lar paneli (section 5.3)
// ---------------------------------------------------------------------------

function renderSuggestions() {
  const list = document.getElementById("suggestions-list");
  list.innerHTML = "";

  // "c_NN" olmayan her şeyi atla — off_meta'nın core_units'i yok, _metadata
  // (avg_placement_tiers) hiç bir comp değil.
  const realComps = Object.entries(state.comps).filter(([compId]) => compId.startsWith("c_"));
  // TFT'de düşük placement iyidir (section 10) — artan sırada = en iyiden başla.
  realComps.sort((a, b) => a[1].avg_placement - b[1].avg_placement);
  const top = realComps.slice(0, SUGGESTION_COUNT);

  for (const [compId, comp] of top) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "suggestion-row";
    row.dataset.compId = compId;

    const name = document.createElement("span");
    name.className = "suggestion-name";
    name.textContent = comp.name;
    const tierBadge = createTierBadge(comp.avg_placement);
    if (tierBadge) name.appendChild(tierBadge);

    const stats = document.createElement("span");
    stats.className = "suggestion-stats";
    stats.textContent = `Ort. sıra ${comp.avg_placement.toFixed(2)} · İlk 4 %${(comp.top4_rate * 100).toFixed(1)} · n=${comp.n_total}`;

    row.append(name, stats);
    row.addEventListener("click", () => loadSuggestedComp(compId));
    list.appendChild(row);
  }
}

/** comps.json'ın core_unit_avg_stars ortalamasını (section 3.4) board'un
 * tam sayı yıldızına çevirir. Sabit 2 yıldız çoğu unit için yanlış olurdu:
 * carry'ler bilinçli 3-yıldızlanıyor, düşük öncelikli unit'ler genelde 1'de
 * kalıyor. Yuvarlama: standart
 * "yarımdan yukarı" (Math.round — 2.56→3, 2.49→2). Ortalama tanım gereği
 * [1,3] aralığında olduğu için clamp gerçek veride hiç devreye girmez,
 * sadece savunma amaçlı (eski/eksik bir comps.json'a karşı). */
function starFromAverage(avgStar) {
  return Math.min(3, Math.max(1, Math.round(avgStar)));
}

/** section 8.3: SADECE sonuç panelinin/modalın
 * item önerileri bölümündeki core unit ikonlarında bir yıldız rozeti — palet
 * ve board kartlarına dokunulmuyor, oradaki yıldız kullanıcının kendi seçimi
 * (gerçek bir ortalama değil), ikisi karıştırılmasın. Gösterim: yuvarlanmış
 * tek basamak (starFromAverage — board doldurmada kullanılanla AYNI
 * yuvarlama), maliyet rozetiyle aynı görsel dilde (küçük, köşeli, renk
 * kodlu), ikonun köşesinde. Tooltip ham ortalama + n taşır — çıplak yuvarlak
 * sayı değil. Tüm seviyeler gösterilir, eşik yok: 1★ da bilgi (bu unit'i
 * yıldızlamaya çalışma sinyali), 3★ kadar "önemsiz" değil. */
function createUnitStarBadge(avgStarEntry) {
  if (!avgStarEntry) return null;
  const level = starFromAverage(avgStarEntry.avg);
  const badge = document.createElement("span");
  badge.className = `unit-star-badge star-level-${level}`;
  badge.textContent = `${level}★`;
  badge.title = `Bu comp'ta ortalama ${avgStarEntry.avg.toFixed(2)} yıldız (n=${avgStarEntry.n}).`;
  return badge;
}

/** section 5.3: bir öneriye tıklanınca board'u o comp'un core_units'iyle
 * doldur — board doluysa bile onay istemeden, doğrudan (comp'lar arası
 * hızlı geçiş için). core_unit_avg_stars girdileri `{avg, n}`; burada
 * sadece `.avg` okunuyor. */
function loadSuggestedComp(compId) {
  const comp = state.comps[compId];
  const coreUnits = comp.core_units.slice(0, BOARD_SIZE);
  const omitted = comp.core_units.length - coreUnits.length;
  const avgStars = comp.core_unit_avg_stars || {};

  board = {};
  boardOrder = [];
  for (const unitId of coreUnits) {
    const entry = avgStars[unitId];
    board[unitId] = { star: entry ? starFromAverage(entry.avg) : DEFAULT_STAR };
    boardOrder.push(unitId);
  }

  if (omitted > 0) {
    showToast(`${comp.name}: ${omitted} unit board'a sığmadı, en az sık görülenler bırakıldı.`);
  }

  onBoardChanged();
}

// ---------------------------------------------------------------------------
// Board (section 5.1 — hex ızgara YOK, düz 10 slot)
// ---------------------------------------------------------------------------

function renderBoardSlots() {
  const container = document.getElementById("board-slots");
  container.innerHTML = "";
  for (let i = 0; i < BOARD_SIZE; i++) {
    const unitId = boardOrder[i];
    const slot = document.createElement("div");
    slot.dataset.slotIndex = String(i);
    if (unitId) {
      slot.className = "board-slot occupied";
      slot.appendChild(createUnitCard(unitId, "board"));
    } else {
      slot.className = "board-slot";
    }
    container.appendChild(slot);
  }
}

function showToast(message) {
  const container = document.getElementById("board-toast-container");
  container.innerHTML = "";
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  container.appendChild(toast);
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.remove(), 3000);
}

function addUnitToBoard(unitId) {
  if (Object.prototype.hasOwnProperty.call(board, unitId)) return; // section 5.2: iki kez eklenemez
  if (boardOrder.length >= BOARD_SIZE) {
    // section 6.2: "Board dolu iken bırakma denemesi sessizce başarısız olmasın."
    showToast(`Board dolu (${BOARD_SIZE} unit) — önce bir unit çıkarın.`);
    return;
  }
  board[unitId] = { star: DEFAULT_STAR };
  boardOrder.push(unitId);
  onBoardChanged();
}

function removeUnitFromBoard(unitId) {
  if (!Object.prototype.hasOwnProperty.call(board, unitId)) return;
  delete board[unitId];
  boardOrder = boardOrder.filter((id) => id !== unitId);
  onBoardChanged();
}

function setUnitStar(unitId, star) {
  if (!board[unitId]) return;
  board[unitId].star = star;
  onBoardChanged();
}

/** .layout-grid CSS Grid'inde align-items:start tek
 * başına board ile palet arasındaki boşluğu çözmüyor — Grid'in auto-satır
 * boyutlandırması align-items'tan bağımsız olarak satırı o satırdaki EN UZUN
 * öğeye (genelde 8 comp'lu .side-column) göre kuruyor; ölçüldü:
 * getComputedStyle(.layout-grid).gridTemplateRows .side-column'un doğal
 * yüksekliğiyle birebir aynı çıktı, .board-column kısa kaldığı için altında
 * 124-160px boş satır alanı kalıyordu. CSS'in bunu çözecek bir yolu yok (bir
 * grid satırının yüksekliğini "en kısa öğeye göre kur" diye ayarlayamazsınız)
 * — bu yüzden .side-column'un max-height'i burada, .board-column'un GERÇEK
 * render yüksekliğine eşitleniyor; taşarsa style.css'teki overflow-y:auto
 * devreye giriyor. Board her değiştiğinde bu elle çağrılmıyor —
 * ResizeObserver .board-column'un kutu boyutunu izliyor, boyut ne SEBEPLE
 * değişirse değişsin (board render, pencere resize, font yüklenmesi, zoom)
 * otomatik yeniden hesaplıyor; ilk gözlemde de bir kere kendiliğinden
 * tetikleniyor, o yüzden ayrıca bir "ilk render'da çağır" satırı gerekmiyor. */
function setupSideColumnSync() {
  const boardColumn = document.querySelector(".board-column");
  const sideColumn = document.querySelector(".side-column");
  if (!boardColumn || !sideColumn) return;
  const observer = new ResizeObserver(() => {
    sideColumn.style.maxHeight = `${boardColumn.getBoundingClientRect().height}px`;
  });
  observer.observe(boardColumn);
}

function onBoardChanged() {
  renderPalette();
  renderBoardSlots();
  renderActiveTraits();
  updateResult();
}

// ---------------------------------------------------------------------------
// Sürükle-bırak (section 6)
// ---------------------------------------------------------------------------

function setupDragAndDrop() {
  const slotsContainer = document.getElementById("board-slots");

  slotsContainer.addEventListener("dragover", (e) => {
    e.preventDefault(); // section 6.2 — zorunlu, yoksa drop hiç tetiklenmez
    document.querySelectorAll(".board-slot.drag-over").forEach((el) => el.classList.remove("drag-over"));
    const slot = e.target.closest(".board-slot");
    if (slot) slot.classList.add("drag-over"); // section 6.2 — hedef slotu görsel olarak vurgula
  });

  slotsContainer.addEventListener("dragleave", (e) => {
    const slot = e.target.closest(".board-slot");
    if (slot) slot.classList.remove("drag-over");
  });

  // Board dışına bırakınca da çalışması için (section 6.1) sayfanın tamamı
  // geçerli bir drop hedefi olmalı — bu yüzden document seviyesinde de
  // preventDefault gerekiyor, sadece board-slots'ta değil.
  document.addEventListener("dragover", (e) => e.preventDefault());

  document.addEventListener("drop", (e) => {
    document.querySelectorAll(".board-slot.drag-over").forEach((el) => el.classList.remove("drag-over"));

    const unitId = e.dataTransfer.getData("text/plain");
    if (!unitId) return;
    e.preventDefault();

    const droppedInsideBoard = e.target.closest("#board-slots");
    const fromBoard = e.dataTransfer.types.includes("application/x-tft-board-unit");

    if (droppedInsideBoard) {
      addUnitToBoard(unitId); // zaten board'daysa sessizce yoksayılır
    } else if (fromBoard) {
      removeUnitFromBoard(unitId); // section 6.1: sürükleyip dışarı atma
    }
  });
}

// ---------------------------------------------------------------------------
// Aktif trait paneli (section 7.1)
// ---------------------------------------------------------------------------

function renderActiveTraits() {
  const container = document.getElementById("active-traits");
  container.innerHTML = "";

  const counts = computeTraitCounts(board, state.units);
  const rows = [];

  for (const [traitId, count] of Object.entries(counts)) {
    const info = state.traits[traitId];
    if (!info) continue;
    const breakpoints = info.breakpoints;
    const tier = breakpoints.filter((bp) => count >= bp).length;
    const next = breakpoints.find((bp) => bp > count);
    const active = tier > 0;
    // "Aktif olmayan ama 1 eksikle aktifleşecek olanları soluk göster" —
    // sadece HENÜZ aktif olmayan trait'ler için (section 7.1).
    const almost = !active && next !== undefined && next - count === 1;
    if (!active && !almost) continue;

    const label = next !== undefined ? `${count}/${next}` : `${count}/${breakpoints[breakpoints.length - 1]}`;
    rows.push({ traitId, info, tier, active, almost, label });
  }

  rows.sort((a, b) => b.tier - a.tier || a.info.name.localeCompare(b.info.name));

  if (rows.length === 0) {
    container.innerHTML = '<p class="empty-state">Aktif trait yok.</p>';
    return;
  }

  for (const row of rows) {
    const badge = document.createElement("div");
    badge.className = "trait-badge" + (row.almost ? " almost" : "");
    const img = document.createElement("img");
    img.src = row.info.icon;
    img.loading = "lazy";
    img.alt = row.info.name;
    img.onerror = () => img.replaceWith(makeFallbackBox(row.info.name, "trait-fallback"));
    badge.appendChild(img);
    const label = document.createElement("span");
    label.textContent = `${row.info.name} ${row.label}`;
    badge.appendChild(label);
    container.appendChild(badge);
  }
}

// ---------------------------------------------------------------------------
// Sonuç paneli (section 8, dil section 8.2) — dil kuralı (section 9):
// "counter"/"win rate" yok, "üstünlük" kullanılıyor, ham sayı yerine cümle.
// ---------------------------------------------------------------------------

function compName(comps, compId) {
  return (comps[compId] && comps[compId].name) || compId;
}

// ---------------------------------------------------------------------------
// Meta-tier rozeti (section 8) — sınırlar KODA GÖMÜLMEDİ:
// comps.json'ın _metadata.avg_placement_tiers'ından okunuyor (clustering.py
// her koşuda 19 comp'un avg_placement dağılımından yeniden hesaplıyor),
// sadece etiket metni/renk sırası burada — "zayıf eşleşme"/"düşük güven" ile
// aynı desen (sayısal eşik veriden, UI metni koddan).
//
// Bantlar standart sapma bazlı (mean±0.5σ/1.5σ), sabit yüzdelik değil —
// gerekçe için bkz. clustering.py'nin compute_avg_placement_tiers()'ı.
// ---------------------------------------------------------------------------

const TIER_BANDS = [
  { id: "s", label: "S" },
  { id: "a", label: "A" },
  { id: "b", label: "B" },
  { id: "c", label: "C" },
  { id: "d", label: "D" },
];

function getTierBand(avgPlacement, tiers) {
  if (!tiers) return null;
  const b = tiers.bounds;
  if (avgPlacement < b.s_max) return TIER_BANDS[0];
  if (avgPlacement < b.a_max) return TIER_BANDS[1];
  if (avgPlacement < b.b_max) return TIER_BANDS[2];
  if (avgPlacement < b.c_max) return TIER_BANDS[3];
  return TIER_BANDS[4];
}

function createTierBadge(avgPlacement) {
  const metadata = state.comps._metadata;
  const tiers = metadata && metadata.avg_placement_tiers;
  const band = getTierBand(avgPlacement, tiers);
  if (!band) return null;

  const badge = document.createElement("span");
  badge.className = `tier-badge tier-badge-${band.id}`;
  badge.textContent = band.label;
  badge.title =
    `${band.label} bandı, ${tiers.n_comps} comp'un ortalama sıralama dağılımından türetildi ` +
    `(ortalama=${tiers.mean.toFixed(2)}, standart sapma=${tiers.stdev.toFixed(2)}) — mutlak bir güç ölçüsü ` +
    `değil, bu koşudaki comp'lar arasında göreceli bir sıralama. Bu comp'un ortalaması: ${avgPlacement.toFixed(2)}.`;
  return badge;
}

/** Bir delta/lift değerini yöne çeviren cümle parçası — TFT'de düşük
 * placement iyi olduğu için negatif değer her zaman "iyileştiriyor" demeli
 * (section 10'un "düşük placement'ı kötü renklendirme" hatasına düşme). */
function directionPhrase(value) {
  return value < 0
    ? `ortalama sıralamayı ${Math.abs(value).toFixed(2)} iyileştiriyor`
    : `ortalama sıralamayı ${value.toFixed(2)} kötüleştiriyor`;
}

function updateResult() {
  const container = document.getElementById("result-content");
  container.innerHTML = "";

  if (boardOrder.length < MIN_UNITS_FOR_MATCH) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = `Eşleştirme için en az ${MIN_UNITS_FOR_MATCH} unit gerekli — şu an ${boardOrder.length} unit var, daha fazla unit ekleyin.`;
    container.appendChild(p);
    return;
  }

  const { meta, units, traits, comps, matchups, compTraits, items } = state;
  const activeTraits = computeActiveTraits(board, units, traits);
  const vector = buildBoardVector(board, meta, activeTraits);
  const { distance, matchedId, ranked } = nearestComp(vector, comps, meta.match_threshold);

  if (!matchedId) {
    renderUnmatched(container, ranked, distance, meta.match_threshold, comps);
    return;
  }

  renderCompReport(container, matchedId, distance, meta, comps, matchups, compTraits, items, units, traits);
}

/** section 7.3: "en yakın 3 comp" tıklanabilir — sol sütundaki öneri
 * listesiyle AYNI davranış (loadSuggestedComp): board o comp'un
 * core_units'iyle dolar, sonuç paneli o comp'un raporunu gösterir. Modal
 * AÇMAZ — bu board'u değiştiren bir eylem; section 8.4'ün head-to-head
 * modalıyla (board'a dokunmadan bakış) karıştırılmamalı. */
function renderUnmatched(container, ranked, distance, threshold, comps) {
  // section 7.3 / src/cli.py: zorla bir comp'a eşleştirip rapor gösterme.
  const p1 = document.createElement("p");
  p1.textContent =
    `Bu board şu anda bilinen hiçbir comp'a yeterince yakın değil ` +
    `(en yakın mesafe ${distance.toFixed(2)}, eşik ${threshold.toFixed(2)}).`;
  container.appendChild(p1);

  const p2 = document.createElement("p");
  p2.textContent = "En yakın 3 comp (tıklayınca board'a yüklenir):";
  container.appendChild(p2);

  const list = document.createElement("ul");
  list.className = "nearest-list";
  for (const { compId, distance: d } of ranked.slice(0, 3)) {
    const li = document.createElement("li");
    const link = document.createElement("button");
    link.type = "button";
    link.className = "comp-link";
    link.textContent = compName(comps, compId);
    link.addEventListener("click", () => loadSuggestedComp(compId));
    li.append(link, document.createTextNode(` — mesafe ${d.toFixed(2)}.`));
    list.appendChild(li);
  }
  container.appendChild(list);
}

/** Bir comp'un item/istatistik/head-to-head/trait gövdesi — hem ana sonuç
 * panelinde (board eşleşmesi bağlamında) hem de section 8.4'ün modalında (board'a
 * dokunmadan "bu comp nasıl" bakışında) AYNI şekilde kullanılıyor, tek yerden
 * üretiliyor ki ikisi birbirinden sürüklenmesin. Sıra: item
 * önerileri → özet → head-to-head → trait'ler. */
function buildCompReportBody(compId, comps, matchups, compTraits, items, units, traits) {
  const comp = comps[compId];
  const frag = document.createDocumentFragment();

  const statsP = document.createElement("p");
  statsP.textContent =
    `Bu comp ortalama ${comp.avg_placement.toFixed(2)} sırada bitiriyor, ` +
    `oyunların %${(comp.top4_rate * 100).toFixed(0)}'sinde ilk 4'e giriyor (${comp.n_total} örnek).`;

  frag.appendChild(renderItemSection(compId, comp, items, units));
  frag.appendChild(statsP);
  frag.appendChild(renderHeadToHead(compId, matchups, comps));
  frag.appendChild(renderTraitSection(compId, compTraits, traits));
  return frag;
}

function renderCompReport(container, compId, distance, meta, comps, matchups, compTraits, items, units, traits) {
  const comp = comps[compId];
  const wrap = document.createElement("div");
  wrap.className = "comp-report";

  const h3 = document.createElement("h3");
  h3.textContent = comp.name;
  const tierBadge = createTierBadge(comp.avg_placement);
  if (tierBadge) h3.appendChild(tierBadge);
  if (distance >= WEAK_MATCH_RATIO * meta.match_threshold) {
    const badge = document.createElement("span");
    badge.className = "badge-weak-match";
    badge.textContent = "zayıf eşleşme";
    h3.appendChild(badge);
  }
  wrap.appendChild(h3);

  const metaP = document.createElement("p");
  metaP.className = "match-meta";
  metaP.textContent = `Board'unuz bu comp'a mesafe ${distance.toFixed(2)} ile en yakın (eşik ${meta.match_threshold.toFixed(2)}).`;
  wrap.appendChild(metaP);

  wrap.appendChild(buildCompReportBody(compId, comps, matchups, compTraits, items, units, traits));

  container.appendChild(wrap);
}

/** src/cli.py's format_head_to_head ile aynı mantık: geçerli (rate'i olan)
 * eşleşme sayısı 2*TOP_K'nin altındaysa tek sıralı liste (yüksekten düşüğe),
 * üstündeyse en yüksek/en düşük üstünlük TOP_K'şar. Hiçbir eşleşme %50
 * sınırını geçmiyorsa/altına inmiyorsa bunu belirten not. Fark: burada ayrıca
 * (SPEC-WEB.md section 8.1 — "yetersiz veri... gizlenmeyecek") insufficient_data
 * olan eşleşmeler sıralamaya girmiyor ama ayrı bir cümlede İSİMLEN belirtiliyor,
 * src/cli.py'de olduğu gibi sessizce atlanmıyor. Dil section 8.2: cümle,
 * satır değil. */
function renderHeadToHead(compId, matchups, comps) {
  const section = document.createElement("div");
  const h = document.createElement("h4");
  h.textContent = "Head-to-head";
  section.appendChild(h);

  const entries = matchups[compId] || {};
  const validEntries = Object.entries(entries).filter(([, cell]) => "rate" in cell);
  const insufficientEntries = Object.entries(entries).filter(([, cell]) => cell.insufficient_data === true);

  // section 8.4: comp isimleri tıklanabilir buton — tıklanınca board'a
  // dokunmadan o comp'un tam raporunu modalda açar.
  function compLink(oppId) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "comp-link";
    link.textContent = compName(comps, oppId);
    link.addEventListener("click", () => openCompModal(oppId));
    return link;
  }

  function sentence(oppId, cell) {
    const p = document.createElement("p");
    p.className = "h2h-sentence";
    const ciWidth = cell.ci[1] - cell.ci[0];
    if (ciWidth > WIDE_CI_WIDTH) p.classList.add("low-confidence");
    const rate = (cell.rate * 100).toFixed(0);
    const lo = (cell.ci[0] * 100).toFixed(0);
    const hi = (cell.ci[1] * 100).toFixed(0);
    let rest =
      `'a karşı aynı lobide %${rate} oranında daha yüksek sırada bitiriyor ` +
      `(${cell.n} maçta, güven aralığı %${lo}-%${hi}).`;
    if (ciWidth > WIDE_CI_WIDTH) rest += " Az veriyle hesaplandığı için bu aralık geniş, temkinli okuyun.";
    p.append(compLink(oppId), document.createTextNode(rest));
    return p;
  }

  if (validEntries.length === 0) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = "Bu comp için yeterli head-to-head verisi yok.";
    section.appendChild(p);
  } else {
    const byRate = validEntries.slice().sort((a, b) => a[1].rate - b[1].rate);

    if (byRate.length < 2 * TOP_K) {
      const label = document.createElement("p");
      label.textContent = "Bilinen eşleşmeler, üstünlük oranına göre yüksekten düşüğe:";
      section.appendChild(label);
      for (const [oppId, cell] of byRate.slice().reverse()) section.appendChild(sentence(oppId, cell));
    } else {
      const best = byRate.slice(-TOP_K).reverse();
      const worst = byRate.slice(0, TOP_K);
      const bestLabel = document.createElement("p");
      bestLabel.textContent = "En yüksek üstünlük sağladığı comp'lar:";
      section.appendChild(bestLabel);
      for (const [oppId, cell] of best) section.appendChild(sentence(oppId, cell));
      const worstLabel = document.createElement("p");
      worstLabel.textContent = "En düşük üstünlük sağladığı comp'lar:";
      section.appendChild(worstLabel);
      for (const [oppId, cell] of worst) section.appendChild(sentence(oppId, cell));
    }

    const minRate = byRate[0][1].rate;
    const maxRate = byRate[byRate.length - 1][1].rate;
    if (minRate >= 0.5 || maxRate <= 0.5) {
      const note = document.createElement("p");
      note.className = "insufficient-note";
      note.textContent =
        minRate >= 0.5
          ? "Yeterli veri olan hiçbir eşleşmede bu comp %50'nin altına düşmüyor."
          : "Yeterli veri olan hiçbir eşleşmede bu comp %50'nin üstüne çıkmıyor.";
      section.appendChild(note);
    }
  }

  if (insufficientEntries.length > 0) {
    const note = document.createElement("p");
    note.className = "insufficient-note";
    note.appendChild(document.createTextNode("Şu comp'larla yeterli veri olmadığı için karşılaştırma yapılamıyor: "));
    insufficientEntries.forEach(([oppId, cell], i) => {
      if (i > 0) note.appendChild(document.createTextNode(", "));
      note.append(compLink(oppId), document.createTextNode(` (${cell.n} maç)`));
    });
    note.appendChild(document.createTextNode("."));
    section.appendChild(note);
  }

  return section;
}

/** src/cli.py's format_traits ile aynı ayrım, cümle biçiminde (section 8.2). */
function renderTraitSection(compId, compTraits, traits) {
  const section = document.createElement("div");
  const h = document.createElement("h4");
  h.textContent = "Trait analizi";
  section.appendChild(h);

  const entry = compTraits[compId] || {};

  function list(introText, emptyText, traitItems) {
    const p = document.createElement("p");
    p.textContent = introText;
    section.appendChild(p);
    if (!traitItems || traitItems.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = emptyText;
      section.appendChild(empty);
      return;
    }
    for (const item of traitItems) {
      const name = (traits[item.trait] && traits[item.trait].name) || item.trait;
      const row = document.createElement("p");
      row.className = "trait-sentence";
      row.textContent = `Rakipte ${name} aktifken bu comp'un ${directionPhrase(item.delta)} (${item.n} maçta).`;
      section.appendChild(row);
    }
  }

  list(
    "Bu comp'un rahat ettiği trait'ler — rakipte aktifken sıralaması iyileşiyor:",
    "Bu comp için yeterli veri yok.",
    entry.strong_against
  );
  list(
    "Bu comp'un zorlandığı trait'ler — rakipte aktifken sıralaması kötüleşiyor:",
    "Bu comp için yeterli veri yok.",
    entry.weak_against
  );

  return section;
}

/** section 3.8/8.3: item vektörün parçası değil, ayrı bir katman;
 * known_limitation kullanıcıya gösterilmeli. section 8.3: bu bölüm 8.2'nin
 * "cümle" kuralına girmiyor — görsel gösterim (unit ikonu + item ikonları,
 * tooltip'lerde ad/lift/n). */
function renderItemSection(compId, comp, items, units) {
  const section = document.createElement("div");
  const h = document.createElement("h4");
  h.textContent = "Item önerileri";
  if (items._metadata && items._metadata.known_limitation) {
    const info = document.createElement("span");
    info.textContent = " ⓘ";
    info.title = items._metadata.known_limitation;
    info.style.cursor = "help";
    h.appendChild(info);
  }
  section.appendChild(h);

  const perUnit = items[compId] || {};
  const coreUnits = comp.core_units || [];

  if (coreUnits.length === 0) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = "Bu comp için item verisi yok.";
    section.appendChild(p);
    return section;
  }

  const grid = document.createElement("div");
  grid.className = "item-groups-grid";

  for (const unitId of coreUnits) {
    const unitInfo = units[unitId];
    const unitName = (unitInfo && unitInfo.name) || unitId;

    const group = document.createElement("div");
    group.className = "item-unit-group";

    // Unit ikonu — üstüne gelince adı (native title, section 8.3).
    const unitIconWrap = document.createElement("div");
    unitIconWrap.className = "item-unit-icon-wrap";
    if (unitInfo) {
      const unitImg = document.createElement("img");
      unitImg.className = "item-unit-icon";
      unitImg.src = unitInfo.icon;
      unitImg.loading = "lazy";
      unitImg.alt = unitName;
      unitImg.title = unitName;
      unitImg.onerror = () => unitImg.replaceWith(makeFallbackBox(unitName, "item-unit-icon-fallback"));
      unitIconWrap.appendChild(unitImg);
    } else {
      unitIconWrap.appendChild(makeFallbackBox(unitName, "item-unit-icon-fallback"));
    }
    // section 8.3: SADECE burada — palet/board kartlarına dokunmuyor.
    const starBadge = createUnitStarBadge((comp.core_unit_avg_stars || {})[unitId]);
    if (starBadge) unitIconWrap.appendChild(starBadge);
    group.appendChild(unitIconWrap);

    const itemRow = document.createElement("div");
    itemRow.className = "item-icons-row";

    const list = perUnit[unitId] || [];
    if (list.length === 0) {
      // section 3.8: boş liste "veri yok" demek, "kötü item" değil.
      const p = document.createElement("span");
      p.className = "empty-state item-no-data";
      p.textContent = "Veri yok";
      itemRow.appendChild(p);
    } else {
      for (const it of list) {
        const itemImg = document.createElement("img");
        itemImg.className = "item-icon";
        itemImg.src = it.icon;
        itemImg.loading = "lazy";
        itemImg.alt = it.name;
        // section 8.3: tooltip'te yön sözle anlatılır, çıplak "lift: -0.34" değil.
        itemImg.title = `${it.name} — bu comp'ta ${directionPhrase(it.lift)} (${it.n} maçta)`;
        itemImg.onerror = () => {
          const fb = makeFallbackBox(it.name, "item-icon-fallback");
          fb.title = itemImg.title;
          itemImg.replaceWith(fb);
        };
        itemRow.appendChild(itemImg);
      }
    }
    group.appendChild(itemRow);
    grid.appendChild(group);
  }

  section.appendChild(grid);
  return section;
}

// ---------------------------------------------------------------------------
// Comp modal (section 8.4) — head-to-head'deki bir comp ismine
// tıklanınca board'a hiç dokunmadan o comp'un tam raporunu gösterir. Modal
// içindeki head-to-head isimleri de openCompModal'a bağlı olduğu için
// tıklanınca yeni bir modal AÇMIYOR, aynı modalın #comp-modal-content'ini
// yeniden dolduruyor — zincirleme gezinme bu şekilde "üst üste modal" değil
// "içerik değişimi" oluyor.
// ---------------------------------------------------------------------------

function openCompModal(compId) {
  const { comps, matchups, compTraits, items, units, traits } = state;
  const comp = comps[compId];
  if (!comp) return; // off_meta'nın centroid'i/raporu yok, buraya hiç düşmemeli

  const content = document.getElementById("comp-modal-content");
  content.innerHTML = "";

  const h3 = document.createElement("h3");
  h3.id = "comp-modal-heading";
  h3.textContent = comp.name;
  const tierBadge = createTierBadge(comp.avg_placement);
  if (tierBadge) h3.appendChild(tierBadge);
  content.appendChild(h3);
  content.appendChild(buildCompReportBody(compId, comps, matchups, compTraits, items, units, traits));

  document.getElementById("comp-modal-overlay").hidden = false;
  document.getElementById("comp-modal-close").focus();
}

function closeCompModal() {
  document.getElementById("comp-modal-overlay").hidden = true;
}

function setupCompModal() {
  document.getElementById("comp-modal-close").addEventListener("click", closeCompModal);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("comp-modal-overlay").hidden) {
      closeCompModal();
    }
  });
}

// ---------------------------------------------------------------------------
// Kısıtlar kutusu (section 8/9)
// ---------------------------------------------------------------------------

function renderConstraints() {
  const list = document.getElementById("constraints-list");
  const meta = state.meta;
  const patchText = meta.patch ? meta.patch : "bilinmiyor (yama verisi mevcut değil)";

  const bullets = [
    "Bu oranlar savaş sonuçlarını değil, aynı lobide bulunan iki comp'un final sıralamalarının karşılaştırmasını gösterir.",
    `Veri yalnızca yüksek elo (${meta.elo_range}) maçlarından toplanmıştır, tüm oyuncu tabanını temsil etmez.`,
    `Veri şu yamadan toplanmıştır: ${patchText}. Denge yamalarıyla sonuçlar eskir.`,
    "Dizilim, ekonomi ve item verisi analize dahil değildir.",
  ];

  // src/build_standalone.py'nin tek-dosya sürümü: veri ve arayüz kodu bu
  // HTML'in içinde, internet gerektirmeden çalışır — ama unit/trait/item
  // ikonları CommunityDragon CDN'inden çekiliyor (bölüm 4), o kısım hâlâ
  // internet ister. Sadece standalone modda gösteriliyor; normal sunucu
  // modunda zaten yerel bir sunucu kurulduğu için bu şaşırtıcı değil.
  if (dataWasEmbedded) {
    bullets.push(
      "Bu, tek dosyalık (standalone) bir sürüm — veri ve arayüz kodu bu HTML'in içinde, " +
        "internet olmadan da açılır. Ama unit/trait/item ikonları CommunityDragon'dan " +
        "yükleniyor, onları görmek için internet bağlantısı gerekir."
    );
  }

  list.innerHTML = "";
  for (const text of bullets) {
    const li = document.createElement("li");
    li.textContent = text;
    list.appendChild(li);
  }
}

// ---------------------------------------------------------------------------
// Başlangıç
// ---------------------------------------------------------------------------

async function init() {
  renderBoardSlots(); // veri gelmeden önce de 10 boş slot görünsün

  try {
    await loadData();
  } catch (err) {
    showFetchError(err);
    return;
  }

  const result = runSelfTest();
  if (!result.passed) {
    showSelfTestError(result.failures);
    return;
  }

  populateTraitFilterOptions();
  setupPaletteFilters();
  setupCostTabs();
  setupPaletteToggle();
  setupResultPanelToggle();
  setupCompModal();
  setupDragAndDrop();
  renderConstraints();
  renderSuggestions();
  renderPalette();
  renderBoardSlots();
  renderActiveTraits();
  updateResult();
  setupSideColumnSync();

  console.log("[app] Veri yüklendi, self-test geçti, arayüz etkileşimli.");
}

document.addEventListener("DOMContentLoaded", init);
