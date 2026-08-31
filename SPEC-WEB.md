# TFT Comp Analyzer — Web Arayüzü Spesifikasyonu

Uygulama spesifikasyonu · Ağustos 2026

Bu doküman, ayrı bir spec ile üretilmiş olan **TFT Comp Analyzer** analiz pipeline'ının çıktılarını gösteren web arayüzünü tarif eder. Pipeline (veri toplama, clustering, istatistik) bu dokümanın kapsamı **dışındadır** — burada anlatılan sadece arayüzdür ve pipeline'ın ürettiği JSON dosyalarının hazır olduğu varsayılır.

> **SPEC.md ile ilişki:** SPEC.md'nin (pipeline spec'i) 17. bölümü daha önce "web arayüzü yok, CLI yeter" diyordu; bu doküman yazılırken o madde güncellendi. CLI (SPEC.md bölüm 14) pipeline'ın birincil arayüzü olmaya devam ediyor — web arayüzü onun yerini almıyor, CLI'nin de okuduğu aynı `data/processed/`/`data/static/` çıktılarını tarayıcıda gösteren ayrı, ek bir katman.

## 1. Kapsam ve temel mimari kararı

Kullanım senaryosu: geliştirici ve 4-5 arkadaşı. Public bir ürün değil, dar bir grup için özel bir araç.

Bunun doğrudan bir mimari sonucu var: **site çalışma anında hiçbir dış servise istek atmaz.** Riot API çağrısı yok, backend yok, veritabanı yok, API anahtarı yok. Pipeline offline çalışır, statik JSON dosyaları üretir, tarayıcı o dosyaları okur. Bütün hesaplama (comp eşleştirme, trait sayımı) tarayıcıda yapılır.

Bu yüzden teknoloji seçimi de sade: düz HTML + CSS + JavaScript. React yok, build adımı yok, npm yok, bundler yok. Beş kişilik bir araç için framework kurmak net bir israf.

> **Tek gotcha:** Sayfayı `file://` ile açamazsın — tarayıcı JSON fetch'ini CORS nedeniyle engeller. Basit bir yerel sunucu gerekir (`python -m http.server 8000` yeter). Bunu README'nin ilk satırına yaz, yoksa arkadaşların dosyayı çift tıklayıp "çalışmıyor" diyecek.

### 1.1 Yasal not (kısa)

Ticari olmayan, dar kullanımlı bir fan projesi. Riot'ın oyun görsellerini kullanmak bu koşullarda sorun değil, ama alt bilgiye standart ifadeyi koy: bu proje Riot Games ile ilişkili değildir ve Riot tarafından onaylanmamıştır. Siteyi herkese açık hale getirmeye karar verirsen bu koşullar değişir; o noktada tekrar değerlendir.

## 2. Dosya yapısı

```
web/
  index.html
  style.css
  app.js
  data/            <- pipeline buraya kopyalar, elle düzenlenmez
    meta.json
    units.json
    traits.json
    comps.json
    matchups.json
    comp_traits.json
    items.json
    (augments.json şu an yok — bkz. bölüm 3.6)
  README.md
```

`data/` klasörü **tamamen pipeline tarafından üretilir.** Arayüz kodu bu dosyaların hiçbirini yazmaz, hiçbirini değiştirmez, içeriğine dair hiçbir varsayımı koda gömmez. Yeni bir yamaya geçildiğinde tek yapılacak şey bu klasörü değiştirmek olmalı.

## 3. Veri sözleşmesi (data contract)

Bu bölüm dokümanın en kritik parçası. Pipeline ile arayüz arasındaki tek bağlantı bu JSON şemaları — burada bir uyumsuzluk olursa arayüz **hata vermez, sessizce yanlış sonuç gösterir.** Şemalara birebir uy.

### 3.1 meta.json

Arayüzün ilk okuyacağı dosya. Vektör şemasını ve koşu bilgisini taşır.

```json
{
  "set_number": 18,
  "patch": "15.16",
  "regions": ["euw1", "na1"],
  "elo_range": "master+",
  "match_count": 24817,
  "board_count": 186204,
  "collected_at": "2026-08-28",

  "unit_order": ["TFT18_Aatrox", "TFT18_Ahri", "..."],
  "trait_order": ["TFT18_Sorcerer", "TFT18_Bruiser", "..."],
  "vector_includes_traits": true,
  "normalization": "l2",
  "match_threshold": 0.35
}
```

**`unit_order` ve `trait_order` — dikkat**

Bu iki dizi, pipeline'ın feature vektörünü hangi sırayla kurduğunu söyler. Arayüz vektörü **tam olarak bu sırayla** kurmak zorunda. Sırayı kendin belirleme, alfabetik sıralama, unit listesini `units.json`'dan türetme. Sadece bu diziyi kullan.

Sebep: pipeline Python, arayüz JavaScript. Aynı vektörü iki ayrı dilde bağımsız kurarsan bir gün sessizce kayar ve kullanıcı yanlış comp eşleşmesi görür, kimse fark etmez. Sıra tek bir yerde tanımlı olsun.

**`match_threshold`**

Kullanıcının board'u ile en yakın comp centroid'i arasındaki cosine mesafesi bu değeri aşarsa "bu board bilinen bir comp'a uymuyor" denir. Eşiği arayüzde sabit yazma, buradan oku.

### 3.2 units.json

Setteki tüm unit'ler. Palet ve trait hesabı bunu kullanır.

```json
{
  "TFT18_Ahri": {
    "name": "Ahri",
    "cost": 4,
    "traits": ["TFT18_Sorcerer", "TFT18_Wisp"],
    "icon": "https://raw.communitydragon.org/latest/game/assets/.../ahri.png"
  },
  "...": {}
}
```

### 3.3 traits.json

Trait breakpoint'leri. Arayüz aktif trait'leri bununla hesaplar.

```json
{
  "TFT18_Sorcerer": {
    "name": "Sorcerer",
    "breakpoints": [2, 4, 6, 8],
    "icon": "https://raw.communitydragon.org/latest/game/assets/.../sorcerer.png"
  },
  "...": {}
}
```

### 3.4 comps.json

Clustering çıktısı. Centroid vektörleri burada.

```json
{
  "c_07": {
    "name": "Ahri — Sorcerer",
    "centroid": [0.0, 0.31, 0.0, 0.22, "..."],
    "n": 4218,
    "avg_placement": 4.12,
    "top4_rate": 0.548,
    "core_units": ["TFT18_Ahri", "TFT18_Lux", "..."],
    "core_unit_avg_stars": {"TFT18_Ahri": 1.84, "TFT18_Lux": 2.71, "...": "..."},
    "common_traits": [["TFT18_Sorcerer", 6], ["TFT18_Wisp", 4]]
  },
  "_metadata": {
    "avg_placement_quartiles": {"q1": 4.271, "q2": 4.519, "q3": 4.682, "n_comps": 19}
  },
  "...": {}
}
```

