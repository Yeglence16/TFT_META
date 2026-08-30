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
    "common_traits": [["TFT18_Sorcerer", 6], ["TFT18_Wisp", 4]]
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

> **`off_meta` — normal bir comp anahtarı değil (2026-08-30, web build validator'ında bulundu, önceden belgelenmemişti).** `comps.json`'da her zaman bir `off_meta` anahtarı da vardır (HDBSCAN gürültüsünden ve early_death'ten hiçbir centroid'e yeterince yakın çıkmayan board'ların toplandığı kova — bkz. SPEC.md bölüm 9). Bu anahtarın değeri yukarıdaki şemadan **farklı ve daha dar**: sadece `n_full`, `n_total`, `avg_placement`, `top4_rate` var — `name`, `centroid`, `core_units`, `common_traits` **yok**, çünkü off_meta gerçek bir comp değil. items.json'daki `_metadata` gibi alt çizgiyle başlamıyor (ismi `c_NN` kalıbına da uymuyor), o yüzden arayüz kodu bunu "bilinmeyen/bozuk comp" sanıp atlamamalı ama tam comp şeması da beklememeli. Palet/eşleştirme mantığı `comps.json`'ı dolaşırken `off_meta` anahtarını (varsa) comp listesinden ayrı tutmalı — bir centroid'i olmadığı için asla bir board'un "eşleştiği comp"u olamaz, sadece bölüm 7.3'ün "hiçbir comp'a uymuyor" yolunda referans istatistik olarak kullanılabilir.

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

> **İki hücre şekli var — üsttekinin bir varyantı, önceden belgelenmemişti (2026-08-30, web build validator'ında bulundu).** "Yetersiz örnekli hücreler zaten elenmiş olmalı" cümlesi yanıltıcı: `matchups.py` (SPEC.md bölüm 10) `n<100` çiftleri **silmiyor**, görünür tutup `{ "n": 91, "insufficient_data": true }` şeklinde işaretliyor — `rate`/`ci` alanları o hücrede hiç yok. Gerçek veride 484 çiftin 177'si (%36.6) bu ikinci şekilde. Arayüz her hücreyi okurken önce `insufficient_data === true` kontrolü yapmalı; true ise `rate`/`ci` okumaya çalışmadan "yetersiz veri" göstermeli (bölüm 8.1'in zaten istediği davranış — burada eksik olan şema tarafıydı, davranış tarafı değil).

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
      "total_cluster_unit_pairs": 166,
      "pairs_with_at_least_1_item": 125,
      "pairs_with_full_3_items": 93,
      "cells_below_min_n": 10829,
      "cells_total": 11680,
      "cells_below_min_n_fraction": 0.927
    }
  },
  "c_07": {
    "TFT18_Ahri": [{ "id": "TFT18_Item_JeweledGauntlet", "name": "Jeweled Gauntlet", "lift": -0.34, "n": 212 }],
    "...": []
  },
  "...": {}
}
```

Item **vektörün bir parçası değil** — comp tanımı (bölüm 3.4'teki `centroid`) item'dan bağımsız, aynı comp'u oynayan iki oyuncu farklı item taşıyabilir. Bu dosya ayrı bir katman: her comp için, o comp'un en sık oynanan unit'lerinden (`comps.json`'daki `core_units`) her biri üzerinde en iyi 3 item, `lift` ve `n` ile. `lift` = augment'lerdeki gibi comp'un kendi ortalama placement'ına göre fark, shrinkage uygulanmış; negatif iyi. Sadece tamamlanmış item'lar var, component'ler yok. Bir unit için `n < 50` olan hiçbir hücre bu listeye girmez (tamamen dışlanır, boş liste `[]` görebilirsin — "veri yok" demektir, "kötü item" demek değil).

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

Tek sayfa, üç sütun. Masaüstü öncelikli — kullanıcılar bilgisayarda oynayacak.

| Bölge | İçerik |
| --- | --- |
| Sol — Unit paleti | Setteki tüm unit'ler kart halinde. Arama kutusu, cost filtresi, trait filtresi. |
| Orta — Board | 10 slotluk takım alanı. Sürüklenen unit'ler buraya düşer. Altında aktif trait paneli. |
| Sağ — Sonuç | Eşleşen comp ve tüm analiz çıktısı. Board boşken boş durum mesajı. |

### 5.1 Board — hex ızgara YAPMA

TFT'nin gerçek altıgen dizilim ızgarasını kurma. Analiz pipeline'ı pozisyon verisi kullanmıyor — çünkü Riot API pozisyon vermiyor. Hex ızgara yaparsan kullanıcıya **dizilimin sonucu etkilediği izlenimini vermiş olursun**, ki etkilemiyor. Düz bir 10 slotluk sıra yeterli ve dürüst.

### 5.2 Unit kartı üzerindeki kontroller

- Yıldız seviyesi seçici (1 / 2 / 3). Varsayılan 2 — veride en sık görülen durum bu.
- Kaldırma butonu (üst köşede x). Sürükleyip dışarı atma da çalışsın ama tek yol olmasın.
- Aynı unit board'a iki kez eklenemez. Palette zaten eklenmiş unit'i soluklaştır.

## 6. Sürükle-bırak

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

**Ölçülen oran, beklenenden çok daha büyük çıktı — açık söylüyoruz.** 23496 gerçek ranked board'un **12410'u (%52.8)** bu birebir-eşleşme testini geçiyor; **11086'sı (%47.2)** eleniyor. (Bu sayılar `DA_18_Rival` düzeltmesinden [bkz. altta, SPEC.md bölüm 4] sonraki hal — düzeltme öncesi %52.0/%48.0'dı, fark küçük çünkü Rival'ın kendisi tek başına büyük bir pay değildi.) İkiye ayrıştırılmış hali:

- **Lux'lu board'lar: %99.0 elendi (2479/2504).** Lux'ın "Avatar" mekaniği, hangi köken origin'ini kopyaladığına göre değişen bir trait veriyor; `static_data.py` onun 9 köken-varyantını (`unit_aliases.json`) tek bir kanonik unit'e (`DA_Lux18_Base`) topluyor ve bu sırada hangi origin'in kopyalandığı bilgisi kayboluyor — salt unit listesinden o origin trait'i geri türetilemiyor.
- **Lux'suz board'larda bile %41.0 elendi (8608/20992) — bileşenleri artık biliniyor.** `emblem_traits.json`'daki 21 bilinen emblem'in katkısı da sayıma katılınca oran **%41.0'dan %11.0'a** düşüyor — yani bu oranın büyük çoğunluğu zaten bilinen emblem etkisi (SPEC.md bölüm 15). Kalan %11.0'lık dilim şu kaynaktan geliyor:
  - **Board/bench ayrımının API'de hiç olmaması (SPEC.md bölüm 8'in belgelediği kısıt, kabul edildi/düzeltilmedi).** `participant.units` board'da mı bench'te mi olduğunu söylemiyor; board'ların %7.2'sinde unit sayısı `level`'i aşıyor. Bunun kalıntı uyumsuzluğa katkısı düzensiz: kapasiteyi aşan board'larda kalıntı oranı %39, aşmayanlarda %12 — ama basit bir "fazlalığı sil" düzeltmesi denendi ve **kanıtla reddedildi** (uyumsuzluğu %39'dan %92'ye çıkardı, gerçek board unit'lerini bench sanıp siliyordu). Ayrıntı SPEC.md bölüm 8 ve "Ek: Karar geçmişi".
  - **`DA_18_Rival`'ın tekrarlanan breakpoint'i — düzeltildi (SPEC.md bölüm 4, 2026-08-30).** `extract_traits()` artık `minUnits`'i dedupe ediyor, Rival'ın breakpoints'i `[1, 1, 2]`'den `[1, 2]`'ye düzeldi. Etkisi ölçüldü: Rival'ın rol oynadığı mismatch sayısı 878'den 455'e düştü, ama **tek başına açıklayıcı olduğu board sayısı 206'dan sadece 4'e indi** — çünkü düzeltme öncesinin asıl sorunu (native=1 board'larda yanlışlıkla tier=2 hesaplanması, gerçek tier=1) artık yok. Geriye kalan 455 vaka, düzeltmenin **çözmediği ayrı bir bulgudan** geliyor: native=2 olan board'ların **%99.8'inde (478/479)** Riot'un gerçek `tier_current`'ı **3** — düzeltilmiş `[1, 2]` breakpoint listesiyle native=2'den hesaplanabilecek en yüksek tier 2, asla 3 değil. Rival'ın gerçek tier skalası basit breakpoint-sayımıyla hâlâ tam örtüşmüyor; kök nedeni araştırılmadı, SPEC.md bölüm 4'te ayrı bir çözülmemiş bulgu olarak duruyor.

**Bunun anlamı — ve anlamadığı şey.** Bu, arayüzün ya da self-test'in bir kusuru **değil**: kullanıcının sürükle-bırakla kurduğu bir board'da hiç item yok (v1 kapsamı), dolayısıyla salt unit sayımı o board için zaten **tanımı gereği doğru** hesap — %47'lik oran, "gerçek geçmiş maçlardan hangilerinin fixture olarak güvenilir olduğu" sorusuyla ilgili, "kullanıcının kurduğu board'ların %47'si yanlış hesaplanıyor" ile ilgili değil. Ama şunu da gösteriyor: item/emblem girişi ileride eklenirse (şu an bölüm 11'de kapsam dışı), aynı boşluk o zaman gerçek bir doğruluk sorunu haline gelir ve önce çözülmesi gerekir.

Elenen board'lar arasından, farklı comp'ları temsil edecek şekilde (cluster başına bir örnek) **23 board** seçilip `_selftest.json`'a yazıldı — 22 gerçek comp + bir "hiçbir comp'a uymuyor" (off-meta) örneği.

### 7.3 Comp eşleştirme

Normalize vektör ile `comps.json`'daki her centroid arasında cosine mesafesi hesapla, en küçüğünü seç.

- Mesafe `meta.match_threshold` altındaysa: o comp'un tam raporunu göster.
- Üstündeyse: "Bu board bilinen bir comp'a uymuyor" mesajı ve en yakın 3 comp'u mesafeleriyle listele. Zorla bir comp'a eşleştirip rapor gösterme.
- Board'da 4'ten az unit varsa hiç eşleştirme yapma, "daha fazla unit ekle" de. Yarım board'lar her comp'a uzak çıkar ve anlamsız sonuç üretir.

## 8. Sonuç paneli

Yukarıdan aşağıya sıra:

| Bölüm | İçerik ve kural |
| --- | --- |
| Comp kimliği | Comp adı, eşleşme mesafesi, örnek sayısı (n). Mesafe eşiğe yakınsa "zayıf eşleşme" rozeti. |
| Temel istatistik | Ortalama placement ve top-4 oranı. n mutlaka görünsün. |
| Head-to-head | En iyi 5 ve en kötü 5 eşleşme. Her satırda oran, n ve güven aralığı. |
| Trait analizi | Zorlandığı 5 ve rahat ettiği 5 trait, delta ve n ile. |
| Kısıtlar | Sayfanın altında sabit duran, kapanabilir bir kutu. İçeriği bölüm 9'da. |

> **Augment sekmesi v1'de yok.** İlk taslakta burada bir "Augment: silver / gold / prismatic" satırı vardı; kaldırıldı çünkü `augments.json` üretilmiyor (bölüm 3.6, SPEC.md bölüm 11 — Riot API'de `augments` alanı hiç yok). Veri bir gün gelirse bu sekme geri eklenir, şema referans olarak bölüm 3.6'da duruyor.

> **"Yakın" eşiği — sayısal olarak tanımlandı (2026-08-30, uygulama sırasında bulundu).** Bu bölüm "mesafe eşiğe yakınsa" diyor ama bir sayı vermiyordu; uygulama bunu belirsiz bırakamadığı için bir sabit seçti: **mesafe, `match_threshold`'un %90'ından fazlaysa** "zayıf eşleşme" rozeti gösterilir (`app.js`'te `WEAK_MATCH_RATIO = 0.9`). Bu veriden türetilmiş bir değer değil, keyfi ama açık bir seçim — mesafe eşiğe yaklaştıkça, board gerçekte comp'un tipik örneklerinden gitgide uzaklaşan bir board olabilir, kullanıcı bunu bilsin diye. Değiştirmek istenirse tek satır (`app.js`'teki sabit).

### 8.1 Her yerde geçerli kurallar

- Hiçbir oran n olmadan gösterilmeyecek.
- Yetersiz veri olan hücre gizlenmeyecek, "yetersiz veri" yazılacak. Gizlemek, kullanıcıya var olmayan bir tamlık hissi verir.
- Güven aralığı geniş olan sonuçları görsel olarak zayıflat (soluk renk veya "düşük güven" etiketi).

> **"Geniş" eşiği — sayısal olarak tanımlandı (2026-08-30, uygulama sırasında bulundu).** Yukarıdaki kural da bir sayı vermiyordu. Uygulama seçimi: **güven aralığının üst-alt genişliği 0.15'i (15 yüzde puanı) aşarsa** "düşük güven" olarak zayıflatılır (`app.js`'te `WIDE_CI_WIDTH = 0.15`). Bu da veriden türetilmedi, keyfi bir sabit — Wilson aralığının genel olarak ne kadar geniş çıktığına dair bir dağılım analizi yapılmadı. Değiştirmek istenirse tek satır.

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