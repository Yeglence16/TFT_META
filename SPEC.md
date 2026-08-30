# TFT Comp Analyzer — Proje Spesifikasyonu

Bu doküman projenin teknik spesifikasyonudur. Amaç: güncel TFT setinden maç
verisi toplayıp, comp'ları veriden çıkarıp (etiketli comp verisi **yoktur**),
comp'lar arası sıralama karşılaştırmaları, augment tavsiyeleri ve trait bazlı
güçlü/zayıf analiz üreten bir CLI aracı.

Kapsam: **tek set, tek yama, 1-2 bölge.** Canlı servis değil, tekrar üretilebilir
bir analiz pipeline'ı.

---

## 0. Terminoloji uyarısı — bunu baştan doğru kur

Riot API tur tur savaş kaydı **vermiyor**. Maç verisi sadece 8 oyuncunun final
board'unu ve final sıralamasını içeriyor. Dolayısıyla "A compu B compunu yener"
diye bir şey hesaplayamayız.

Hesapladığımız şey: **aynı lobide her iki comp da varken A'nın B'den yukarıda
bitirme oranı.** Buna kod içinde, çıktılarda ve README'de `head-to-head placement
rate` de. `counter` / `win rate` **deme.** Bu bir üslup tercihi değil, veri
neyi destekliyorsa o.

---

## 1. Teknoloji ve iskelet

Python 3.11+. Bağımlılıklar:

```
httpx           # veya requests
pandas, numpy
scikit-learn    # >=1.3, HDBSCAN dahil
lightgbm
pyyaml
tqdm
```

Dizin yapısı:

```
tft-comp-analyzer/
  config.yaml
  data/
    raw/            # gzipped jsonl shard'lar — SİLİNMEZ
    processed/
    static/
  models/
  src/
    riot_client.py
    static_data.py
    scrape_ladder.py
    scrape_matches.py
    features.py
    clustering.py
    matchups.py
    augments.py
    traits.py
    model.py
    cli.py
  README.md
```

`config.yaml` içinde olması gerekenler: hedef set numarası, hedef yama prefix'i
(`game_version` bununla filtrelenecek), bölge listesi, hedef maç sayısı,
clustering parametreleri, shrinkage sabiti.

**Hiçbir unit / trait / item / augment ismi veya ID'si koda gömülmeyecek.**
Hepsi static data katmanından runtime'da gelecek. Set numarası config'ten
okunacak, sabit yazılmayacak.

API anahtarı `RIOT_API_KEY` environment variable'ından okunacak. Repo'ya asla
girmeyecek, `.env` `.gitignore`'a eklenecek.

---

## 2. Rate-limited API istemcisi (`riot_client.py`)

Development key limitleri:
- 20 istek / 1 saniye
- 100 istek / 2 dakika

Bağlayıcı olan ikincisi. Sürdürülebilir hız ~0.83 istek/saniye. **İki pencereyi
de ayrı ayrı takip eden bir token bucket** yaz, ikisini birden sağla.

Hata davranışı — bu kısım kritik:

| Durum | Davranış |
|---|---|
| `429` | `Retry-After` header'ına uy, bekle, tekrar dene |
| `403` | **Anında dur.** Retry etme. Kuyruktaki öğeyi "başarısız" işaretleme. Anlaşılır bir hata bas: key expire olmuş. |
| `404` | Atla, logla, devam et |
| `5xx` | Exponential backoff, en fazla 3 deneme |

`403` davranışı özellikle önemli: dev key 24 saatte bir expire oluyor. İstemci
bunu "bu maç alınamadı" diye yorumlarsa binlerce maç sessizce çöpe gider ve bunu
ancak sonuçlar tuhaf çıkınca fark ederiz. Sessiz veri kaybı olmayacak.

Routing iki seviyeli, karıştırma:
- **Platform host** (`euw1`, `na1`, `kr`, `tr1`): league ve summoner endpoint'leri
- **Regional host** (`europe`, `americas`, `asia`): account ve match endpoint'leri

---

## 3. Resumability

Scraper'ın her aşaması **kaldığı yerden devam edebilir** olacak.

**Uygulandı** — `src/state_store.py`, SQLite üzerine `StateStore`. Tek tablo,
`namespace` alanıyla bölünmüş (`namespace, key, status`), `status` ∈
`{pending, done}`. Her yazma anında commit ediliyor; süreç ortasında ölürse
diskteki durum gerçek ilerlemeyi yansıtmaya devam ediyor. Bu tek tablo üç
kullanım için yeterli:

- **işlenmiş PUUID'ler** → `scrape_matches.py` faz 1'de namespace
  `puuids_matchid_collected`, her puuid'nin maç ID'leri çekildikçe `mark_done`
- **indirilmiş maç ID'leri** → namespace `match_ids`, indirilince (veya 404
  onaylanınca) `mark_done`
- **bekleyen kuyruk** → aynı `match_ids` namespace'i, `pending_keys()` ile
  okunuyor (faz 1'de `add_pending` ile eklenen, henüz indirilmemiş ID'ler)

Ayrıca `scrape_ladder.py` sayfalama ilerlemesi için `ladder_pages` namespace'i
kullanıyor (`{platform}:{tier}:{division}:{page}` anahtarıyla), ve
`data/processed/puuids.json`'ı her çalıştırmada önce okuyup mevcut PUUID
kümesini bellekte tutuyor, böylece kesinti sonrası kaldığı yerden devam
ediyor.

Süreç öldüğünde, key expire olduğunda veya bilgisayar kapandığında baştan
başlanmıyor — canlı testte doğrulandı: aynı komut ikinci kez çalıştırıldığında
0 yeni PUUID / 0 bekleyen maç ID'si buldu ve hiçbir şeyi tekrar indirmedi. Bu
opsiyonel değildi — 20-30k maçlık scrape dev key'le ~11 saat sürüyor.

---

## 4. Static data (`static_data.py`)

Kaynak: CommunityDragon TFT dump'ı
(`https://raw.communitydragon.org/latest/cdragon/tft/en_us.json`).

İndir, `data/static/` altına cache'le, şu eşlemeleri üret:

- `character_id` → unit display name, cost, traits
- trait ID → display name, breakpoint'ler
- item ID → display name
**Item sınıflandırması (uygulandı).** Ham dump'ta 773 item var; augment'lerde
doğrulanan aynı `DA_` önek filtresi 502'ye indiriyor (271'i başka setlerden
taşınan legacy kayıt). SPEC'in augment için varsaydığı `rarity` alanı item
kayıtlarında **yok** — sınıflandırma yapısal sinyallerle yapılıyor, sırayla:

- `component` (10) — `DA_Component_*` öneki. 9 klasik bileşen + `FryingPan`.
- `emblem` (23) — isim sinyali (`Emblem`). 16'sı composition'dan türüyor
  (Spatula×8 + FryingPan×8), 7'sinin composition'ı boş (ödül/encounter yoluyla
  veriliyor olabilir).
- `completed` (39) — composition dolu. Sayı doğrulandı: 10 bileşenden 55
  kombinasyon çıkar, 16'sı emblem olarak sınıflandığı için (emblem kontrolü
  composition kontrolünden **önce** çalışır) geriye 39 kalır. Kalan 3'ü
  Tactician's Crown/Shield/Cape.
- `other` (430) — geri kalan. **Doğrulanmış tek bir kategori değil**;
  örnekleme Radiant'ları, gerçek `DA_Artifact_*`'ları ve support/encounter
  mekanik item'larını birlikte gösterdi. Daha önce "artifact" adlandırılmıştı,
  doğrulanmamış etiket olduğu için `other`'a çevrildi.

Emblem→trait eşlemesi `emblem_traits.json`'a yazılıyor: emblem isminden
" Emblem" çıkarılıp trait ismiyle eşleştirilerek 23'ün 21'i çözülüyor.

**Düzeltildi — `DA_18_Rival`'ın tekrarlanan `minUnits`'i (2026-08-30, web
build validator'ı sırasında bulundu, aynı gün düzeltildi).**
`extract_traits()` her trait'in breakpoint listesini `sorted(e["minUnits"]
for e in t["effects"])` ile kuruyordu, dedupe etmeden. Set 18'in 36 ham
trait kaydının **sadece birinde** (`DA_18_Rival`) bu bir soruna yol
açıyordu: cdragon'da aynı eşikte (`minUnits=1`) iki ayrı `effect` kaydı var
(biri stil varyantı), bu yüzden çıkan breakpoint listesi `[1, 1, 2]` idi —
tekil olması gereken ilk eşik iki kez sayılıyordu. Düzeltme yapısal:
`sorted({e["minUnits"] for e in t["effects"] if ...})` — `set()` üzerinden
dedupe, trait ismi/ID'si üzerinden özel durum yok, gelecekteki bir set'te
aynı desen çıkarsa kod değişmeden çalışır. Yeni breakpoints: **`[1, 2]`**.

**Ölçüldü — düzeltme yalnızca `DA_18_Rival`'ı etkiliyor, boyut değişmiyor.**
Düzeltilmiş `extract_traits()` gerçek cdragon dump'ına karşı (dosyaya
yazmadan, bellekte) çalıştırılıp diskteki eski `traits.json` ile
karşılaştırıldı: **diğer 34 trait'in breakpoint listesi birebir aynı**,
sadece `DA_18_Rival` değişti. Trait sayısı (dict anahtar sayısı) **35'te
35** — değişmedi. Vektör boyutu trait sayısına bağlı (`trait_order` =
`sorted(traits.keys())`, `features.py` `breakpoints` alanını hiç okumuyor —
doğrulandı), breakpoint listesinin *uzunluğuna* değil; dolayısıyla **board
vektörünün boyutu (100 = 65 unit + 35 trait) değişmiyor.**

`breakpoints`'i gerçekten kullanan tek pipeline aşaması `traits.py`
(comp_traits.json) — `high_thresholds[trait] = len(breakpoints)` ile
"maxed tier" eşiğini kuruyor. Rival için bu eşik 3'ten 2'ye düştü. Gerçek
veride ölçüldü: bu eşiğin yakaladığı board sayısı 478'den (`tier_current==3`)
480'e çıkıyor (+2, `tier_current==2` olan 2 board da dahil oluyor) —
`comp_traits.json`'ı gözle görülür şekilde değiştirmesi beklenmez ama
`traits.py` yeniden çalıştırılmadan mevcut çıktı hâlâ eski (düzeltilmemiş)
eşiği yansıtıyor. `features.py`, `clustering.py`, `matchups.py`, `items.py`
— hiçbiri `breakpoints` alanını okumuyor (kod genelinde doğrulandı),
bunların yeniden çalıştırılmasına gerek yok.

**Düzeltme Rival'ı tam açıklamıyor — yeni, çözülmemiş bir bulgu.** Gerçek
`tier_current` dağılımı ölçülünce beklenmedik bir desen çıktı: native=1 →
`tier_current`=1 (969 board, düzeltilmiş formülle birebir tutuyor) ama
native=2 → `tier_current`=**3** (478 board, %99.8 tutarlılıkla) — düzeltilmiş
`[1, 2]` breakpoint listesiyle native=2 için hesaplanabilecek en yüksek tier
2, asla 3 değil. Yani Rival'ın gerçek tier skalası basit "kaç breakpoint
aşıldı" sayımıyla **hâlâ** tam örtüşmüyor; dedupe gerçek ve doğrulanmış bir
düzeltme ama Rival'ın tüm davranışını açıklamıyor. Kök nedeni bu oturumda
araştırılmadı — ayrı bir bulgu olarak kaydedildi, düzeltme kararı yok.

**Augment tier'ı için:** cdragon augment kayıtlarında bir `rarity` alanı var.
Önce onu incele ve gerçekten tier'a karşılık geldiğini birkaç bilinen augment'le
doğrula (örneğin bilinen bir prismatic ve bilinen bir silver seç, değerleri
karşılaştır). Doğrulamadan varsayma. Eşleme tutmuyorsa alternatif bir türetme
yolu bul ve hangi yöntemi kullandığını README'ye yaz.

Config'teki set numarasına göre filtrele — dump tüm setleri içeriyor.

---

## 5. Ladder scrape (`scrape_ladder.py`)

**Bulgu (2026-08-30): Set 18, 25 Ağustos 2026'da çıktı — bu yazıldığında 5
günlük.** Ranked ladder her sette sıfırlanıyor, yani `challenger` boş,
`grandmaster`/`master` neredeyse boş dönüyor. Bu bir API/key sorunu değil,
gerçek durum; canlıda doğrulandı (euw1 challenger: 0, grandmaster: 1, master:
4-6 entry). Bu yüzden yüksek tier endpoint'lerine ek olarak **sayfalı**
endpoint kullanılıyor:

```
/tft/league/v1/challenger              (platform host, LeagueListDTO)
/tft/league/v1/grandmaster             (platform host, LeagueListDTO)
/tft/league/v1/master                  (platform host, LeagueListDTO)
/tft/league/v1/entries/{tier}/{division}?page=N   (platform host, düz LeagueEntryDTO listesi)
```

Yüksek tier'lar (`challenger`/`grandmaster`/`master`) **önce denenir**; boş
dönerse sessizce atlanır (log'lanır ama hata sayılmaz). Sayfalı endpoint için
tier (`DIAMOND`, `EMERALD`, ...) ve division (`I`-`IV`) listesi, sayfa üst
sınırı `config.yaml`'ın `ladder:` anahtarından okunur — kodda hiçbir tier/
division/sayfa sayısı sabit yazılı değil:

```yaml
ladder:
  high_tiers: [challenger, grandmaster, master]
  paginated_tiers: [DIAMOND, EMERALD]
  divisions: [I, II, III, IV]
  max_pages_per_division: 50