`centroid` dizisi `unit_order` ile aynı uzunlukta olmalı. `core_units` **sabit
uzunlukta değil** — comp'taki board'ların en az %50'sinde görülen unit'ler,
5 ile 12 arasında sınırlı (bkz. `clustering.py`'deki `select_core_units`).
Arayüz uzunluğunu varsaymasın, ne gelirse onu göstersin.

`n_full` = HDBSCAN eğitim alt kümesinden (early_death hariç) bu comp'a düşen
board sayısı; `n_total` = early_death dahil atanan tüm board sayısı.
`avg_placement`/`top4_rate`/`core_units`/`common_traits` hepsi `n_total`
üzerinden hesaplanır, `n_full` hiçbir hesaba girmez — sadece eğitim
popülasyonunun büyüklüğünü gösterir (`n_total - n_full` = o comp'a atanan
erken-ölüm board sayısı).

> **`core_unit_avg_stars` (2026-09-01, ölçüldü, eklendi).** Her `core_units`
> ID'sini o unit'in gerçek ortalama yıldız seviyesine (float, [1.0, 3.0])
> eşler — anahtarlar `core_units` ile **birebir** aynı olmalı, `build_web_data.py`
> bunu doğruluyor. Ortalama, sadece unit'in GERÇEKTEN bulunduğu board'lar
> üzerinden hesaplanıyor (o comp'un tüm `n_total` board'u üzerinden değil —
> bir board'da olmayan bir unit'in yıldızı yoktur, ortalamaya girmez). Ölçüm
> gerekçesi (SPEC.md bölüm 9): sabit "2 yıldız" varsayımı 144 (cluster,
> core_unit) çiftinin sadece %13.2'sinde 2.0'a ±0.1 yakın çıktı — carry'ler
> bilinçli 3-yıldızlanıyor (2.9'a kadar), düşük öncelikli unit'ler (ör.
> `DA_Taric18`, göründüğü her cluster'da) 1'den kurtulamıyor. Bölüm 5.3'ün
> `loadSuggestedComp()`'u board'u artık bu gerçek değerle dolduruyor.
>
> **`_metadata` — normal bir comp anahtarı değil (2026-09-01, items.json'daki
> `_metadata` ile aynı desen).** `comps.json`'da her zaman bir `_metadata`
> anahtarı da bulunur: `avg_placement_quartiles` — 19 real comp'un
> `avg_placement`'ından `clustering.py`'nin her koşuda yeniden hesapladığı
> çeyreklikler (`q1`/`q2`/`q3`) + kaç comp'tan hesaplandığı (`n_comps`).
> Bölüm 8'in meta-tier rozeti için — sınırlar arayüz koduna hiç gömülmüyor,
> `app.js` bunları buradan okuyup her comp'un `avg_placement`'ını bu üç
> sınıra karşı sınıflandırıyor. `off_meta` gibi `c_NN` kalıbına uymuyor,
> `comps.json`'ı dolaşan her kod yolu (`build_web_data.py`, `items.py`,
> `app.js`) bunu comp listesinden ayrı tutmalı — 2026-09-01'de bu üç dosyanın
> hepsinde denetlenip düzeltildi.

> **`off_meta` — normal bir comp anahtarı değil (2026-08-30, web build validator'ında bulundu, önceden belgelenmemişti).** `comps.json`'da her zaman bir `off_meta` anahtarı da vardır (HDBSCAN gürültüsünden ve early_death'ten hiçbir centroid'e yeterince yakın çıkmayan board'ların toplandığı kova — bkz. SPEC.md bölüm 9). Bu anahtarın değeri yukarıdaki şemadan **farklı ve daha dar**: sadece `n_full`, `n_total`, `avg_placement`, `top4_rate` var — `name`, `centroid`, `core_units`, `core_unit_avg_stars`, `common_traits` **yok**, çünkü off_meta gerçek bir comp değil. items.json'daki `_metadata` gibi alt çizgiyle başlamıyor (ismi `c_NN` kalıbına da uymuyor), o yüzden arayüz kodu bunu "bilinmeyen/bozuk comp" sanıp atlamamalı ama tam comp şeması da beklememeli. Palet/eşleştirme mantığı `comps.json`'ı dolaşırken `off_meta` anahtarını (varsa) comp listesinden ayrı tutmalı — bir centroid'i olmadığı için asla bir board'un "eşleştiği comp"u olamaz, sadece bölüm 7.3'ün "hiçbir comp'a uymuyor" yolunda referans istatistik olarak kullanılabilir.

### 3.5 matchups.json

Head-to-head sıralama oranları. Yetersiz örnekli hücreler pipeline tarafından zaten elenmiş olmalı, ama arayüz yine de `n` kontrolü yapsın.

```json
{
  "c_07": {
    "c_12": { "rate": 0.612, "n": 843, "ci": [0.578, 0.645] },
    "c_03": { "rate": 0.441, "n": 517, "ci": [0.399, 0.484] }
  },
  "...": {}
}
```

`rate` = c_07'nin, aynı lobide c_12 varken ondan **yukarıda bitirme oranı.** Savaş kazanma oranı değil. Bu ayrım kritik, bölüm 8'e bak.

> **İki hücre şekli var — üsttekinin bir varyantı, önceden belgelenmemişti (2026-08-30, web build validator'ında bulundu).** "Yetersiz örnekli hücreler zaten elenmiş olmalı" cümlesi yanıltıcı: `matchups.py` (SPEC.md bölüm 10) `n<100` çiftleri **silmiyor**, görünür tutup `{ "n": 91, "insufficient_data": true }` şeklinde işaretliyor — `rate`/`ci` alanları o hücrede hiç yok. Gerçek veride (2937 maçlık ilk örneklem) 484 çiftin 177'si (%36.6) bu ikinci
şekildeydi; 14513 maçlık koşuda (2026-08-31, 19 cluster'la) 361 çiftin
sadece 18'i (%5.0) — 5 kat veriyle beklendiği gibi belirgin şekilde düştü,
ama şema/davranış kuralı aynen geçerli. Arayüz her hücreyi okurken önce `insufficient_data === true` kontrolü yapmalı; true ise `rate`/`ci` okumaya çalışmadan "yetersiz veri" göstermeli (bölüm 8.1'in zaten istediği davranış — burada eksik olan şema tarafıydı, davranış tarafı değil).

### 3.6 augments.json

> **v1'de bu dosya üretilmiyor, sekme de yok.** SPEC.md bölüm 11: augment analizi askıya alındı — Riot API'nin katılımcı nesnesinde `augments` alanı hiç yok (0/23984, örneklemin tamamı tarandı). Bu bir "veri henüz gelmedi" durumu değil, yapısal bir eksiklik; kısa vadede düzelmesi beklenmiyor. Aşağıdaki şema, Riot bir gün bu alanı doldurmaya başlarsa diye **referans olarak** tutuluyor. Arayüz kodu `augments.json`'ın var olmadığını varsaymalı — bu dosyayı `fetch` etmeye çalışmamalı, augment sekmesini hiç göstermemeli (bkz. bölüm 8, 11).

```json
{
  "c_07": {
    "silver": [{ "id": "TFT18_Augment_...", "name": "...", "lift": -0.34, "n": 212 }],
    "gold": ["..."],
    "prismatic": ["..."]
  },
  "...": {}
}
```

`lift` = comp'un kendi ortalama placement'ına göre fark, shrinkage uygulanmış. Negatif değer iyi (daha düşük placement = daha iyi sıralama). Arayüzde bunu kullanıcıya doğru yönde göster — negatifi "kötü" gibi renklendirme, TFT'de küçük sayı iyidir.

### 3.7 comp_traits.json

```json
{
  "c_07": {
    "strong_against": [{ "trait": "TFT18_Bruiser", "delta": -0.41, "n": 1904 }],
    "weak_against": [{ "trait": "TFT18_Sniper", "delta": 0.55, "n": 1622 }]
  },
  "...": {}
}
```

`delta` = o trait rakip board'larda aktifken comp'un ortalama placement'ının genel ortalamasından farkı.

### 3.8 items.json

```json
{
  "_metadata": {
    "known_limitation": "...",
    "coverage": {
      "total_cluster_unit_pairs": 144,
      "pairs_with_at_least_1_item": 138,
      "pairs_with_full_3_items": 129,
      "cells_below_min_n": 15117,
      "cells_total": 17574,
      "cells_below_min_n_fraction": 0.86
    }
  },
  "c_07": {
    "TFT18_Ahri": [{ "id": "TFT18_Item_JeweledGauntlet", "name": "Jeweled Gauntlet", "lift": -0.34, "n": 212, "icon": "https://raw.communitydragon.org/latest/game/assets/.../jeweledgauntlet.png" }],
    "...": []
  },
  "...": {}
}
```

Item **vektörün bir parçası değil** — comp tanımı (bölüm 3.4'teki `centroid`) item'dan bağımsız, aynı comp'u oynayan iki oyuncu farklı item taşıyabilir. Bu dosya ayrı bir katman: her comp için, o comp'un en sık oynanan unit'lerinden (`comps.json`'daki `core_units`) her biri üzerinde en iyi 3 item, `lift` ve `n` ile. `lift` = augment'lerdeki gibi comp'un kendi ortalama placement'ına göre fark, shrinkage uygulanmış; negatif iyi. Sadece tamamlanmış item'lar var, component'ler yok. Bir unit için `n < 50` olan hiçbir hücre bu listeye girmez (tamamen dışlanır, boş liste `[]` görebilirsin — "veri yok" demektir, "kötü item" demek değil).

> **`icon` alanı sonradan eklendi (2026-08-30, item önerileri görselleştirilirken bulundu).** Bu dosyanın kaynağı `items.py` (SPEC.md bölüm 15) hiç `icon` yazmıyordu — sadece `id`/`name`/`lift`/`n`. İkon URL'i asıl `data/static/items.json`'da duruyor (`static_data.py`'nin diğer her şeyle aynı `_icon_url()` türetmesiyle, bölüm 4), ama o dosya `web/data/`'ya hiç kopyalanmıyordu. `build_web_data.py`'nin `enrich_items_with_icons()`'ı artık her `items.json` girdisini `id` üzerinden `data/static/items.json`'a karşı eşleyip `icon` alanını ekliyor — arayüzün ayrı bir statik katalog dosyası çekmesine gerek kalmıyor, her öneri kendi ikonunu taşıyor. URL deseni canlı doğrulandı: 10 rastgele tamamlanmış item, 10/10 → 200.

**`_metadata` — normal bir comp anahtarı değil.** Comp anahtarları her zaman `c_NN` biçiminde; `_metadata` bunun dışında, alt çizgiyle başlıyor ki karıştırılmasın. `known_limitation`: bu dosyadaki `lift`'in ciddi bir confounding taşıdığını açıklayan sabit bir metin — bir oyuncunun bir item'ı tamamlayabilmiş olması zaten iyi giden bir maçla ilişkili, yani ölçülen kısmen "item'ı toplayabilmiş olmak", item'ın kendi katkısı değil. **Arayüz bu metni kullanıcıya göstermeli** (ör. bir tooltip/uyarı ikonuyla) — `lift`'i temiz bir nedensellik gibi sunma. `coverage`: `n<50` eşiğinin kaç (comp, core_unit) çiftini tamamen boş bıraktığını, kaçını tam 3 item ile doldurduğunu gösterir — bir unit için boş `[]` dönmesi normal olabilir, `coverage` bunun ne kadar yaygın olduğunu açıklar.

## 4. Görseller

Unit ve trait ikonlarını **repoya elle yükleme.** Görsel URL'leri pipeline tarafından `units.json` / `traits.json` içine yazılacak, arayüz oradan okuyup doğrudan CommunityDragon CDN'inden çekecek.

Bunun üç sebebi var: set değişince tek satır kod değişmez, repo şişmez, ve Riot'ın görsellerini kendi sunucunda barındırmamış olursun.

**URL türetme (pipeline tarafı, ama burada belgeleniyor)**

CommunityDragon TFT dump'ında her unit ve trait için bir icon alanı var, içinde oyun içi asset yolu bulunur (`ASSETS/Characters/...` biçiminde, `.dds` veya `.tex` uzantılı). Web URL'sine çevirmek için: yolu tamamen küçük harfe çevir, uzantıyı `.png` yap, başına CommunityDragon game asset kökünü ekle.

> **Doğrula:** Bu dönüşümü varsayma. İki-üç unit için üretilen URL'yi gerçekten iste, 200 döndüğünü ve görselin doğru unit olduğunu gör. Bazı asset yollarında uzantı veya klasör yapısı beklenenden farklı olabiliyor; sessizce kırık görsel dönmesindense erken yakalanmalı.

**Arayüz tarafı davranış**

- Her görsele `onerror` ile yedek davranış koy: görsel yüklenmezse unit adının ilk harflerini içeren bir kutu göster. Kırık görsel ikonu gösterme.
- `loading="lazy"` kullan — palette 60+ görsel var.
- Unit kartının kenarlığını cost rengiyle boya (1: gri, 2: yeşil, 3: mavi, 4: mor, 5: altın). Renkleri koda gömebilirsin, cost sistemi setler arası değişmiyor.

## 5. Ekran yapısı

**Bu bölüm 2026-08-30'da baştan yazıldı — ilk sürümün üç eşit sütunlu yerleşimi terk edildi.** Eski tasarım (sol palet / orta board / sağ sonuç, üçü de aynı dikey şeritte) uygulandı ve çalışıyordu; terk edilme sebebi bir hata değil, TFT'nin kendi görsel diline daha yakın, oyunumsu bir tasarıma geçme kararı. Bu bölümün eski hali artık geçersiz.

Masaüstü öncelikli kalıyor (bölüm 11: mobil optimizasyonu yok).

### 5.0 Genel yerleşim

**Bu bölüm 2026-08-30'da ikinci kez değişti — sağ sonuç sütunu ızgaradan çıkıp kayan bir panele dönüştü** (ayrıntı bölüm 5.5). Aşağıdaki diyagram güncel hali:

```
+--------------------------------------------------------------------+
| [altın spatula ikonu] TFT Comp Analyzer                            |  <- header
+----------------+---------------------------------------------------+
|  ÖNERİLEN      |                                                    |
|  COMP'LAR      |                    BOARD                           |
|  (dar, dikey,  |                (geniş — artık tüm kalan genişlik)  |
|  genişleti-    |                                                    |
|  lebilir)      +----------------------------------------------------+
|                |               Aktif trait paneli                   |
+----------------+----------------------------------------------------+
|                      UNIT PALETİ (tam genişlik, yatay)               |
| [1]                                                                  |
| [2]   <- dikey maliyet sekmeleri         unit'ler yatay dizili       |
| [3]                                                                  |
| [4]                                                                  |
| [5]                                                                  |
+------------------------------------------------------------------+

Ekranın sağ kenarında her zaman dikey bir "‹" sekmesi durur. Tıklanınca
SONUÇ paneli sağdan içeri kayar — board'un ÜSTÜNE biner (overlay),
yerleşimi sıkıştırmaz:

+----------------+---------------------------------------+-----------+
|  ÖNERİLEN      |                                     ░░│  SONUÇ    │
|  COMP'LAR      |         BOARD (aynı yerde durur)   ░░│  (overlay,│
|                |                                     ░░│  kendi    │
|                +-------------------------------------░░│  içinde   │
|                |        Aktif trait paneli           ░░│  kayar)   │
+----------------+---------------------------------------+-----------+
```

Dört bölge + bir overlay:

| Bölge | İçerik |
| --- | --- |
| Header | Altın renkli spatula ikonu + "TFT Comp Analyzer" başlığı, sağında sonuç panelini açan "‹"/"›" sekmesi. |
| Sol sütun | Önerilen comp'lar paneli — bölüm 5.3. |
| Orta (kalan tüm genişlik) | Board (bölüm 5.1) + altında aktif trait paneli (bölüm 7.1). Artık sağda sabit bir sütun olmadığı için tüm kalan genişliği kullanır. |
| Sağ kenar (overlay, varsayılan kapalı) | Sonuç paneli — bölüm 5.5, içerik bölüm 8; dil kuralı bölüm 8.2. |
| Alt (tam genişlik) | Unit paleti — dikey değil yatay bir panel. Bölüm 5.2/5.4. |

> **Sabit viewport kuralı — tamamen terk edildi (2026-08-31, gerçek bir sıkışma hatasına yol açtığı bulundu).** Önceki hal (2026-08-30, aşağıdaki eski metin) `body`'yi `height: 100vh` ile sabitleyip önerilen-comp'lar/board ızgarasını (`flex: 1 1 auto; min-height: 0`) tek esneyen bölge yapıyordu; palet, kısıtlar kutusu ve footer ise `flex: 0 0 auto` ile hiç küçülmüyordu. Bu dört sabit bölgenin toplam doğal yüksekliği 100vh'a yaklaştığında/aştığında, ızgaranın payına kalan yükseklik `min-height:0` sayesinde neredeyse 0'a kadar küçülüyordu — board sütunu palet ile kısıtlar kutusu arasında birkaç piksele sıkışıyor, `overflow-y:auto` teknik olarak devredeydi ama görünür alan tek bir unit kartının (128px) bile altına düşüyor, kartların altı kesiliyordu. `body`'nin "son çare" scroll'u hiç devreye girmiyordu çünkü flex-shrink açığı zaten kapatıyordu — body'nin toplam içerik yüksekliği hiçbir zaman 100vh'ı geçmediği için tarayıcı scroll'a gerek görmüyordu, içerik sadece görünmüyordu. Canlı ekran görüntüsüyle doğrulandı (headless Chrome, 1400×2400 pencere): eski haliyle board sütunu ve palet sıkışık, düzeltmeden sonra board'un 10 slotu ve paletteki tüm unit kartları (portre + trait + isim + maliyet) tam yükseklikte, kesilmeden görünüyor.
>
> Düzeltme: `body`'den `height`/`overflow-y`/flex-column zorlaması tamamen kaldırıldı (`min-height: 100vh` yeterli), `.layout-grid`'den `flex: 1 1 auto; min-height: 0` ve `grid-template-rows: minmax(0, 1fr)` kaldırıldı, `.side-column`/`.board-column`'dan `min-height: 0; overflow-y: auto` kaldırıldı. Sayfa artık doğal içerik yüksekliğine göre büyüyor, taştığında **sayfa düzeyinde** dikey scroll ediyor — iç içe küçülen flex/grid kutuları yok. Sonuç panelinin `position: fixed` overlay yapısı (yukarıdaki paragraf) değişmedi, zaten bu sorundan hiç etkilenmiyordu. Palet genişletildiğinde kendi `max-height: 45vh; overflow-y: auto`'sunu koruyor — bu, squeeze hatasıyla ilgisiz, ayrı ve kasıtlı bir tasarım kararı (bkz. 5.4).
>
> *Eski metin (2026-08-30, artık geçerli değil, referans için tutuluyor):* "Sonuç panelinin overlay'e taşınmasıyla kısmen kendiliğinden çözüldü ... Geriye kalan iki bölge (önerilen comp'lar, board) için kural aynen geçerli: `body` dikey flex ve `height: 100vh` ... Çok kısa bir ekranda gerçekten sığmıyorsa `body` son çare olarak kendi kaymasına izin veriyor." Bu "son çare" hiç çalışmıyordu — yukarıya bakın.

> **İkinci bulgu (2026-08-31): `align-items: start` sıkışmayı çözdü ama yeni bir boşluk sorunu açtı — CSS Grid'in auto-satır boyutlandırması `align-items`'tan bağımsız çalışıyor.** Yukarıdaki düzeltme board'un sıkışmasını gerçekten çözdü, ama board ile alttaki unit paleti arasında ~120-160px boş alan bıraktı (ölçüldü: headless tarayıcıda `getComputedStyle(.layout-grid).gridTemplateRows`, `.side-column`'un [8 önerilen comp'lu] doğal yüksekliğiyle birebir aynı çıktı, üç board durumunda da [boş/5/10 unit] `.board-column` bundan 124-160px kısaydı). Sebep CSS Grid'in az bilinen bir kuralı: bir satırın `auto` yüksekliği, o satırdaki öğelerin `align-items`'tan tamamen bağımsız, salt içerik boyutuna (max-content) göre kuruluyor — `align-items: start` sadece KISA öğeyi o zaten uzun satırın içinde üste hizalıyor, satırın kendisini kısaltmıyor. Pure-CSS'te bunu çözecek bir yol yok (bir grid satırını "en kısa öğeye göre kur" diye ayarlayamazsınız). Düzeltme: `app.js`'e `.board-column`'u izleyen bir `ResizeObserver` eklendi (`setupSideColumnSync`), `.side-column`'un `max-height`'ini board'un GERÇEK render yüksekliğine eşitliyor — board hangi durumda olursa olsun satırı O belirliyor, `.side-column` taşarsa (bkz. `style.css`'teki `overflow-y: auto`) kendi içinde scroll ediyor. `ResizeObserver` seçildi (window `resize` event'i değil) çünkü board-column'un boyutu pencere resize dışında da değişebiliyor (board render, font yüklenmesi) ve `ResizeObserver` ilk gözlemde kendiliğinden bir kere tetiklendiği için ayrı bir "ilk render'da çağır" satırına gerek kalmıyor. Ölçüldü (headless tarayıcı, üç board durumu): boşluk 124-160px'ten **16px'e** düştü (kalan 16px `.layout-grid`'in kasıtlı `padding-bottom: 1rem`'i, ayrı bir sorun değil).

**Tema:** Koyu arka plan, altın ve mor vurgu renkleri, TFT'nin oyun içi kart estetiğine yakın (gölgeli kartlar, belirgin çerçeveler). Kesin renk kodları CSS'te — spec sadece yönü belirtiyor, bir palet dayatmıyor.

### 5.1 Board — hex ızgara YAPMA

Değişmedi. TFT'nin gerçek altıgen dizilim ızgarasını kurma. Analiz pipeline'ı pozisyon verisi kullanmıyor — çünkü Riot API pozisyon vermiyor. Hex ızgara yaparsan kullanıcıya **dizilimin sonucu etkilediği izlenimini vermiş olursun**, ki etkilemiyor. Düz bir 10 slotluk sıra yeterli ve dürüst.

### 5.2 Unit kartı — tasarım ve kontroller

**Tasarım (2026-08-30, yeni — referans: TFT'nin kendi kart stili).** Bu tasarım hem palette hem board'daki dolu slotlarda aynı temel karta uygulanır:

- Unit portresi kartın **arka planı** — `units.json`'daki `icon` URL'i (bölüm 4).
- Sol altta, dikey sıralı, küçük trait ikonları — her ikonun yanında trait adı. Palette'te unit'in kendi (statik) trait listesi gösterilir; board'a özgü aktif-tier hesabı burada değil, aktif trait panelinde (bölüm 7.1).
- Alt şeritte solda unit adı, sağda maliyet (altın bir ikon + sayı).
- Kartın çerçevesi maliyet rengiyle boyanır (bölüm 4'teki 5 renk: 1 gri, 2 yeşil, 3 mavi, 4 mor, 5 altın).

> **Dikkat — `onerror` geri düşüşü CSS `background-image` ile çalışmaz.** Bölüm 4'ün zorunlu kıldığı kırık-görsel yedeği (`onerror` ile unit adının baş harflerini gösteren kutu) sadece bir `<img>` elementinin `error` olayıyla tetiklenir; CSS `background-image` için böyle bir olay yok. Portreyi arka plan gibi GÖSTERMEK için bile, altyapıda gerçek bir `<img>` (üstüne `object-fit: cover` ile tam kart boyutunda) kullan, salt CSS `background-image` değil — yoksa kırık görsel durumunda hiçbir şey tetiklenmez ve bölüm 4'ün kuralı sessizce delinmiş olur.

**Kontroller (değişmedi):**

- Yıldız seviyesi seçici (1 / 2 / 3). Varsayılan 2 — veride en sık görülen durum bu.
- Kaldırma butonu (üst köşede x). Sürükleyip dışarı atma da çalışsın ama tek yol olmasın (bölüm 6.1).
- Aynı unit board'a iki kez eklenemez. Palette'te zaten eklenmiş unit'i soluklaştır.

### 5.3 Önerilen comp'lar paneli (yeni)

Sol sütun. `comps.json`'dan (`off_meta` hariç — centroid'i/core_units'i yok, önerilemez) **ortalama placement'a göre en iyi 8 comp**, düşükten yükseğe sıralı (TFT'de küçük placement iyidir — bölüm 10'un "düşük placement'ı kötü renklendirme" hatasına düşme). 8, spec'in verdiği "5-10" aralığından seçilmiş sabit bir sayı; değiştirmek istenirse tek satır.

Her satırda: comp adı, ortalama placement, top4 oranı, `n` (`n_total`). Panelin kendisi genişletilebilir/daraltılabilir (`<details>` yeterli, JS gerekmez).

**Bir satıra tıklanınca:**
1. **Board doluysa bile doğrudan, onay istemeden yüklenir.** İlk taslak burada bir `confirm()` onayı öngörüyordu ("mevcut board silinecek, devam edilsin mi?"); kullanıma alınınca rahatsız edici bulundu ve kaldırıldı (2026-08-30) — comp'lar arası hızlı geçiş, sık sık "evet"e tıklamayı gerektirmeyecek şekilde akıcı olmalı. Board tamamen temizlenir, sonra o comp'un `core_units` listesindeki her unit eklenir. `core_units` 10'dan uzunsa (bölüm 3.4: 5-12 arası olabilir), listenin **ilk 10'u** alınır — `clustering.py`'nin `select_core_units`'i bu listeyi zaten sıklığa göre sıralı üretiyor, yani ilk 10 o comp'un en karakteristik unit'leri olur. Kesilen unit varsa kullanıcıya kısaca söyle ("N unit sığmadı, en az sık görülenler bırakıldı" gibi).
   > **Yıldız seviyesi artık sabit 2 değil (2026-09-01, kullanıcı kararı — ölçüldü, bkz. bölüm 3.4'ün `core_unit_avg_stars` notu).** Her core unit, o comp'taki GERÇEK ortalama yıldız seviyesine (`core_unit_avg_stars[unitId]`) yuvarlanarak ekleniyor — sabit varsayımın (144 çiftin sadece %13.2'si 2.0'a yakın) yerini aldı. Yuvarlama: standart "yarımdan yukarı" (`Math.round` — 2.56→3, 2.49→2); sonuç `[1, 3]`'e clamp'leniyor (ortalama tanım gereği zaten bu aralıkta, clamp sadece savunma amaçlı). Board'u elle kuran kullanıcının tek tek eklediği unit'ler (bölüm 6.1) bundan etkilenmiyor — DEFAULT_STAR (2) orada aynen geçerli, çünkü elle kurulan bir board'un hangi "comp" bağlamında olduğu bilinmiyor, ortalama alınacak bir referans yok.
2. Bu, board'u tek tek `addUnitToBoard` ile doldurmaktan **farklı** bir yol — "board dolu" uyarısı (bölüm 6.2) burada devreye girmez, çünkü toplu bir değiştirme işlemi.
3. Sonuç paneli normal akışla (bölüm 7.3) yeniden hesaplanır — comp'u zorla göstermek yerine `nearestComp` ne bulursa o gösterilir. Çoğu zaman aynı comp'a geri döner (`core_units` zaten o comp'un en sık board'larından türetildi) ama bu **garanti değil** (`core_units` bir frekans özeti, centroid'in kendisi değil) — arayüz bunu farklı bir comp ya da "uymuyor" olarak gösterirse bu bir hata değil, dürüst bir sonuçtur.

### 5.4 Unit paleti — yatay panel (yeni)

Artık dikey bir sütun değil, sayfanın altında tam genişlikte yatay bir panel. Kendisi genişletilebilir/daraltılabilir; genişletilince unit sırası birden fazla satıra sarar, daraltılmışken tek (yatay kaydırılabilir) satır yeterli.

**Sol tarafta dikey maliyet sekmeleri: 1, 2, 3, 4, 5.** Her sekme kendi maliyet renginde (bölüm 4/5.2'deki 5 renk). Bir sekmeye tıklamak paleti **sadece o maliyetteki unit'lere** daraltır — birden fazla maliyet aynı anda seçilemez, sekmeler birbirini dışlar. **Varsayılan açık sekme: 1 maliyet.**

> **Arama, maliyet sekmesini aşar (2026-08-30, kullanıcı kararı).** Önceki taslak sekme + arama + trait filtresini hep VE ile birleştiriyordu — bu bir kullanılabilirlik sorunu yaratır: "1 maliyet" sekmesi açıkken kullanıcı "Ahri" ararsa (4 maliyet) hiç sonuç çıkmaz, kullanıcı "Ahri" yokmuş sanabilir. Karar: **arama kutusunda metin varken maliyet sekmesi devre dışı kalır** — sonuçlar tüm maliyetlerden gelir. Arayüz bunu görsel olarak da yansıtmalı: arama sürerken sekme butonlarının hiçbiri "seçili" görünmemeli (aktif sekme vurgusu kalkar), arama temizlenince en son seçili sekmeye geri dönülür. **Trait filtresi bundan etkilenmiyor** — aramayla birlikte hâlâ VE mantığıyla çalışır (ör. arama="ah" + trait="Sorcerer" → tüm maliyetlerden, adında "ah" geçen VE Sorcerer trait'i olan unit'ler).

### 5.5 Sonuç paneli — kayan panel (2026-08-30, sütundan overlay'e)

Sonuç paneli artık üç-sütunlu ızgaranın bir parçası değil, `position: fixed` bir overlay:

- **Sekme:** ekranın sağ kenarında her zaman görünen, dikey (dar, uzun) bir "‹" butonu — kapalıyken de görünür, board boş olsa veya hiçbir comp eşleşmese bile. Tıklanınca panel sağdan içeri kayar, buton "›" olur; tekrar tıklanınca panel kayarak kapanır, buton "‹"ya döner.
- **Konum ve davranış:** panel `position: fixed; right: 0; height: 100vh`, kapalıyken `transform: translateX(100%)` ile ekranın dışında, açılınca `translateX(0)`'a geçiş animasyonuyla kayar. Board'un **üstüne biner** (yüksek `z-index`) — açılması board'un veya sol sütunun genişliğini değiştirmez, sıkıştırmaz.
- **İçerik:** bölüm 8'in tarif ettiği sonuç paneli içeriğinin **aynısı** — comp adı + mesafe, bölüm 8.2'nin sade cümleleri, head-to-head, trait analizi, bölüm 8.3'ün ikon tabanlı item önerileri. Panel kendi içinde kayar — bölüm 5.0'ın sabit-viewport kuralına bu yüzden zaten uyuyor, ayrıca bir önlem gerekmiyor.
- **Buton her zaman aktif, panel her zaman güncel.** Board boşken veya `nearestComp` hiçbir comp'a eşleştirmediğinde de sekme tıklanabilir kalır; panel açılınca bölüm 7.3'ün ilgili mesajını gösterir ("board'a en az 4 unit ekleyin" / "bilinen bir comp'a uymuyor" + en yakın 3 comp). Panelin açık/kapalı durumu ile içeriğin güncelliği birbirinden bağımsız — board her değiştiğinde içerik hesaplanır (panel görünür olsun olmasın), sekmeye tıklamak sadece görünürlüğü değiştirir.

> **Uygulama tuzağı — `overflow-y` sekmeyi görünmez kılar (2026-08-30, bulundu ve düzeltildi).** İlk uygulama scroll'u doğrudan dış panele (`overflow-y: auto`) koymuştu; sekme (panelin sol kenarından `left:-28px` ile dışarı taşan bir çocuk element) tamamen kayboldu. Sebep CSS'in az bilinen bir kuralı: bir eksenin `overflow`'u `visible` dışında bir şeye ayarlanınca, öteki eksen `visible` olsa bile `auto`'ya döner — yani `overflow-y: auto` tek başına `overflow-x`'i de kırpar. Düzeltme: dış konteyner (`#result-panel`) `overflow: visible` (varsayılan) kalıyor, scroll'u ayrı bir iç wrapper (`.result-panel-scroll`, `height:100%; overflow-y:auto`) üstleniyor. Sekme dış konteynerin çocuğu olduğu için artık kırpılmıyor. Ekran görüntüsüyle doğrulanmadan (DOM'da elementin var olması yetmiyor, CSS'in onu gerçekten göstermesi gerekiyor) bu fark edilmezdi.

### 5.6 Terk edilen tasarımlar — karar geçmişi

Üç eşit sütunlu ilk yerleşim (palet/board/sonuç, hepsi dikey) bilinçli olarak terk edildi, bir hata düzeltmesi değildi — SPEC.md'nin "Ek: Karar geçmişi" bölümündeki mantıkla aynı gerekçeyle burada da not düşülüyor: gelecekte biri "neden palet artık yatay?" diye sorarsa cevap "TFT'nin görsel diline yaklaşmak", "eski tasarım bozuktu" değil.

**Sonuç sütunu (üç-sütunlu ızgara içinde) — terk edildi, overlay'e taşındı (2026-08-30).** Ara aşamada sonuç paneli üçüncü bir ızgara sütunuydu, kendi `overflow-y:auto`'suyla (bölüm 5.0'ın "sabit viewport kuralı"nın ilk hali). Bu çalışıyordu ama kullanımda board'a ayrılan genişliği daralttığı ve palet dahil sayfanın geri kalanını sıkıştırdığı görüldü. Sağ kenarda kayan bir overlay'e taşınması hem bu sıkışmayı ortadan kaldırdı (board artık kalan tüm genişliği kullanıyor) hem de eski "sabit viewport" düzeltmesini o panel için gereksiz kıldı (overlay zaten sayfa akışının dışında).

## 6. Sürükle-bırak

Bu bölüm sadece board'a unit yerleştirme/çıkarma etkileşimini kapsar (bölüm 5.1/5.2). Önerilen comp'lar panelinin "tıkla, board'u toplu doldur" davranışı (bölüm 5.3) ayrı bir mekanizma — sürükle-bırak değil, tek tıkla topluca doldurma; aşağıdaki kurallar ona uygulanmaz.

Tarayıcının yerleşik HTML5 drag & drop API'si (`draggable`, `dragstart`, `drop`) bu iş için yeterli. Kütüphane ekleme.

### 6.1 Zorunlu: tıklama alternatifi

Sürükle-bırak **tek giriş yolu olmayacak.** Palette bir unit'e tıklamak onu board'a eklesin, board'daki unit'e tıklamak (veya x'ine basmak) çıkarsın. Bu bir "nice to have" değil: yerleşik DnD dokunmatik ekranlarda çalışmaz, klavyeyle erişilemez, ve trackpad'de sürükleme sinir bozucudur. Arkadaşlarından biri tablet'ten açtığında site kullanılamaz olmasın.

### 6.2 Uygulama notları

- `dragstart` içinde `dataTransfer.setData("text/plain", unitId)` ile unit ID'sini taşı.
- `dragover` handler'ında mutlaka `e.preventDefault()` çağır — bunu unutmak drop'un hiç tetiklenmemesinin en yaygın sebebi.
- Board dolu (10 unit) iken bırakma denemesi sessizce başarısız olmasın, kısa bir uyarı göster.
- Sürükleme sırasında hedef slotu görsel olarak vurgula.

## 7. İstemci tarafı hesaplamalar

### 7.1 Aktif trait hesabı

Board her değiştiğinde yeniden hesapla:

- Board'daki her unit'in traits listesini topla.
- Her trait için benzersiz unit sayısını bul. Aynı unit iki kez sayılmaz — yıldız seviyesi trait sayımını etkilemez.
- `traits.json`'daki breakpoint'lerle karşılaştır, ulaşılan en yüksek eşiği aktif tier olarak al.
- Aktif trait'leri ikon + "3/4" biçiminde göster. Aktif olmayan ama 1 eksikle aktifleşecek olanları soluk göster — kullanıcıya faydalı.

> **v1 kapsam dışı:** Emblem ve spatula item'ları trait ekleyebiliyor. Bunu v1'de destekleme, item girişi zaten yok. README'de kısıt olarak yaz.

### 7.2 Board vektörü

`meta.json`'daki `unit_order` dizisini kullanarak vektörü kur:

```javascript
// unit_order sırasıyla, her unit için değer = yıldız seviyesi (yoksa 0)
const vec = meta.unit_order.map(id => board[id]?.star ?? 0);

// meta.vector_includes_traits true ise trait bileşenini ekle
if (meta.vector_includes_traits) {
  vec.push(...meta.trait_order.map(t => activeTraits[t] ?? 0));
}

// meta.normalization "l2" ise normalize et
const norm = Math.hypot(...vec);
const unit = norm > 0 ? vec.map(v => v / norm) : vec;
```

> **Kritik:** Bu vektörün pipeline'ın ürettiğiyle birebir aynı olması gerekiyor. Doğrulama yolu: pipeline birkaç örnek board'u ve onlar için hesapladığı vektörü ve eşleştiği comp'u bir test dosyasına yazsın (`data/_selftest.json`). Arayüz açılışta bunu okuyup kendi hesabıyla karşılaştırsın, tutmuyorsa konsola yüksek sesle hata bassın. Bu, projedeki en sinsi hata sınıfına karşı tek savunma.

#### 7.2.1 `_selftest.json`'ın kapsamı — ne test ediyor, ne test etmiyor

`_selftest.json`, `data/`'nin geri kalanı gibi rastgele board'lardan değil, **özenle seçilmiş** board'lardan oluşuyor — ve seçim kriteri arayüzü kuracak kişinin bilmesi gereken gerçek bir kısıt getiriyor.

**Neden seçici davranmak gerekti.** Board'un trait bileşeni Riot'un kendi `tier_current`'ı (bölüm 7.1, 8), ama bu değer bazen board'daki unit'lerin salt native üyeliğinden **daha yüksek** çıkıyor — SPEC.md bölüm 15'in belgelediği emblem etkisi bunun bilinen bir örneği (bir emblem, sahibi olmadığı bir trait'i "sanal üye" olarak ekliyor). v1 arayüzü item/emblem girişini desteklemediği için (bölüm 11), JS'in hesabı **her zaman** salt native unit sayımına dayanıyor. Gerçek bir maçtan alınan, item/emblem etkisi taşıyan bir board'u self-test'e koymak, doğru bir JS implementasyonunu haksız yere "FAIL" gösterirdi — bu yüzden `_selftest.json`'ı üreten `build_web_data.py`, her aday board için salt unit-üyeliğinden türetilen trait tier'larının gerçek `raw_traits` değeriyle **birebir** tuttuğunu önce doğruluyor; tutmayan board'lar fixture olarak hiç kullanılmıyor.

**Ölçülen oran, beklenenden çok daha büyük çıktı — açık söylüyoruz.** İlk ölçüm (2937 maçlık dev sample, `DA_18_Rival` düzeltmesinden sonraki hal): 23496 gerçek ranked board'un **12410'u (%52.8)** bu birebir-eşleşme testini geçiyor, **11086'sı (%47.2)** eleniyor. **15k koşusunda yeniden ölçüldü (2026-08-31):** 116104 board'un **61963'ü (%53.4)** geçiyor, **54141'i (%46.6)** eleniyor — oran neredeyse birebir korundu, 5 kat veriyle sistematik bir kayma yok. İkiye ayrıştırılmış hali (15k koşusu):

- **Lux'lu board'lar: %98.8 elendi (11283/11418; ilk ölçüm %99.0, 2479/2504).** Lux'ın "Avatar" mekaniği, hangi köken origin'ini kopyaladığına göre değişen bir trait veriyor; `static_data.py` onun 9 köken-varyantını (`unit_aliases.json`) tek bir kanonik unit'e (`DA_Lux18_Base`) topluyor ve bu sırada hangi origin'in kopyalandığı bilgisi kayboluyor — salt unit listesinden o origin trait'i geri türetilemiyor.
- **Lux'suz board'larda bile %40.9 elendi (42858/104686; ilk ölçüm %41.0, 8608/20992) — bileşenleri artık biliniyor.** `emblem_traits.json`'daki 21 bilinen emblem'in katkısı da sayıma katılınca oran (ilk ölçümde) **%41.0'dan %11.0'a** düşüyor — yani bu oranın büyük çoğunluğu zaten bilinen emblem etkisi (SPEC.md bölüm 15). *(Bu emblem-düzeltmeli alt kırılım 15k koşusunda yeniden hesaplanmadı — sadece üstteki toplam Lux'suz oran [%40.9] yeniden ölçüldü, ilk ölçümle tutarlı çıktığı için altındaki emblem-payı analizinin de benzer kaldığı varsayılıyor, doğrulanmadı.)* Kalan %11.0'lık dilim (ilk ölçüm) şu kaynaktan geliyor:
  - **Board/bench ayrımının API'de hiç olmaması (SPEC.md bölüm 8'in belgelediği kısıt, kabul edildi/düzeltilmedi).** `participant.units` board'da mı bench'te mi olduğunu söylemiyor; board'ların %7.2'sinde (15k koşusunda %8.2, SPEC.md bölüm 8) unit sayısı `level`'i aşıyor. Bunun kalıntı uyumsuzluğa katkısı düzensiz: kapasiteyi aşan board'larda kalıntı oranı %39, aşmayanlarda %12 — ama basit bir "fazlalığı sil" düzeltmesi denendi ve **kanıtla reddedildi** (uyumsuzluğu %39'dan %92'ye çıkardı, gerçek board unit'lerini bench sanıp siliyordu). Ayrıntı SPEC.md bölüm 8 ve "Ek: Karar geçmişi".
  - **`DA_18_Rival`'ın tekrarlanan breakpoint'i — düzeltildi (SPEC.md bölüm 4, 2026-08-30).** `extract_traits()` artık `minUnits`'i dedupe ediyor, Rival'ın breakpoints'i `[1, 1, 2]`'den `[1, 2]`'ye düzeldi. Etkisi ölçüldü: Rival'ın rol oynadığı mismatch sayısı 878'den 455'e düştü, ama **tek başına açıklayıcı olduğu board sayısı 206'dan sadece 4'e indi** — çünkü düzeltme öncesinin asıl sorunu (native=1 board'larda yanlışlıkla tier=2 hesaplanması, gerçek tier=1) artık yok. Geriye kalan 455 vaka, düzeltmenin **çözmediği ayrı bir bulgudan** geliyor: native=2 olan board'ların **%99.8'inde (478/479)** Riot'un gerçek `tier_current`'ı **3** — düzeltilmiş `[1, 2]` breakpoint listesiyle native=2'den hesaplanabilecek en yüksek tier 2, asla 3 değil. Rival'ın gerçek tier skalası basit breakpoint-sayımıyla hâlâ tam örtüşmüyor; kök nedeni araştırılmadı, SPEC.md bölüm 4'te ayrı bir çözülmemiş bulgu olarak duruyor.

**Bunun anlamı — ve anlamadığı şey.** Bu, arayüzün ya da self-test'in bir kusuru **değil**: kullanıcının sürükle-bırakla kurduğu bir board'da hiç item yok (v1 kapsamı), dolayısıyla salt unit sayımı o board için zaten **tanımı gereği doğru** hesap — %47'lik oran, "gerçek geçmiş maçlardan hangilerinin fixture olarak güvenilir olduğu" sorusuyla ilgili, "kullanıcının kurduğu board'ların %47'si yanlış hesaplanıyor" ile ilgili değil. Ama şunu da gösteriyor: item/emblem girişi ileride eklenirse (şu an bölüm 11'de kapsam dışı), aynı boşluk o zaman gerçek bir doğruluk sorunu haline gelir ve önce çözülmesi gerekir.

Elenen board'lar arasından, farklı comp'ları temsil edecek şekilde (cluster başına bir örnek) board seçilip `_selftest.json`'a yazılıyor — ilk ölçümde **23 board** (22 gerçek comp + bir "hiçbir comp'a uymuyor" [off-meta] örneği), 15k koşusunda (2026-08-31, 19 cluster'la) **20 board** (19 gerçek comp + off-meta). Sayı cluster sayısına bağlı, kod değişmedi.

### 7.3 Comp eşleştirme

Normalize vektör ile `comps.json`'daki her centroid arasında cosine mesafesi hesapla, en küçüğünü seç.

- Mesafe `meta.match_threshold` altındaysa: o comp'un tam raporunu göster.
- Üstündeyse: "Bu board bilinen bir comp'a uymuyor" mesajı ve en yakın 3 comp'u mesafeleriyle listele. Zorla bir comp'a eşleştirip rapor gösterme.
- Board'da 4'ten az unit varsa hiç eşleştirme yapma, "daha fazla unit ekle" de. Yarım board'lar her comp'a uzak çıkar ve anlamsız sonuç üretir.

> **En yakın 3 comp artık tıklanabilir (2026-08-31, kullanıcı kararı).** Liste artık düz metin değil — her satır bir `.comp-link` butonu. Tıklanınca bölüm 5.3'teki `loadSuggestedComp()` ÇAĞRILIYOR (aynı fonksiyon, aynı davranış): board o comp'un `core_units`'iyle dolar, sonuç paneli o comp'un tam raporunu gösterir. **Modal AÇMAZ** — bu, board'u değiştiren bir eylem, bölüm 8.4'ün head-to-head modalıyla (board'a dokunmadan peek) karıştırılmamalı. Canlı doğrulandı (headless tarayıcı): uymayan bir board kurulup en yakın comp'a tıklandı, board o comp'un core_units'iyle doldu, sonuç paneli o comp'un raporuna geçti.

## 8. Sonuç paneli

**Sıra 2026-08-31'de değişti — item önerileri en üste taşındı (kullanıcı kararı: görsel olması ve ilgi çekmesi nedeniyle).** Yukarıdan aşağıya güncel sıra:

| Bölüm | İçerik ve kural |
| --- | --- |
| Comp kimliği | Comp adı, eşleşme mesafesi, örnek sayısı (n). Mesafe eşiğe yakınsa "zayıf eşleşme" rozeti. Adın yanında meta-tier rozeti (Güçlü/Ortalama üstü/Ortalama altı/Zayıf — bölüm 8.5). Panelin başlığı, sıralamanın parçası değil — her zaman en üstte. |
| Item önerileri | Her core unit için ikon + önerilen item ikonları. Görsel, cümle değil — bölüm 8.3. |
| Temel istatistik | Ortalama placement ve top-4 oranı. n mutlaka görünsün. |
| Head-to-head | En iyi 5 ve en kötü 5 eşleşme. Her satırda oran, n ve güven aralığı. Karşı taraf comp isimleri kalın, tıklanabilir buton (`.comp-link`) — tıklanınca bölüm 8.4'ün modalı açılır. |
| Trait analizi | Zorlandığı 5 ve rahat ettiği 5 trait, delta ve n ile. |
| Kısıtlar | Sayfanın altında sabit duran, kapanabilir bir kutu. İçeriği bölüm 9'da. |

*Eski sıra (2026-08-30'a kadar): comp kimliği → temel istatistik → head-to-head → trait analizi → item önerileri (en altta). `app.js`'in `renderCompReport`'unda `appendChild` sırası değişti, section 8.1-8.3'ün kuralları (n zorunlu, dil, görsel gösterim) değişmedi.*

> **Augment sekmesi v1'de yok.** İlk taslakta burada bir "Augment: silver / gold / prismatic" satırı vardı; kaldırıldı çünkü `augments.json` üretilmiyor (bölüm 3.6, SPEC.md bölüm 11 — Riot API'de `augments` alanı hiç yok). Veri bir gün gelirse bu sekme geri eklenir, şema referans olarak bölüm 3.6'da duruyor.

> **"Yakın" eşiği — sayısal olarak tanımlandı (2026-08-30, uygulama sırasında bulundu).** Bu bölüm "mesafe eşiğe yakınsa" diyor ama bir sayı vermiyordu; uygulama bunu belirsiz bırakamadığı için bir sabit seçti: **mesafe, `match_threshold`'un %90'ından fazlaysa** "zayıf eşleşme" rozeti gösterilir (`app.js`'te `WEAK_MATCH_RATIO = 0.9`). Bu veriden türetilmiş bir değer değil, keyfi ama açık bir seçim — mesafe eşiğe yaklaştıkça, board gerçekte comp'un tipik örneklerinden gitgide uzaklaşan bir board olabilir, kullanıcı bunu bilsin diye. Değiştirmek istenirse tek satır (`app.js`'teki sabit).

### 8.1 Her yerde geçerli kurallar

- Hiçbir oran n olmadan gösterilmeyecek.
- Yetersiz veri olan hücre gizlenmeyecek, "yetersiz veri" yazılacak. Gizlemek, kullanıcıya var olmayan bir tamlık hissi verir.
- Güven aralığı geniş olan sonuçları görsel olarak zayıflat (soluk renk veya "düşük güven" etiketi).

> **"Geniş" eşiği — sayısal olarak tanımlandı (2026-08-30, uygulama sırasında bulundu).** Yukarıdaki kural da bir sayı vermiyordu. Uygulama seçimi: **güven aralığının üst-alt genişliği 0.15'i (15 yüzde puanı) aşarsa** "düşük güven" olarak zayıflatılır (`app.js`'te `WIDE_CI_WIDTH = 0.15`). Bu da veriden türetilmedi, keyfi bir sabit — Wilson aralığının genel olarak ne kadar geniş çıktığına dair bir dağılım analizi yapılmadı. Değiştirmek istenirse tek satır.

### 8.2 Dil sadeleştirme (2026-08-30 — yerleşim değişikliğiyle birlikte eklendi)

Sonuç panelinin **içeriği** (yukarıdaki tablo, hangi bölümler var) değişmedi — değişen **sunuluş biçimi.** Ham sayı yığını yerine cümle:

- ~~"Ortalama placement: 4.18 · Top4 oranı: %57.3 · n=1245"~~
- **"Bu comp ortalama 4.18. sırada bitiriyor, oyunların %57'sinde ilk 4'e giriyor (1245 örnek)."**

Sayılar kaybolmuyor — bölüm 8.1'in "hiçbir oran n olmadan gösterilmeyecek" kuralı aynen geçerli, `n` cümlenin içine gömülü ama hâlâ orada. Aynı ilke head-to-head ve trait analizinde de geçerli: tablo satırı yerine cümle, ör. "X comp'una karşı aynı lobide %62 oranında daha yüksek sırada bitiriyor (843 maçta)." Veri yetersizse ("yeterli veri yok", bölüm 8.1) bunu da düz bir cümleyle söyle — boş alan veya sadece bir tire bırakma.

**Bölüm 9'un dil kuralı hiç gevşemedi.** Cümleye çevirirken "üstünlük" dışında bir kelime (özellikle "kazanıyor", "yeniyor", "daha güçlü") sızmasın diye dikkat et — amaç jargonu azaltmak, veri iddiasını büyütmek değil.

### 8.3 Item önerileri — görsel gösterim (2026-08-30, bölüm 8.2'nin istisnası)

Item önerileri bölüm 8.2'nin "cümle, ham sayı değil" kuralına **girmiyor** — burada metin yerine görsel kullanılıyor, referans TFT'nin kendi item gösterimi (bölüm 5.2'nin unit kartı tasarımıyla aynı ruh):

- Her core unit (`comps.json`'daki `core_units`) için: unit ikonu, altında/yanında o unit üzerinde önerilen item ikonları (küçük, en fazla 3 — `items.json`'ın kendi sınırı).
- Unit ikonunun üstüne gelince (native `title` yeterli, özel bir tooltip kütüphanesi gerekmez) unit adı çıkar.
- Bir item ikonunun üstüne gelince item adı + `lift` + `n` çıkar — `lift`'in yönü hâlâ doğru anlatılmalı (negatif iyi, bölüm 3.8), tooltip metninde bunu sözle belirt ("X sıralamayı Y kadar iyileştiriyor" gibi), çıplak `lift: -0.34` yazma.
- Bir core unit için hiç item yoksa (`items.json`'da boş liste — bölüm 3.8: "veri yok" demek, "kötü item" demek değil) unit ikonu yine görünür, altında item ikonu yerine kısa bir "veri yok" notu olur.
- Item ikonları `units.json`/`traits.json` ile aynı CommunityDragon stratejisiyle (bölüm 4: `onerror` yedek kutusu, `loading="lazy"`) — kaynak artık `items.json`'ın kendi `icon` alanı (bölüm 3.8'in güncellenmiş şeması), ayrı bir statik katalog çekmeye gerek yok.
- `_metadata.known_limitation` uyarısı (bölüm 3.8) bu bölümde de gösterilmeye devam ediyor — bölüm başlığının yanında bir uyarı ikonu/tooltip yeterli, her item için tekrarlamaya gerek yok.

### 8.4 Head-to-head modal — board'a dokunmadan comp peek (2026-08-31, kullanıcı kararı)

Head-to-head bölümündeki (8, 8.1) karşı taraf comp isimleri ve bölüm 7.3'ün "en yakın 3 comp" listesi **farklı** iki tıklama davranışı taşır — karıştırılmamalı:

- **Bölüm 7.3'ün "en yakın 3 comp" listesi** ve **bölüm 5.3'ün önerilen comp'lar paneli**: tıklanınca board'u DEĞİŞTİRİR (`loadSuggestedComp()`, core_units board'a yüklenir).
- **Head-to-head'deki karşı taraf isimleri (bu bölüm)**: tıklanınca board'a HİÇ DOKUNMAZ — sadece o comp'un raporunu ortada bir modalda gösterir, "bu comp nasıl bir şey" diye bakıp board'u kaybetmeden geri dönme imkânı.

Davranış:

- Comp ismi `.comp-link` butonu — kalın, altı çizili hover/focus'ta (bölüm 8'in tablosu).
- Tıklanınca `#comp-modal-overlay` görünür olur: arka plan karartılır (dim overlay), ortada bir panel (`#comp-modal`) açılır. Panel içeriği o comp'un tam raporu — özet cümlesi, item önerileri, head-to-head, trait analizi (bölüm 8.2/8.3'ün aynı kuralları) — **eşleşme mesafesi satırı YOK**, çünkü bu board'un eşleştiği bir comp değil, board'a dokunmadan incelenen bir comp.
- Sağ üstte bir çarpı (`#comp-modal-close`) ve `Escape` tuşu paneli kapatır.
- **Zincirleme gezinme, üst üste modal değil.** Modal içindeki head-to-head'in kendi comp isimleri de aynı `.comp-link` mekanizmasına bağlı — tıklanınca YENİ bir modal açılmaz, `#comp-modal-content`'in içeriği o comp'un raporuyla değiştirilir (`openCompModal()` idempotent: hangi comp'tan çağrılırsa çağrılsın aynı tek overlay/panel'i yeniden doldurur). Canlı doğrulandı (headless tarayıcı): modal açıldıktan sonra içindeki bir comp linkine tıklanınca `document.querySelectorAll('.comp-modal-overlay').length` hem tıklamadan önce hem sonra **1** kaldı, başlık tıklanan comp'a göre değişti.
- `renderCompReport()`'un gövdesi (item→özet→head-to-head→trait, bölüm 8'in sırası) `buildCompReportBody()` adıyla ortak bir fonksiyona çıkarıldı — ana sonuç paneli ve modal AYNI fonksiyonu çağırıyor, ikisi birbirinden sürüklenmesin diye.

### 8.5 Meta-tier rozeti (2026-09-01, kullanıcı kararı — sınırlar veriden, ölçüldü)

Comp adının yanında dört bantlı bir rozet: **Güçlü / Ortalama üstü / Ortalama altı / Zayıf**, sırasıyla yeşil→sarı(altın)→turuncu→gri. Görüldüğü üç yer: sol sütunun önerilen comp'lar listesi (bölüm 5.3), ana sonuç panelinin başlığı (bölüm 8), ve comp modalının başlığı (bölüm 8.4).

- **Sınırlar koda gömülmedi.** Dört bant, `comps.json`'ın `_metadata.avg_placement_quartiles`'ındaki (bölüm 3.4) `q1`/`q2`/`q3` çeyreklik sınırlarına göre kuruluyor: `avg_placement ≤ q1` → Güçlü, `q1 < avg_placement ≤ q2` → Ortalama üstü, `q2 < avg_placement ≤ q3` → Ortalama altı, `avg_placement > q3` → Zayıf. `clustering.py` bu üç sayıyı her koşuda 19 real comp'un `avg_placement`'ından yeniden hesaplıyor — veri değişince (yeni bir scrape, farklı cluster sayısı) bantlar da otomatik kayar, `app.js`'te hiçbir sayısal eşik yok. Etiket metinleri ve renkler (`TIER_BANDS`) `app.js`'te sabit — "zayıf eşleşme"/"düşük güven" ile aynı ilke: sayısal eşik veriden, UI metni koddan.
- **2026-08-31'de ölçülen dağılım** (15k koşusu, 19 comp): min=3.958, max=4.871, medyan=4.519, Q1/Q2/Q3=4.271/4.519/4.682.
- **Tooltip** (native `title`), bandın nasıl hesaplandığını VE bu comp'un tam değerini gösterir — ör. "Bant, 19 comp'un ortalama sıralama çeyrekliklerinden türetildi (Q1=4.27, Q2=4.52, Q3=4.68). Bu comp: 4.11."
- `off_meta`'nın rozeti yok — zaten hiçbir yerde comp gibi gösterilmiyor (bölüm 3.4).

## 9. Dil — bu bölüm pazarlık konusu değil

Riot API tur tur savaş kaydı vermiyor. Elimizdeki veri sadece final board'lar ve final sıralamalar. Dolayısıyla arayüzde şu kelimeler **kullanılmayacak:** counter, yener, karşı gelir, win rate, kazanma oranı.

Kullanılacak ifade: "aynı lobide daha yüksek sıralama alma oranı" veya kısaca "sıralama üstünlüğü".

Sayfanın altındaki kısıtlar kutusunda şunlar yazacak:

- Bu oranlar savaş sonuçlarını değil, aynı lobide bulunan iki comp'un final sıralamalarının karşılaştırmasını gösterir.
- Veri yalnızca yüksek elo (Master ve üzeri) maçlarından toplanmıştır, tüm oyuncu tabanını temsil etmez.
- Veri hangi yamadan toplandıysa o yazılacak (`meta.json`'dan oku, elle yazma). Denge yamalarıyla sonuçlar eskir.
- Dizilim, ekonomi ve item verisi analize dahil değildir.

> **v1'de listede olmayan madde:** "Augment istatistikleri yalnızca seçilmiş augment'leri içerir..." satırı ilk taslaktaydı, kaldırıldı — augment sekmesi v1'de hiç yok, gösterilmeyen bir şeyin seçim yanlılığını uyarmanın anlamı yok. `augments.json` üretilmeye başlarsa (bölüm 3.6) bu satır kısıtlar kutusuna geri eklenmeli.

## 10. Yaygın hata listesi

| Hata | Sonucu | Önlem |
| --- | --- | --- |
| `unit_order` yerine kendi sıralaması | Sessizce yanlış comp eşleşmesi | `meta.json`'dan oku, `_selftest` ile doğrula |
| `dragover`'da `preventDefault` yok | Drop hiç çalışmaz | Handler'ı ilk yazarken ekle |
| `file://` ile açma | JSON fetch CORS hatası | README'nin ilk satırı |
| Yıldız seviyesini trait sayımına katma | Yanlış aktif trait | Benzersiz unit say |
| Düşük placement'ı kötü renklendirme | Öneriler tersine okunur | TFT'de küçük sayı iyidir |
| n'i gizleme | Gürültü kesin bilgi gibi görünür | Her oranın yanında zorunlu |
| Yetersiz veriyi saklama | Sahte tamlık | "Yetersiz veri" yaz |
| Set verisini koda gömme | Yeni sette baştan yazma | Her şey `data/` klasöründen |

## 11. Kapsam dışı (v1'de yapılmayacak)

- Augment önerisi/sekmesi. Pipeline tarafında augment analizi askıya alındı (SPEC.md bölüm 11) — Riot API'nin katılımcı nesnesinde `augments` alanı hiç yok, `augments.json` üretilmiyor. Şema referans olarak bölüm 3.6'da duruyor; veri gelirse bu sekme eklenir.
- Item / ekipman girişi ve item bazlı öneri.
- Unit dizilimi (pozisyon) — API veri vermiyor.
- Emblem ve trait ekleyen item'lar.
- LightGBM placement tahmini. Model tarayıcıda çalışmaz; ONNX'e çevirmek ayrı bir iş. v1'de sadece cluster eşleşmesi göster.
- Kullanıcı hesabı, kayıt, geçmiş, paylaşma linki.
- Canlı veri güncellemesi. `data/` elle değiştirilir.
- Mobil öncelikli tasarım. Çalışsın yeter, optimize etme.

## 12. Bitmiş sayılma kriterleri

- `data/` klasöründeki dosyalar değiştirilince arayüz kodu değişmeden yeni veriyi gösteriyor.
- Kodda tek bir unit, trait veya augment ismi/ID'si yok.
- `_selftest.json` kontrolü açılışta çalışıyor ve tuttuğunu konsola yazıyor.
- Unit hem sürüklenerek hem tıklanarak eklenebiliyor ve kaldırılabiliyor.
- Aktif trait paneli board değişince anında ve doğru güncelleniyor.
- Eşiği aşan board'da rapor değil, "uymuyor" mesajı çıkıyor.
- Her oranın yanında n var, güven aralığı olan yerlerde aralık var.
- Kısıtlar kutusu görünür ve içindeki yama bilgisi `meta.json`'dan geliyor.
- Sayfada "counter" veya "win rate" kelimesi geçmiyor.
- Kırık görsel durumunda yedek kutu görünüyor.

---

*Uygulama sırası önerisi: veri yükleme ve şema doğrulama → palet → board ve DnD → trait hesabı → vektör ve eşleştirme → sonuç paneli → kısıtlar ve cilalama. Her adımı bitirmeden sonrakine geçme; özellikle vektör doğrulaması geçmeden sonuç paneline başlama.*

## Ek: Web arayüzü karar geçmişi

Bu bölüm, web arayüzü inşa edilirken alınan önemli kararları ve reddedilen/geri alınan yaklaşımları kaydeder — SPEC.md'nin kendi "Ek: Karar geçmişi" bölümüyle aynı gerekçeyle: gelecekte biri "bu neden böyle?" diye sorduğunda cevap burada dursun, aynı çıkmaza ikinci kez girilmesin. Ayrıntılı gerekçeler ilgili bölümlerde yazılı (aşağıda referans veriliyor); burası özet ve zaman çizelgesi.

**Üç eşit sütun → TFT temalı yerleşim (bölüm 5, kabul edildi).** İlk uygulanan üç eşit dikey sütun (palet/board/sonuç) çalışıyordu — terk edilme sebebi bir hata değil, TFT'nin kendi görsel diline yaklaşma kararı: logo, koyu tema, altın/mor vurgular, kart-stili unit görünümü (portre arka plan + trait ikonları + maliyet şeridi), yatay palet + dikey maliyet sekmeleri, sol "önerilen comp'lar" paneli. Ayrıntı bölüm 5.0-5.4, 5.6.

**Sonuç sütunu (ızgara içinde) → kayan overlay panel (bölüm 5.5, kabul edildi).** Ara aşamada sonuç paneli üçüncü bir ızgara sütunuydu; kendi `overflow-y:auto`'suyla sayfayı uzatmıyordu ama board'a ayrılan genişliği daraltıyor, kullanımda sıkışık hissettiriyordu. Sağ kenarda `position:fixed` bir overlay'e taşındı: board artık kalan tüm genişliği kullanıyor, panel board'un üstüne biniyor (yüksek `z-index`), açılıp kapanması layout'u hiç etkilemiyor. Sekme ("‹"/"›") her zaman görünür, board boş veya comp eşleşmemiş olsa bile.

**Comp'a tıklayınca board'u dolduran onay penceresi — eklendi, sonra kaldırıldı (bölüm 5.3).** İlk taslak, board doluyken bir öneriye tıklanınca `confirm("Mevcut board silinecek, devam edilsin mi?")` gösteriyordu. Kullanıma alınınca rahatsız edici bulundu (comp'lar arası hızlı gezinmeyi her seferinde kesiyordu) ve kaldırıldı — artık board doluysa da doğrudan, onaysız yükleniyor.

**`overflow-y` sekmeyi görünmez kılıyordu — bulundu ve düzeltildi (bölüm 5.5).** Kayan panelin scroll'unu doğrudan dış konteynere (`overflow-y:auto`) koymak, panelin sol kenarından dışarı taşan sekme butonunu tamamen kırptı — CSS'in "bir eksenin overflow'u `visible` dışında olunca öteki eksen de `auto` sayılır" kuralı yüzünden. DOM'da elemanın var olması yeterli değildi; jsdom'daki tüm testler geçmesine rağmen (jsdom gerçek CSS layout/clip hesaplamıyor) sorun sadece gerçek bir tarayıcıda ekran görüntüsü alınınca ortaya çıktı. Düzeltme: scroll'u ayrı bir iç `div`'e (`.result-panel-scroll`) taşımak, dış konteyneri `overflow: visible` (varsayılan) bırakmak.

**Item önerileri: cümleden ikona (bölüm 8.3, kabul edildi — bölüm 8.2'nin tek istisnası).** İlk uygulama item önerilerini de diğer her şey gibi cümleye çevirmişti ("X üzerinde Y, ortalama sıralamayı Z iyileştiriyor"). Sonradan TFT'nin kendi item gösterimine yakın bir tasarıma geçildi: her core unit için ikon + altında item ikonları, ad/yön/n bilgisi native `title` tooltip'inde. Bunun için ayrı bir backend eksikliği ortaya çıktı: `items.json`'da hiç `icon` alanı yoktu, `data/static/items.json`'daki ikon URL'i hiç `web/data/`'ya kopyalanmıyordu — `build_web_data.py`'ye `enrich_items_with_icons()` eklenip bölüm 3.8'in şeması güncellendi. Desen canlı doğrulandı: 10 rastgele tamamlanmış item, 10/10 → 200.

**Arama vs. maliyet sekmesi çakışması — arama kazanıyor (bölüm 5.4, kabul edildi).** Yatay palete geçince maliyet filtresi bir dropdown'dan sekmeye döndü; bu, arama ile sekmenin aynı anda aktifken çelişebileceği bir durum yarattı ("1 maliyet" sekmesi açıkken "Ahri" (4 maliyet) aranırsa hiç sonuç çıkmaz, kullanıcı "Ahri" yokmuş sanabilir). Karar: arama kutusunda metin varken maliyet sekmesi devre dışı kalır, sonuçlar tüm maliyetlerden gelir (hiçbir sekme "seçili" görünmez); trait filtresi bundan etkilenmez, aramayla VE mantığıyla birleşmeye devam eder.

**Head-to-head'de yetersiz veri — gizlemek yerine isimlendirmek (bölüm 8, kabul edildi, CLI'den kasıtlı sapma).** `src/cli.py`'nin `format_head_to_head`'i `insufficient_data` eşleşmelerini sessizce atlar. Web arayüzü bölüm 8.1'in "yetersiz veri gizlenmeyecek" kuralını daha sıkı uyguluyor: bu eşleşmeler sıralamaya girmiyor ama ayrı bir cümlede karşı taraf isimleriyle birlikte açıkça belirtiliyor ("şu comp'larla yeterli veri olmadığı için karşılaştırma yapılamıyor: ...").

**Sabit viewport (`body height:100vh` + tek esneyen flex çocuk) → doğal yükseklik + sayfa scroll'u (bölüm 5.0, 2026-08-31, gerçek bir hataya karşı düzeltildi).** 2026-08-30'da "çözüldü" denen model aslında board sütununu palet ile kısıtlar kutusu arasında birkaç piksele sıkıştırıyordu — küçülmeyen kardeşler (`flex:0 0 auto`) yüzünden tek esneyen bölgenin (`flex:1 1 auto; min-height:0`) payı 0'a yakın kalıyordu, `body`'nin "son çare" scroll'u da hiç tetiklenmiyordu çünkü flex-shrink açığı sessizce kapatıyordu. Canlı ekran görüntüsüyle doğrulandı. Düzeltme: `body`'nin sabit yüksekliği tamamen kaldırıldı, sayfa artık içeriğe göre büyüyor ve taştığında sayfa düzeyinde scroll ediyor. Sonuç panelinin `position:fixed` overlay'i bundan hiç etkilenmiyordu, değişmedi. Ayrıntı bölüm 5.0.

**Sonuç panelinde item önerileri en alttan en üste (bölüm 8, 2026-08-31, kullanıcı kararı).** Item önerileri görsel olduğu ve kullanıcının ilgisini ilk çektiği için sıranın başına alındı: item önerileri → temel istatistik → head-to-head → trait analizi. Comp kimliği (ad + mesafe) panelin başlığı olduğu için sıralamanın dışında, her zaman en üstte kaldı. Sadece `app.js`'in `renderCompReport`'undaki DOM ekleme sırası değişti — hiçbir bölümün içeriği, hesaplama mantığı veya dil kuralı (8.1-8.3) değişmedi; self-test bu fonksiyona hiç bağlı olmadığı için (sadece vektör/eşleştirme mantığını test ediyor) etkilenmedi, 23/23 doğrulandı.

**Diğer küçük kararlar (ayrıntı ilgili bölümlerde):** önerilen comp sayısı 8 (bölüm 5.3, "5-10" aralığından seçilmiş sabit), `core_units` 10'u aşarsa ilk 10'u alma (bölüm 5.3), arama kutusu ve trait filtresinin yeniden tasarımdan sonra da korunması (bölüm 5.4), unit portresi için CSS `background-image` değil gerçek `<img>` kullanılması (bölüm 5.2 — `onerror` yedek davranışı için zorunlu), "zayıf eşleşme" ve "düşük güven" eşiklerinin (%90, 15 puan) veriden değil uygulamadan seçilmiş sabitler olması (bölüm 8, 8.1).

**"En yakın 3 comp" tıklanabilir hale getirildi (bölüm 7.3, 2026-08-31, kullanıcı kararı).** Önceden düz metindi. Artık sol sütunun önerilen comp'lar panelindeki AYNI `loadSuggestedComp()` fonksiyonuna bağlı — tıklanınca board o comp'un `core_units`'iyle dolar. Modal açmaz, bilinçli bir ayrım: bu board'u DEĞİŞTİREN bir eylem, bölüm 8.4'ün board'a dokunmayan head-to-head modalıyla karıştırılmamalı.

**Head-to-head modal eklendi (bölüm 8.4, 2026-08-31, kullanıcı kararı).** Head-to-head'deki karşı taraf comp isimleri kalın, tıklanabilir `.comp-link` butonu oldu; tıklanınca board'a dokunmadan o comp'un tam raporunu ortada bir modalda gösteriyor (X butonu ve `Escape` ile kapanır). `renderCompReport()`'un gövdesi (`buildCompReportBody()`) ana panel ve modal arasında paylaşılan tek bir fonksiyona çıkarıldı. Zincirleme gezinme kasıtlı bir tasarım kararıydı: modal içindeki head-to-head isimleri de aynı mekanizmaya bağlı ama yeni bir modal AÇMIYOR, `openCompModal()` idempotent olduğu için mevcut modalın içeriğini değiştiriyor — canlı doğrulandı (headless tarayıcı, zincirleme tıklamadan önce/sonra `.comp-modal-overlay` sayısı hep 1).

**Meta-tier rozeti eklendi (bölüm 8.5, 2026-09-01, kullanıcı kararı — bantlar önce ölçülüp raporlandı, sonra onaylanıp uygulandı).** 19 comp'un `avg_placement`'ı çeyrekliklere bölünüp 4 bant (Güçlü/Ortalama üstü/Ortalama altı/Zayıf) kuruldu. Sayısal sınırlar `clustering.py`'de hesaplanıp `comps.json`'ın yeni `_metadata` anahtarına yazılıyor (bölüm 3.4) — `app.js`'e hiçbir eşik gömülmedi, sadece etiket/renk. `_metadata` `off_meta` ile aynı türden bir "normal comp değil" anahtarı olduğu için `comps.json`'ı dolaşan üç dosya (`build_web_data.py`, `items.py`, `app.js`) denetlenip düzeltildi — `items.py`'de `int(comp_id.split("_")[1])` `_metadata` için çökerdi (`int("metadata")`), `build_web_data.py`'nin self-test centroid sözlüğü `info["centroid"]` KeyError verirdi. Hepsi `comp_id.startswith("c_")` deseniyle düzeltildi.

**`core_unit_avg_stars` eklendi (bölüm 3.4/5.3, 2026-09-01, kullanıcı kararı — önce ölçülüp raporlandı).** `loadSuggestedComp()` her core unit'i sabit 2 yıldızla dolduruyordu; gerçek veride ölçülünce (SPEC.md bölüm 9) 144 (cluster, core_unit) çiftinin sadece %13.2'sinin 2.0'a yakın çıktığı görüldü. `clustering.py`'ye `_unit_avg_star()` eklendi, her comp'un `core_unit_avg_stars`'ı (unit_id → ortalama yıldız, sadece o unit'i barındıran board'lar üzerinden) `comps.json`'a yazılıyor — `core_units`'in kendisi değişmedi (hâlâ düz bir ID listesi), bu tamamen ek bir alan. `loadSuggestedComp()` artık bu değeri `Math.round()`'la (yarımdan yukarı) en yakın tam yıldıza yuvarlayıp kullanıyor. Elle board kuran kullanıcı (bölüm 6.1) etkilenmedi — DEFAULT_STAR (2) orada hâlâ geçerli, çünkü tek tek eklenen bir unit'in hangi comp bağlamında olduğu bilinmiyor.

*Uygulama sırası önerisi: veri yükleme ve şema doğrulama → palet → board ve DnD → trait hesabı → vektör ve eşleştirme → sonuç paneli → kısıtlar ve cilalama. Her adımı bitirmeden sonrakine geçme; özellikle vektör doğrulaması geçmeden sonuç paneline başlama.*