```

Ladder tekrar dolduğunda (set olgunlaştıkça) kod değişmeden `high_tiers`
otomatik olarak dolu dönmeye başlar ve dahil edilir — bunun için ayrı bir
kod yolu yok, aynı fonksiyon.

Canlıda doğrulandı: `/tft/league/v1/entries/{tier}/{division}?page=N` düz bir
liste döndürüyor, her elemanda doğrudan `puuid` alanı var (`DIAMOND/I` için
`summonerId`'ye düşmeye hiç gerek kalmadı). Yine de entry'lerde `puuid` alanı
yoksa `summonerId` üzerinden `/tft/summoner/v1/summoners/{summonerId}` ile
çözme kodu duruyor — alan varlığı runtime'da kontrol ediliyor, varsayılmıyor.

Bölge önerisi: `euw1` + `na1`. Hacim yetmezse `kr` eklenebilir.

Yüksek elo seçilmesinin sebebi: comp'lar daha temiz oynanıyor, cluster'lar daha
net ayrışıyor. Bunu README'de bir kapsam kısıtı olarak yaz — sonuçlar yüksek elo
metasını temsil eder, tüm oyuncu tabanını değil.

Her tier/sayfadan kaç yeni PUUID geldiği loglanır (`[platform] tier: N entries,
M new puuids`).

Resumability (bölüm 3): sayfalama durumu `StateStore`'un `ladder_pages`
namespace'inde tutulur (`{platform}:{tier}:{division}:{page}`), zaten
tamamlanmış sayfalar tekrar çekilmez.

Çıktı: `data/processed/puuids.json` — `{"puuid": ..., "platform": ...}`
nesnelerinden oluşan, puuid'e göre dedupe edilmiş bir liste. `platform` alanı
tutuluyor çünkü maç ID toplama (bölüm 6) hangi regional host'un (europe/
americas/asia) kullanılacağını bundan çıkarıyor.

---

## 6. Maç ID toplama (`scrape_matches.py`, 1. faz)

Regional host:
```
/tft/match/v1/matches/by-puuid/{puuid}/ids?count=20
```

Her lobide 8 oyuncu olduğu için aynı maç ID'si defalarca gelecek. **Set halinde
dedupe et.** Dedupe oranını logla — beklenen ciddi bir çakışma.

Regional host, `puuids.json`'daki her puuid'nin `platform` alanından
çözülüyor (bölüm 5). Maç indirme adımında (bölüm 7) ise her maç ID'sinin
kendi platform ön eki (`EUW1_...` gibi) kullanılıyor — faz 1'in puuid→platform
eşlemesine ihtiyaç yok, ID kendi kendini yönlendiriyor.

Resumability (bölüm 3): işlenmiş puuid'ler `puuids_matchid_collected`
namespace'inde, toplanan maç ID'leri `match_ids` namespace'inde `pending`
olarak tutuluyor.

Hedef: 20-30k benzersiz maç ID'si.

---

## 7. Maç detayı indirme (`scrape_matches.py`, 2. faz)

```
/tft/match/v1/matches/{matchId}
```

**Ham JSON'u olduğu gibi sakla.** Gzipli jsonl shard'ları halinde
(`data/raw/matches_0001.jsonl.gz` gibi, shard başına ~5000 maç). Sadece işlenmiş
feature'ları saklamak yetmez — feature tanımı değişince yeniden scrape etmek
zorunda kalmayalım. Resumability (bölüm 3): bir maç ancak shard'a fiilen
yazıldıktan (veya 404 olarak onaylandıktan) sonra `match_ids` namespace'inde
`done` işaretlenir — süreç shard yazımı ortasında ölürse o parti bir dahaki
çalıştırmada tekrar denenir, veri kaybolmaz.

Filtreleme **indirmeden sonra**, ayrı bir adımda yapılacak (ham veri korunsun,
`data/raw/` hiç değiştirilmez):

- `info.tft_set_number` == config'teki `set_number`
- `info.queue_id` == config'teki `queue_id`

**`info.game_version` KULLANILMIYOR — bulgu (2026-08-30):** Set 18, TFT'nin
Unreal Engine'e geçtiği ilk set. Canlıda örneklenen ~30 maçın (iki bölge, iki
tier) **hepsi** birebir aynı, rakamsız placeholder'ı döndürdü:
`"TFT Unreal Version ?.?.?.?"`. Bu alandan çıkarılabilecek bir yama numarası
yok — `startswith` de, regex de fark etmez, veri orada değil. Bu yüzden
patch bazlı filtre kaldırıldı; `config.yaml`'da `patch_prefix` alanı yok,
`src/patch_filter.py` silindi. Şu an zaten tek bir yamanın verisi elde
edilebildiği için (set 5 günlük) `tft_set_number` + `queue_id` filtresi
yeterli. Riot bu alana gerçek bir sürüm yazmaya başlarsa, patch bazlı filtre
ayrı bir iş olarak yeniden değerlendirilecek.

Queue ID için: ranked TFT'nin `1100` olduğu **canlı veriyle doğrulandı** —
~30 örnek maçın hepsi `1100` döndürdü, bu `config.yaml`'ın `queue_id`
alanına yazıldı, kodda sabit yazılmadı. Double Up (`1160`) / Hyper Roll
(`1130`) karışırsa cluster'lar bozulur; bu yüzden filtre koddan değil
config'ten okunuyor.

Her maçın `game_datetime` alanı (zaten `info` içinde) filtrelenmiş çıktıya
olduğu gibi taşınıyor. Toplama sonunda tutulan maçların **min/max
`game_datetime`'ı loglanıyor** — bu, README'deki "şu tarihler arasında
toplandı" satırının kaynağı olacak.

Kaç maçın hangi filtreyle elendiği loglanıyor (`N raw matches -> M kept (X
wrong tft_set_number, Y wrong queue_id)`).

Çıktı: `data/processed/matches_filtered.jsonl.gz`.

---

## 8. Feature çıkarımı (`features.py`)

Her maçta 8 katılımcı var, her katılımcı bir örnek.

**Ön eleme — early_death (düzeltildi, 2026-08-30: bu bölümün önceki "at"
ifadesi yanlıştı, kod atmıyor).** `last_round` eşiğinin altında kalan
katılımcıların board'ları yarım kalmış olsa da **atılmıyor** — `early_death:
bool` olarak işaretlenip pipeline'da tutuluyor. Eşik `config.yaml`'ın
`features.min_last_round` alanından okunuyor, şu an **27**. Gerçek sayılar
(2937 maçlık örneklem): toplam **23496** board'un **3344'ü (%14.2)** eşik
altı (`early_death=true`), eğitim seti (eşik üstü) **20152** board.

Bu bayrak dört aşamada farklı kullanılıyor:
- **(a) Vektör üretimi (bu dosya):** early_death board'lar da dahil her
  katılımcı için vektör üretiliyor — hiçbiri `features_vectors.npy`'den
  çıkarılmıyor.
- **(b) HDBSCAN fit'i (bölüm 9):** sadece `early_death=false` olan alt küme
  (20152 board) ile eğitiliyor — yarım kalmış board'lar centroid konumunu
  bozmasın diye.
- **(c) Fit sonrası atama (bölüm 9):** early_death board'lar, HDBSCAN
  gürültü noktalarıyla aynı en-yakın-centroid + mesafe-eşiği mantığıyla bir
  cluster'a (veya off-meta'ya) atanıyor.
- **(d) Raporlama (bölüm 9, 10, 12, 13, 15):** early_death board'lar dahil
  tam popülasyon üzerinden hesaplanıyor — yalnızca HDBSCAN'in centroid
  hesaplamasından çıkarılıyorlar, hiçbir raporlanan istatistikten değil.
  Sebebi: early_death board'ları raporlamadan da atmak her comp'u olduğundan
  iyi gösterirdi (erken ölüm placement'ı sert biçimde 7./8.'ye çeker, bunu
  çıkarmak geri kalanın ortalamasını yapay şekilde iyileştirir) — kullanıcının
  bilmek istediği "bunu oynarsam ne olur" sorusu, oynayanların bir kısmının
  erken öldüğü gerçeğini de içermeli.

Eşiği config'e koy ve kaç örneğin `early_death` işaretlendiğini logla.

Board vektörü:
- **Unit bileşeni:** o setteki her unit için bir boyut, değer = star level (0/1/2/3)
- **Trait bileşeni:** her trait için aktif tier (`style` veya `tier_current`)
- Item'lar v1'de dışarıda kalabilir; bırakılırsa README'de belirt

Vektörü L2 normalize et. Clustering cosine mesafesiyle çalışacak.

**Bilinen kısıt — board/bench ayrımı yok (2026-08-30, web build validator'ı
sırasında bulundu, düzeltilmedi — kasıtlı karar).** `participant.units`
listesinin API'de böyle bir alanı yok: her unit kaydında sadece
`character_id`, `itemNames`, `name`, `rarity`, `tier` var — pozisyon da,
board/bench bayrağı da yok. SPEC.md'nin "Riot API pozisyon vermiyor" notu
(bölüm 0, 5) sanılandan daha köklü: hangi hex'te olduğunu değil, board'da mı
bench'te mi olduğunu bile bilmiyoruz.

Bunun somut etkisi ölçüldü: 23496 board'un **1686'sında (%7.2)** `units`
listesinin uzunluğu `level`'i (nominal board kapasitesi) aşıyor —
matematiksel olarak bunun bir kısmı bench içermek zorunda. Ama bu basit bir
"fazlalığı at" düzeltmesiyle çözülemiyor:

- **Fazlalık her zaman bench değil.** +1 fazlalıklı board'ların **%69'unda**,
  hiçbir unit çıkarılmadan tüm listeyle trait'ler Riot'un `tier_current`'ıyla
  **birebir** hesaplanıyor — yani o "fazladan" unit gerçekten oynanıyordu.
  Fazlalık büyüdükçe bu oran çöküyor (+2: %55, +3: %5, +4-5: %0) — büyük
  fazlalıklarda gerçekten açıklanamayan bir şey var, ama ne olduğu **bilinmiyor**.
- **Denenen "en düşük yatırımlı unit = bench" heuristiği (0 item → en düşük
  yıldız → en düşük maliyet sırasıyla fazlalık kadarını sil) test edildi ve
  yanlış çıktı.** İstatistiksel olarak temiz bir ayrım üretiyor (silinenlerin
  %99.7'si 0 item, kalanların sadece %37.2'si) ama gerçek `tier_current`'a
  karşı test edilince uyumsuzluk **%39'dan %92'ye çıktı** — yani heuristik,
  board'da gerçekten oynanan unit'leri bench sanıp siliyor. Somut örnek: bir
  board'da 0 item/1 yıldız görünen bir unit (`DA_18_Azir`) heuristikle
  silinince 3 farklı trait'in tier'ı bozuldu; oysa o unit gerçekten
  board'daydı. **Bu yüzden hiçbir düzeltme uygulanmadı** — yanlış bir
  düzeltme, bilinen bir kısıttan kötüdür.
- Etkinin büyüklüğü (heuristik simülasyonundan): board başına unit vektörü
  değişimi küçük (ortalama cosine mesafesi 0.029, medyan 0.019), etkilenen
  board'lar 22 cluster'a da orantılı yayılmış (tek bir comp'ta yoğunlaşma
  yok, %2.2-%11.4 arası).

Sonuç: **board vektörünün unit bileşeni, bir kısım board'da (en az %7.2)
gerçekte board'da olmayan unit'leri de içeriyor olabilir, ve bunu ayıklamanın
güvenilir bir yolu yok.** Kısıt olarak kabul edildi, düzeltilmedi (bkz. Ek:
Karar geçmişi).

Ayrıca her katılımcı için sakla: `placement`, `augments`, `match_id`, `puuid`,
`last_round`, `level`.

`match_id` özellikle önemli — hem eşleştirme hem de train/test bölmesi için lazım.

---

## 9. Clustering (`clustering.py`)

**Bu projenin kalbi. Comp etiketi veride yok, biz üretiyoruz.**

- Algoritma: **HDBSCAN.** K-Means kullanma — comp'lar ne eşit büyüklükte ne de
  küresel, K-Means bunları yanlış böler. HDBSCAN küme sayısını kendi bulur, bu
  ayrıca set değişince yeniden ayar gerektirmemesini sağlar.
- `min_cluster_size`: örnek sayısının %0.2-0.5'i civarı. Config'ten ayarlanabilir.
- Gürültü noktaları (`label == -1`): en yakın centroid'e ata, ama bir mesafe eşiği
  koy. Eşiği aşanlar `off-meta` olarak kalsın, zorla bir cluster'a sokma.

**Otomatik isimlendirme.** Elle isim verme — her çalıştırmada bozulur. Her cluster
için:
- carry unit = en yüksek ortalama star level × item sayısı olan unit
- baskın trait = en yüksek ortalama aktif tier'a sahip trait

İsim: `"{carry} — {trait}"`, örn. `"Ahri — Sorcerer"`. Aynı isim iki cluster'a
düşerse ikinciye ayırt edici bir ikinci unit ekle.

**İnsan denetimi çıktısı.** Her cluster için şunları bir tabloya bas:
cluster boyutu, ortalama placement, top-4 oranı, **core unit'ler** (frekansla),
en sık 3 trait. Bu tabloyu gözle inceleyeceğiz — cluster'ların anlamlı olup
olmadığını ölçecek ground truth yok, tek doğrulama yolu bu.

`comps.json`/`cluster_review.txt`'deki `avg_placement`, `top4_rate`, core
unit frekansları ve `common_traits`'in hepsi n_total üzerinden (bölüm 8'in
early_death bayraklı tam popülasyonu dahil) hesaplanıyor — bölüm 8'deki (d)
maddesine bakın. `n_full` = HDBSCAN eğitim alt kümesinden (early_death
hariç) bu cluster'a düşen üye sayısı; hiçbir hesaplamada kullanılmıyor, ama
`n_total - n_full` o cluster'a atanan erken-ölüm board sayısını verir.

**Core unit sayısı sabit 8 değil (bulgu, 2026-08-30, düzeltildi).** İlk
uygulama sabit top-8 kesiyordu. Gerçek veride ölçüldü: 22 cluster'ın 20'sinde
board'ların **≥%50'sinde** görülen unit sayısı, gözle bakılan doğal düşüş
noktasıyla birebir örtüşüyor (kalan 2'si — biri eşiğin 0.9pp altında kalan
sınır durumu, diğeri kademeli düşüşe sahip bir outlier — sabit 8'de zaten
belirsizdi). Bu yüzden core unit tanımı **frekans eşiğine** çevrildi: bir
unit, cluster'daki board'ların `CORE_UNIT_FREQUENCY_THRESHOLD`'ü (`clustering.py`
modül sabiti, %50) kadarında görülüyorsa core sayılır — config.yaml'da değil,
kodda adlandırılmış bir sabit (`select_core_units` fonksiyonu). `CORE_UNIT_MIN`
(5) / `CORE_UNIT_MAX` (12) ile sınırlı — eşik tek başına çok az/çok fazla unit
verirse sırasıyla en yüksek frekanslı 5'e tamamlanır veya ilk 12'de kesilir.
Gerçek veride taban/tavan hiç bağlamadı (gözlenen aralık: 6-9 unit/cluster).
Sonuç: 22 cluster'ın 11'inde core unit sayısı küçüldü, 3'ünde büyüdü (sabit
top-8'in, %50'yi geçen 9. bir unit'i yanlışlıkla dışarıda bıraktığı 3 cluster
dahil), 8'i değişmedi.

Model ve centroid'ler `models/` altına kaydedilsin.

---

## 10. Head-to-head matrisi (`matchups.py`)

Her maçta, cluster etiketi atanmış her katılımcı çifti (i, j) için: i'nin j'den
yukarıda bitip bitmediğini kaydet.

Cluster çifti (A, B) bazında topla:
- `n` = gözlem sayısı
- `rate` = A'nın B'den yukarıda bitme oranı
- **Wilson skor güven aralığı** (normal approximation kullanma, uçlarda bozuk)

`n < 100` olan hücreleri bastır, "yetersiz veri" olarak işaretle. Sayı ve aralık
olmadan hiçbir oran gösterilmeyecek.

Aynı cluster'ın kendisiyle eşleşmesi (A vs A) anlamlı, tut — mirror durumu.

---

## 11. Augment analizi (`augments.py`)

**Uygulanmadı — askıya alındı, beklemekle gelmeyecek (bulgu, 2026-08-30).**
`augments.py` kasıtlı olarak 4 satırlık bir stub ("Not yet implemented").
Sebep: katılımcı nesnesinde `augments` anahtarı **hiç yok** — `null` değer
değil, anahtarın kendisi mevcut değil. Doğrulandı: `data/raw/`'daki ham veri
baştan sona tarandı (örneklem değil, tamamı) — 3005 maç, 23984 katılımcı —
ve **0/23984** katılımcıda `augments` anahtarı bulundu. Bu Set 18'e özgü de
değil: aynı taramada bulunan Set 16 (`queue_id=1210`, 11 maç) ve Set 17
(`queue_id=1100`/`1160`, 17 maç) kayıtlarında da anahtar yok; hepsinin
`metadata.data_version` alanı `"6"`. Yani "set 5 günlük, veri henüz gelmedi"
açıklaması geçersiz — sorun set'e özgü değil. Aşağıdaki metodoloji bu yüzden
**planlanan bir iş değil**; Riot bu alanı bir gün doldurmaya başlarsa diye
referans olarak bırakılıyor, silinmedi:

Her (cluster, augment) çifti için ortalama placement ve `n`.

Ham ortalama kullanma. İki düzeltme:

1. **Lift'e çevir:** cluster'ın kendi ortalama placement'ından farkı ölç, böylece
   comp'un genel gücü augment sinyalini bastırmasın.
2. **Empirical Bayes shrinkage:** hücre ortalamasını augment'in global
   ortalamasına doğru çek —
   `shrunk = (n * cell_mean + k * global_mean) / (n + k)`, `k` config'ten (~50 ile başla).

Neden gerekli: ~40 cluster × ~250 augment = 10k hücre, 240k board ile hücre başına
~70 gözlem düşüyor. Düzeltmesiz sonuçlar gürültü olur.

Çıktı (veri gelirse): tier başına (silver / gold / prismatic) en iyi 3 augment,
yanında `n` ve shrink edilmiş lift.

**README'ye yazılacak kısıt (veri gelirse geçerli olacak):** veri sadece
*alınan* augment'leri içeriyor, *teklif edilen*leri değil. Yani ölçtüğümüz şey
"iyi augment" değil, "alındığında iyi sonuç veren augment". Seçim yanlılığı
var, gizleme.

---

## 12. Trait bazlı güçlü/zayıf analiz (`traits.py`)

Her cluster A için: rakip board'larda trait T aktifken A'nın ortalama
placement'ı vs. genel ortalaması.

Trait tier'ını da hesaba kat (2'li Sorcerer ile 6'lı Sorcerer aynı şey değil) —
en azından "aktif" ve "yüksek tier" ayrımı yap.

Aynı şekilde `n` ve güven aralığı ile raporla. Çıktı: A'nın karşısında en çok
zorlandığı 5 trait ve en rahat ettiği 5 trait.

---

## 13. Placement modeli (`model.py`)

LightGBM regressor: board feature vektörü → placement (1-8).

- **Train/test bölmesi `match_id` bazında olacak, katılımcı bazında DEĞİL.** Aynı
  maçın katılımcıları hem train hem test'e düşerse sızıntı olur ve metrik yalan
  söyler.
- Metrikler: MAE ve Spearman korelasyonu.
- Baseline karşılaştırması: her şeye 4.5 tahmin eden dummy. Model bunu geçmiyorsa
  bir sorun var demektir.
- **Tahminler [1, 8]'e kırpılır (bulgu + düzeltme, 2026-08-30).** Ham
  LightGBM çıktısı bu aralığa sınırlı değil — bu veri setinde ham test
  tahminleri [0.66, 8.68] arasına yayılıyordu, matematiksel olarak imkânsız
  placement değerleri. Kırpma `model.py`'de tanımlanan tek bir fonksiyonla
  (`clip_predictions`) yapılır; `cli.py` dahil her tüketici bunu import
  edip kullanır, kendi kırpmasını yazmaz. Kırpma öncesi/sonrası MAE ve
  Spearman ayrı raporlanır — kırpma metrikleri bozmamalı, bozarsa bir şey
  yanlıştır.

Kullanım amacı: kullanıcı hiçbir cluster'a yakın olmayan bir board girdiğinde
lookup tablosu çöker, model yine de tahmin üretir.

Feature importance'ı da bas, ilginç çıkıyor.

---

## 14. CLI (`cli.py`)

Girdi: unit listesi (display name kabul et, ID zorunlu olmasın; fuzzy eşleme iyi olur).

Akış:
1. Girdiyi board vektörüne çevir
2. En yakın cluster centroid'ini bul
3. Mesafe eşiğin altındaysa → cluster raporu; üstündeyse → sadece model tahmini
   ve "bu board bilinen bir comp'a uymuyor" uyarısı

Çıktı:
- Eşleşen comp adı + eşleşme mesafesi
- Ortalama placement, top-4 oranı, örnek sayısı
- Head-to-head eşleşmeler (oran + `n` + güven aralığı): yeterli veri olan
  (n≥100) eşleşme sayısı `2×TOP_K`'nin altındaysa tek sıralı liste,
  üstündeyse en yüksek/en düşük üstünlük 5'erli liste — "en iyi/en kötü"
  değil "üstünlük" dili kullanılır (bölüm 0); hiçbir eşleşme %50 sınırını
  geçmiyorsa bunu belirten bir not eklenir.
- Augment durumu: sabit bir "veri yok" açıklama satırı — tier bazlı öneri
  YOK, augment analizi askıya alındı (bölüm 11)
- Zorlandığı / rahat ettiği trait'ler
- Modelin tahmin ettiği placement ([1, 8]'e kırpılmış, bkz. bölüm 13)

---

## 15. Item analizi (`items.py`)

**Item vektöre eklenmiyor.** Comp tanımı (clustering, bölüm 9) item'dan
bağımsız kalır — aynı comp'u oynayan iki oyuncu farklı item taşıyabilir,
item board vektörünün star level + trait bileşenini bozmamalı. Bu, ayrı,
tamamen bağımsız bir analiz katmanı: "bu comp'ta, şu carry unit üzerinde
hangi item ortalama placement'ı iyileştiriyor?" sorusuna cevap veriyor.

Her (cluster, unit, item) üçlüsü için ortalama placement ve `n`.

Ham ortalama kullanma — bölüm 11'de augment için planlanan iki düzeltmenin
aynısı:

1. **Lift'e çevir:** cluster'ın kendi ortalama placement'ından farkı ölç.
2. **Empirical Bayes shrinkage:** hücre ortalamasını item'in global
   ortalamasına doğru çek —
   `shrunk = (n * cell_mean + k * global_mean) / (n + k)`, `k` config'ten.

Sadece tamamlanmış item'ları raporla, component'leri değil (component'ler
zaten tamamlanmışın parçası, ayrı raporlamak çift sayım/gürültü olur).

Çıktı: her cluster için, cluster'ın carry unit'leri (core_units) üzerinde en
iyi 3 item, yanında `n` ve shrink edilmiş lift. `n < 50` olan hücreleri
bastır (sıralamaya hiç dahil etme — tek bir şanslı/şanssız gözlem "en iyi 3"
listesine sessizce sızmasın).

Çıktı dosyası: `data/processed/items.json` (SPEC-WEB.md bölüm 3.8).

`other` kovasının büyük kısmı (430'unun 360'ı, %83.7) gerçek maç verisinde
hiç görünmeyen ölü kayıt — bilinçli olarak filtrelenmiyor çünkü bu kova zaten
hiçbir aşamada okunmuyor (`items.py` sadece `completed`'i işliyor, o da
39/39 temiz); sayılar sadece gözlem olarak `items.json`'ın
`_metadata.item_pool_usage` alanında tutuluyor.

**Bilinen kısıt — confounding (2026-08-30, düzeltilmedi, sadece belgelendi).**
Bu lift ölçümü ayrıştıramadığı bir karışıklık taşıyor: bir item'ı
tamamlamış olmak (özellikle niyet edilen carry üzerinde) zaten iyi giden bir
maçla korelasyon içinde — yeterli altın/roll, erken elenmemiş olmak. Yani
ölçülen şey kısmen item'ın kendi katkısı, kısmen de "bu item'ı toplayabilmiş
olmak"; placement verisinden ikisi ayrıştırılamaz. Augment seçimi göreceli
olarak dışsal (küçük rastgele bir kümeden sunuluyor) olduğu için bu kaygı
augment'te zayıf, item'da güçlü. Shrinkage veya lift-vs-baseline bunu
düzeltmez — bu, hücrenin ortalamasında bir gürültü sorunu değil, "bu hücre
hangi board'larda var" sorusunda bir seçim etkisi. Kod değiştirilmedi;
`items.json`'ın `_metadata.known_limitation` alanında açıkça yazılı, frontend
gösterebilsin diye.

**Kapsam istatistiği.** `n < 50` eşiği (cluster, unit, item) üçlüsü üzerinde
çalıştığı için kombinatorik olarak çok şey eleyebiliyor. `items.py` her
çalıştığında konsola ve `items.json`'ın `_metadata.coverage` alanına
şunları yazar: toplam (cluster, core_unit) çifti sayısı, en az 1 item
üretebilen çift sayısı, tam 3 item üretebilen çift sayısı, ve eşik yüzünden
düşen hücre oranı. 2937 maçlık örneklemde: 166 çiftin 125'i en az 1 item
üretti, 93'ü tam 3'ü, ham hücrelerin %92.7'si `n<50` yüzünden elendi.

**Emblem — kontrol edildi, features.py'de düzeltme yapılmadı (bulgu,
2026-08-30).** Önce varsayım şuydu: `itemNames` içindeki emblem item'ları
(`DA_18_EmblemFloraFatalisAugment` gibi) bir trait ekliyor ama aktif trait
sayımına dahil değil. **Gerçek veride doğrulandı, varsayım yanlış çıktı:**
Riot'un `participant.traits` alanı (`tier_current`/`num_units`) emblem'in
katkısını **sunucu tarafında zaten hesaba katıyor** — örnek: Cassiopeia
"Flora Fatalis Emblem" taşıyor, board'da native olarak sadece Fiddlesticks o
trait'e sahip (1 unit), ama API `num_units=2` diyor (Fiddlesticks + emblem
sayesinde Cassiopeia). Set genelinde emblem taşıyan (katılımcı, trait)
çiftlerinin **%95'inde** `num_units` = native + emblem sayımı birebir
tutuyor. **Bu yüzden features.py'ye emblem için +1 eklemek çift sayıma yol
açardı — yapılmadı.**

Kalan ~%4.6'lık uyumsuzlukta API'nin sayısı native+emblem'den de **bir
fazla** çıkıyor — muhtemelen bölüm 4'teki `other` kovasındaki (430 item,
sınıflandırılamamış — bkz. aşağıdaki düzeltme notu) bazı item'ların da trait
verdiğini gösteriyor, ama bu ayrı ve çözülmemiş bir soru; kapsam dışı
bırakıldı.

`static_data.py` yine de emblem→trait eşlemesini üretir
(`emblem_traits.json`) — statik veri olarak değerli, sadece features.py'de
bir düzeltme tetiklemedi.

---

## 16. Bitmiş sayılma kriterleri

- [ ] Her aşama tek başına, kaldığı yerden çalıştırılabiliyor (canlı
      doğrulandı — bölüm 3; otomatik test kapsam dışı, SPEC bunu istemedi)
- [ ] Kodda tek bir hardcoded unit/trait/item/augment ismi yok
- [ ] `403` scraper'ı sessizce bozmuyor
- [ ] Cluster inceleme tablosu basılıyor ve gözle bakınca comp'lar tanınıyor
- [ ] Oransal metrikler (head-to-head placement oranı §10, trait exposure
      §12) her zaman `n` ve güven aralığıyla geliyor — Wilson/normal-approx
      CI sadece oranlar/ortalamalar için tanımlı. Item lift (§15, iki
      ortalama arasındaki fark, oran değil) bu kurala girmiyor, sadece `n`
      ile raporlanır; bölüm 15 ile çelişmiyor.
- [ ] Model train/test bölmesi match bazında
- [ ] README'de: veri toplama tarihi, yama, bölgeler, elo aralığı, maç sayısı,
      ve kısıtlar bölümü (head-to-head ≠ counter, yüksek elo örneklemi,
      yama ömrü) — augment seçim yanlılığı kısıtı yok, augment analizi kapsam
      dışı (bölüm 11)

---

## 17. Yapılmayacaklar (kapsam disiplini)

- Web arayüzü bu dokümanın kapsamı dışında — ayrı bir spec'te tanımlı:
  **SPEC-WEB.md**. CLI (bölüm 14) bu pipeline'ın birincil arayüzü olmaya
  devam ediyor; web arayüzü CLI'nin yerini almıyor, aynı
  `data/processed/`/`data/static/` çıktılarını tarayıcıda gösteren ayrı,
  ek bir katman.
- Canlı/otomatik güncelleme yok. Tek yama, tek koşu.
- Pozisyon (unit dizilimi) analizi yok — API zaten vermiyor.
- Ekonomi / roll timing analizi yok — final board'dan çıkarılamaz.
- Birden fazla set desteği yok. Kod parametrik olsun ama tek sete koşulacak.
- Augment analizi yok — veri yok (ayrıntı bölüm 11'de).

---

## Ek: Karar geçmişi — denenip reddedilen yaklaşımlar

Bu bölüm sadece **uygulanmamış** kararları kaydeder. Uygulanmış olanlar ilgili
bölümlerde yazılı (emblem düzeltmesi §15, confounding §15, core_units §9,
early_death eğitim/raporlama ayrımı §8, game_version §7). Amaç: gelecekteki
bir session aynı çıkmaz yollara girmesin.

**early_death board'larını tamamen atma — reddedildi.** SPEC'in ilk taslağı
(bölüm 8) "çok erken elenen oyuncuları at" diyordu — vektör üretiminden ve
her raporlanan istatistikten tam çıkarmayı öneriyordu; ilk uygulama da
buydu. Reddedildi çünkü survivorship bias'a yol açtı: early_death board'ları
raporlamadan da atmak her cluster'ın `avg_placement`/`top4_rate`'ini
olduğundan iyi gösteriyordu (erken ölüm placement'ı sert biçimde 7./8.'ye
çekiyor, bunları çıkarmak geri kalanın ortalamasını yapay şekilde
iyileştiriyor). Bir dummy-baseline testinde yakalandı: her şeye 4.5 tahmin
eden modelin MAE'sinin üniform 1-8 dağılımda tam 2.0 olması beklenirken,
sadece eğitim alt kümesi (early_death'siz) üzerinde 1.81 ölçüldü. Yapılan:
eğitimden çıkar/raporlamada tut ayrımı — early_death board'lar HDBSCAN
fit'inden çıkarılıyor (yarım board centroid'i bozmasın), ama fit sonrası
en-yakın-centroid ile bir cluster'a atanıp tüm raporlanan istatistiklere
dahil ediliyor. Ayrıntı bölüm 8 ve 9'da.

**Set aidiyeti üzerinden kontaminasyon ölçümü — yöntem çıkmaz.** "Phantom
Emblem `DA_` önekiyle geliyor ama Set 17 içeriği, öyleyse 502 item içinde başka
kaçı yabancı?" sorusu ölçülmeye çalışıldı. Yöntem çalışmıyor: 502/502 item
yalnızca Set 18'in kendi listesinde geçiyor, çünkü her set kendi kopyasını farklı
`apiName` ile tutuyor. Phantom'u yakalayan şey item'ın başka sette listelenmesi
değil, **verdiği trait'in** başka setin trait listesinde olmasıydı
(`TFTEvent5YR_Phantom`). Dolayısıyla `component` ve `other` kovalarında
kontaminasyon "sıfır" değil **"ölçülemiyor"** — referans verilecek alanları yok.
Trait listesi bu arada doğrulandı: hiçbir filtreden geçmiyor, 36'sı da eksiksiz;
sorun trait tarafında değil. Phantom'a zorla eşleştirme yapılmadı.

**Güncelleme (2026-08-30, web build validator'ı sırasında bulundu):** "36'sı
da eksiksiz" artık doğru değil — `extract_traits()` şimdi bir filtre
uyguluyor, çıktı 35 trait. `DA_18_Eclipse` cdragon'da diğer 36 kayıtla
birlikte trait listesine giriyordu ama tek `effect`'i `minUnits: null`
taşıyor ve açıklaması ("kills the lowest Health enemy") bir champion
origin/class'ı değil, bir PvE/encounter mekaniğini tarif ediyor — canlı
maç verisinde 86 board'da `tier_current=1` olarak görüldü, yani sahte bir
"veri henüz gelmedi" durumu değil, gerçek katılımcılarda oluşan bir sinyal.
`extract_traits()` artık hiçbir effect'inde sayısal `minUnits` kalmayan
trait'leri (champion filtresindeki `traits: []` mantığının trait
tarafındaki eşdeğeri) dışlıyor. Bu, yukarıdaki paragrafın "trait tarafında
sorun yok" sonucunu geçersiz kılmıyor (Phantom'la ilgisi yok, ayrı bir
bulgu) — sadece "36'sı da eksiksiz" sayısını düzeltiyor.

**Kullanım tabanlı item filtresi — reddedildi.** 502 item'ın %72.1'i maç
verisinde hiç görünmüyor. Yine de `DA_` filtresi kullanım tabanlı bir filtreyle
değiştirilmedi, çünkü: (a) false negative üretmiyor — maçta görülen 140 item'ın
140'ı havuzda; (b) `items.py`'nin okuduğu tek kova olan `completed` zaten %100
temiz (39/39 yaygın kullanımda); (c) ölü kayıtların tamamı hiçbir aşamada
okunmayan `other`'da. Asıl gerekçe: `static_data.py` şu an maç verisinden
**bağımsız**, ve bu korunması gereken bir özellik — kullanım tabanlı filtre
çıktıyı dataset büyüklüğüne bağımlı hale getirir, kırılganlık ekler, karşılığında
hiçbir şey kazandırmaz. Sayılar sadece gözlem olarak
`_metadata.item_pool_usage`'da.

**`n < 50` eşiği — korundu.** Ham hücrelerin %92.7'sinin elenmesi endişe
yarattı ama asıl kontrol şuydu: item üretemeyen (cluster, unit) çiftleri nerede
yoğunlaşıyor? Cevap: `n_total < 300` olan cluster'larda 5/8, `n_total > 1000`
olanlarda 0-2. Net bir örneklem-büyüklüğü etkisi; o cluster'lar zaten güvenilmez,
item yokluğu doğru davranış. Eşik sorunu değil, eşik değiştirilmedi.

**İsim bazında yinelenen ID'ler — tespit edildi, düzeltilmedi.** 502 item'da 163
duplicate-isim grubu var (aynı isim, farklı ID). 161'i tamamen `other` içinde ve
her varyantı 0 kullanım. 2'si emblem'de: "Flora Fatalis Emblem"
(`DA_18_EmblemFloraFatalis`=548 kullanım / `...Augment`=774 — **gerçek bir
bölünme**) ve "Phantom Emblem" (0/0). `completed` ve `component` kovalarında hiç
duplicate yok; `items.py` yalnızca `completed` işlediği için hiçbir
(cluster, unit, item) hücresi etkilenmiyor, aggregation ID bazında kaldı.
**Uyarı:** ileride emblem istatistiği eklenirse Flora Fatalis bölünmesi aynı
item'ın verisini ikiye böler ve her iki parça da `n<50`'nin altına düşebilir.
O noktada isimden türetilmiş kanonik anahtar gerekir.

**Bench unit'lerini board vektöründen çıkarma — reddedildi (2026-08-30).**
Bölüm 8'in belgelediği kısıt (`participant.units`'te board/bench ayrımı yok,
board'ların %7.2'sinde unit sayısı `level`'i aşıyor) için bir düzeltme
denendi: fazlalık kadar unit'i "0 item → en düşük yıldız → en düşük maliyet"
sırasıyla en bench'e-benzeyen'den seçip vektörden/trait sayımından çıkarmak.
Reddedildi çünkü **kanıtla çelişiyor**, varsayımla değil:

- Heuristik gerçek `tier_current`'a karşı test edildi. Uyumsuzluk **%39'dan
  %92'ye çıktı** — yani heuristik gerçek board unit'lerini bench sanıp
  siliyordu, rastgele gürültü eklemekten de kötüydü.
- Somut örnek: bir board'da heuristiğin "en bench'e benzer" seçtiği unit
  (`DA_18_Azir`, 0 item, 1 yıldız) aslında board'daydı — silinince 3 farklı
  trait'in hesaplanan tier'ı (Executioner, Blackthorn, Summoner) gerçek
  değerinden saptı, oysa tüm listeyle (hiç kesmeden) o board zaten
  `tier_current`'la birebir tutuyordu.
- Fazlalık miktarına göre kırılım tek bir mekanizma olmadığını gösterdi:
  +1 fazlalıklı board'ların %69'u tam listeyle zaten doğru (o unit gerçekten
  oynanıyordu), +3 ve üstünde bu oran %5'e ve %0'a düşüyor (orada gerçekten
  açıklanamayan bir şey var, ama heuristik onu da bulamıyor).
- Sebep muhtemelen `level` alanının board kapasitesini her zaman güvenilir
  temsil etmemesi (bkz. +1 fazlalığın çoğunun meşru çıkması) — ama bu
  kanıtlanmadı, sadece heuristiğin başarısızlığı kanıtlandı.

Yapılan: hiçbir şey — düzeltme uygulanmadı, kısıt olarak belgelendi (bölüm
8). Yanlış bir düzeltme, bilinen bir kısıttan kötüdür: en azından kısıt
dürüst, heuristik ise board'un %39'unu değil %92'sini bozan sessiz bir
hataydı. `unit_count > level` sinyali (kesin bench kanıtı olan %7.2'lik
kesim) ile "gizli, kapasitede/altında kalan bench" arasındaki ayrım da
çözülmedi — bölüm 8'deki dolaylı kontrol (tam-kapasite board'larda "çıplak"
unit oranı %30.4, kapasite-altındakilerde %24.5-28.4) net bir sıçrama
göstermedi, yani en azından tam-kapasite board'larda yaygın bir gizli-bench
kirliliğine dair güçlü bir kanıt yok — ama bu profilin kendisi de yukarıdaki
heuristik testinde güvenilmez çıktığı için zayıf bir kanıt.