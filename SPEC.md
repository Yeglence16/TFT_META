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
| `401` / `403` | **Anında dur.** Retry etme. Kuyruktaki öğeyi "başarısız" işaretleme. Anlaşılır bir hata bas: key expire olmuş. |
| `404` | Atla, logla, devam et |
| `5xx` | Exponential backoff, en fazla 3 deneme |

`401`/`403` davranışı özellikle önemli: dev key 24 saatte bir expire oluyor. İstemci
bunu "bu maç alınamadı" diye yorumlarsa binlerce maç sessizce çöpe gider ve bunu
ancak sonuçlar tuhaf çıkınca fark ederiz. Sessiz veri kaybı olmayacak.

**Düzeltildi — `401` başta ele alınmıyordu, canlı 15k-maç indirme koşusunda
bulundu (2026-08-31).** SPEC'in ilk hali sadece `403`'ü key-expiry olarak
tanımlıyordu. Gerçek bir maç indirme koşusunda (`GET
/tft/match/v1/matches/{matchId}`), aynı key az önce (aynı oturumda, ~2.5 saat
önce) league/match-id endpoint'lerinde sorunsuz 200 dönerken, bu endpoint'te
`401 Unauthorized` döndürdü — `403` değil. `riot_client.py`'nin `get()`'i o an
sadece `429`/`403`/`404`/`5xx`'i tanıyordu; `401` hiçbir branch'e girmeden en
sona düşüp ham `raise_for_status()` ile kontrolsüz bir `httpx.HTTPStatusError`
fırlatıp süreci çirkin bir traceback'le çökertti — SPEC'in burada istediği
"anlaşılır hata bas, temiz dur, resumable kal" davranışı yerine. Kök neden
muhtemelen aynı ~24 saatlik dev-key expiry'si, sadece bu endpoint'te Riot'un
tarafında `403` yerine `401` olarak yüzeye çıkmış — yani key expiry artık
endpoint'e göre iki farklı HTTP koduyla gelebiliyor, biri değil. Düzeltme
yapısal: `riot_client.py`'de `response.status_code in (401, 403)` artık
ikisini de aynı `RiotApiKeyExpiredError`'a yönlendiriyor, hangi kodun
geldiği hata mesajında raporlanıyor. Test eklendi
(`tests/test_riot_client.py::test_401_raises_immediately_with_no_retry`).
Koşunun kendisinde veri kaybı olmadı — `401` ilk indirme isteğinde geldiği
için hiçbir shard yazılmamıştı, state DB (`match_ids`) koşu öncesiyle birebir
aynı kaldı; yeni key ile aynı komut (`python src/scrape_matches.py`) kaldığı
yerden sorunsuz devam etti.

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
(**14513 maçlık koşu, 2026-08-31**): toplam **116104** board'un **16481'i
(%14.19)** eşik altı (`early_death=true`), eğitim seti (eşik üstü) **99623**
board. (İlk ölçüm 2937 maçlık dev sample'daydı: 23496 board, 3344 early_death
[%14.2], 20152 eğitim — oran 5 kat veriyle neredeyse birebir korundu.)

Bu bayrak dört aşamada farklı kullanılıyor:
- **(a) Vektör üretimi (bu dosya):** early_death board'lar da dahil her
  katılımcı için vektör üretiliyor — hiçbiri `features_vectors.npy`'den
  çıkarılmıyor.
- **(b) HDBSCAN fit'i (bölüm 9):** sadece `early_death=false` olan alt küme
  (99623 board) ile eğitiliyor — yarım kalmış board'lar centroid konumunu
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

Vektörü L2 normalize et. Clustering euclidean mesafesiyle çalışıyor (bölüm 9'a
bakın — L2-normalize vektörlerde cosine'in monoton eşdeğeri, 2026-08-31'de
ölçekte cosine'in O(n²) bellek duvarına çarpması yüzünden değiştirildi).

**Bilinen kısıt — board/bench ayrımı yok (2026-08-30, web build validator'ı
sırasında bulundu, düzeltilmedi — kasıtlı karar).** `participant.units`
listesinin API'de böyle bir alanı yok: her unit kaydında sadece
`character_id`, `itemNames`, `name`, `rarity`, `tier` var — pozisyon da,
board/bench bayrağı da yok. SPEC.md'nin "Riot API pozisyon vermiyor" notu
(bölüm 0, 5) sanılandan daha köklü: hangi hex'te olduğunu değil, board'da mı
bench'te mi olduğunu bile bilmiyoruz.

Bunun somut etkisi ölçüldü: 23496 board'luk ilk örneklemde **1686'sında
(%7.2)** `units` listesinin uzunluğu `level`'i (nominal board kapasitesi)
aşıyordu; 116104 board'luk 15k koşusunda yeniden ölçüldü (2026-08-31):
**9497'sinde (%8.2)** — oran aynı büyüklük mertebesinde, ~1 puan yukarı.
Matematiksel olarak bunun bir kısmı bench içermek zorunda. Ama bu basit bir
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
  yok, %2.2-%11.4 arası). *(Bu alt madde 2937 maçlık ilk örneklemin — 22
  cluster'ın — ölçümü; heuristik simülasyonu 15k koşusunda [19 cluster]
  yeniden çalıştırılmadı, sonuç uygulanmadığı için de gerek görülmedi —
  aşağıdaki kısıt kararı değişmiyor.)*

Sonuç: **board vektörünün unit bileşeni, bir kısım board'da (%7.2 — 15k
koşusunda %8.2 ölçüldü, bkz. yukarı) gerçekte board'da olmayan unit'leri de
içeriyor olabilir, ve bunu ayıklamanın güvenilir bir yolu yok.** Kısıt olarak
kabul edildi, düzeltilmedi (bkz. Ek:
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
  Config'teki oran (%0.3) 3k-maçlık dev sample'da min_cluster_size=60'a, 15k
  koşusunda (99623 eğitim board'u) min_cluster_size=**299**'a karşılık geliyor
  — sabit değil, örnek sayısıyla birlikte büyüyor, tasarım gereği bu. **19
  cluster'ın az olup olmadığı 2026-08-31'de ölçülerek sorgulandı** — cluster
  sayısı bir tercih değil, veriden çıkıyor; 4 fraction'lık tam bir tarama
  0.003'ün doğru nokta olduğunu doğruladı, değiştirilmedi. Ayrıntı Ek: Karar
  geçmişi, "min_cluster_size'ı düşürme — reddedildi".
- Gürültü noktaları (`label == -1`): en yakın centroid'e ata, ama bir mesafe eşiği
  koy. Eşiği aşanlar `off-meta` olarak kalsın, zorla bir cluster'a sokma.

**Mesafe metriği: euclidean, cosine değil (değiştirildi, 2026-08-31 — ölçekte
gerçek bir bellek çökmesi bulundu).** İlk uygulama `HDBSCAN(metric="cosine")`
kullanıyordu, 3k-maçlık dev sample'da (~20k eğitim board'u) sorunsuz çalışıyordu.
15k koşusunda (99623 eğitim board'u) canlı çöktü:

```
numpy._core._exceptions._ArrayMemoryError: Unable to allocate 73.9 GiB
for an array with shape (99623, 99623) and data type float64
```

**Kök neden:** sklearn'ün HDBSCAN'i `metric="cosine"` için sadece brute-force
bir yol sunuyor (`_hdbscan_brute` → yoğun N×N `pairwise_distances` matrisi,
O(n²) bellek). 3k ölçeğinde (20152 board) bu matris ~3.25 GB'tı, sorun
çıkarmıyordu; 15k ölçeğinde (99623 board, ~4.94×) 73.9 GB'a çıktı — makine
31.7 GB RAM'e sahip, hiç yaklaşmıyor. Bu O(n²) büyüme kalıcı: 25k'lik nihai
hedefte (~167k eğitim board'u) matris ~223 GB'a çıkardı — daha büyük bir
makineyle "çözülecek" bir sorun değil, algoritma seçimiyle çözülmesi gereken
bir sorun.

**Neden euclidean matematiksel olarak eşdeğer:** vektörler zaten L2-normalize
(bölüm 8). Birim vektörlerde `‖a−b‖² = 2(1−cos(a,b))` — euclidean mesafe,
cosine mesafenin **monoton** bir dönüşümü. HDBSCAN tamamen göreli mesafe
sıralamasından kuruluyor (mutual reachability graph + minimum spanning tree),
monoton bir dönüşüm bu sıralamayı değiştiremez. Bu sadece teorik bir argüman
değil — sklearn `algorithm="auto"` (varsayılan) `metric="euclidean"`'ı
`KDTree.valid_metrics`'te olduğu için KD-tree tabanlı yola (`_hdbscan_prims`)
yönlendiriyor, brute-force'a değil — bu, sklearn 1.8'in kaynak kodu
(`_hdbscan/hdbscan.py`) okunarak doğrulandı, varsayılmadı.

**Nasıl doğrulandı (canlı ölçüm, uygulamadan önce):**
- Orijinal 2937 maçlık örneklem (untouched ham shard'lardan yeniden kurulup
  doğrulandı: 2937/23496/20152/min_cluster_size=60 — dördü de eski
  belgelenen sayılarla birebir) üzerinde cosine vs euclidean karşılaştırıldı:
  **22=22 cluster, off_meta oranı 0.1291=0.1291, Adjusted Rand Index 0.9995**
  (1.0 = tam eşleşme) — neredeyse mükemmel örtüşme.
- Cosine sonucu ayrıca diskteki GERÇEK eski `comps.json`'a karşı çapraz
  doğrulandı: 22=22 cluster — metodolojinin sağlam olduğunu gösteriyor.
- Tam ölçekte (99623×100) euclidean bellek kullanımı **~554-562 MB**'de sabit
  kaldı (cosine'in 73.9 GB'ına kıyasla), **1033 saniyede (~17 dk)** tamamlandı,
  **19 cluster** buldu.
- **Dürüst sınır:** eşdeğerlik yalnızca ESKİ ölçekte (20152 nokta) doğrudan
  doğrulanabildi — cosine tam ölçekte zaten çökme yaşadığı için tam ölçekte
  doğrudan bir ARI karşılaştırması yapılamıyor (bu zaten sorunun kendisi).
  Tam ölçekteki 22→19 cluster düşüşü büyük ölçüde veri hacminden kaynaklanıyor
  (bkz. aşağıdaki min_cluster_size kararı), metrik değişikliğinden değil.

**Downstream eşikler etkilenmedi.** `config.yaml`'ın `noise_distance_threshold`'u
ve `meta.json`'ın `match_threshold`'u — ikisi de `clustering.py`'nin kendi
`_nearest_centroid_labels()`/`reassign_noise()`'unda ve `cli.py`/`app.js`'in
comp eşleştirmesinde kullanılıyor; bunların hepsi L2-normalize vektörler
üzerinde **kendi** cosine dot-product hesabını yapıyor, HDBSCAN'in iç
metriğini hiç okumuyor. `cluster_selection_epsilon` de hiç set edilmiyor
(sklearn'ün etkisiz 0.0 varsayılanında kalıyor, metrikten bağımsız zaten
etkisiz). Sonuç: hiçbir downstream sabit dönüştürülmedi.

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

*(Bu karşılaştırma 2937 maçlık ilk örneklemin 22 cluster'ına aitti — karar
[frekans eşiği, `CORE_UNIT_MIN`/`MAX`] koddan değişmedi, 15k koşusunda [19
cluster] yeniden ölçülmedi; gözlenen 6-9 unit/cluster aralığı 15k'de de
sağlandı [bkz. gerçek `cluster_review.txt`], taban/tavan yine bağlamadı.)*

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
baştan sona tarandı (örneklem değil, tamamı) — ilk doğrulamada 3005 maç,
23984 katılımcı, **0/23984**; 15k koşusu sonrası tüm ham veri (5 shard)
yeniden tarandı (2026-08-31) — **15003 maç, 119787 katılımcı, yine 0/119787**
katılımcıda `augments` anahtarı bulundu. Bu Set 18'e özgü de değil: aynı
taramada bulunan Set 16 (`queue_id=1210`, 11 maç) ve Set 17
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

`other` kovasının büyük kısmı (430'unun 359'u, %83.5 — 15k koşusunda yeniden
ölçüldü, ilk ölçüm 360/%83.7'ydi) gerçek maç verisinde hiç görünmeyen ölü
kayıt — bilinçli olarak filtrelenmiyor çünkü bu kova zaten hiçbir aşamada
okunmuyor (`items.py` sadece `completed`'i işliyor, o da 39/39 temiz);
sayılar sadece gözlem olarak `items.json`'ın `_metadata.item_pool_usage`
alanında tutuluyor.

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
düşen hücre oranı. 2937 maçlık ilk örneklemde: 166 çiftin 125'i en az 1 item
üretti, 93'ü tam 3'ü, ham hücrelerin %92.7'si `n<50` yüzünden elendi. 14513
maçlık koşuda (2026-08-31, 19 cluster'la — cluster sayısı azaldığı için çift
sayısı da düştü): **144 çiftin 138'i en az 1 item üretti (%95.8), 129'u tam
3'ü (%89.6), ham hücrelerin %86.0'sı `n<50` yüzünden elendi** — kapsam
belirgin şekilde iyileşti, beklendiği gibi (5 kat veri, aynı `n<50` eşiği).

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
early_death eğitim/raporlama ayrımı §8, game_version §7, euclidean metriği
§9). Amaç: gelecekteki bir session aynı çıkmaz yollara girmesin.

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

**`n < 50` eşiği — korundu.** Ham hücrelerin %92.7'sinin (2937 maçlık ilk
örneklem) elenmesi endişe yarattı ama asıl kontrol şuydu: item üretemeyen
(cluster, unit) çiftleri nerede yoğunlaşıyor? Cevap: `n_total < 300` olan
cluster'larda 5/8, `n_total > 1000` olanlarda 0-2. Net bir örneklem-büyüklüğü
etkisi; o cluster'lar zaten güvenilmez, item yokluğu doğru davranış. Eşik
sorunu değil, eşik değiştirilmedi — 14513 maçlık koşuda elenme oranı %86.0'a
düştü (bölüm 15'e bakın), aynı yönde: daha çok veri, daha az elenme, eşiğin
kendisi hâlâ değiştirilmedi. `n_total < 300`/`> 1000` kırılımı 15k'de yeniden
ölçülmedi.

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
board'ların %7.2'sinde [15k koşusunda %8.2, bölüm 8'e bakın] unit sayısı
`level`'i aşıyor) için bir düzeltme
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

**Cosine metriği — ölçekte terk edildi (2026-08-31).** `clustering.py`
başlangıçta `HDBSCAN(metric="cosine")` kullanıyordu (bölüm 9'un ilk hali).
3k-maçlık dev sample'da (~20k eğitim board'u) sorunsuzdu, ama 15k koşusunda
(99623 eğitim board'u) canlı çöktü: sklearn'ün cosine için tek sunduğu yol
brute-force'tur (`_hdbscan_brute` → yoğun N×N `pairwise_distances`, O(n²)
bellek) — 99623×99623 float64 matris 73.9 GiB istiyordu, makinede 31.7 GB
RAM var. `metric="euclidean"`'a geçildi: L2-normalize vektörlerde
`‖a−b‖²=2(1−cos(a,b))` olduğu için cosine'in monoton eşdeğeri, ve sklearn
`algorithm="auto"` bunun için KD-tree tabanlı bir yola gidiyor (kaynak kodundan
doğrulandı) — O(n²) bellek sorunu tamamen ortadan kalkıyor. Değiştirmeden önce
doğrulandı, varsayılmadı: eski 2937 maçlık örneklemde (cosine hâlâ
çalışabildiği ölçekte) iki metrik karşılaştırıldı — 22=22 cluster, off_meta
oranı 0.1291=0.1291, **Adjusted Rand Index 0.9995**. Tam ölçekte euclidean
~554-562 MB bellekte, ~17 dakikada tamamlandı (cosine hiç tamamlanamadı).
Ayrıntı ve downstream-etkisi-yok kanıtı bölüm 9'da. Bu madde bölüm 9'un
kendisiyle çelişmiyor — cosine'in artık **reddedilmiş** olduğunu bu bölümün
"denenip reddedilenler" formatında kayda geçiriyor.

**min_cluster_size'ı sabit 60'ta tutma (örnek sayısıyla ölçeklemek yerine) —
reddedildi (2026-08-31, ölçüldü).** Euclidean'a geçiş kararı verilirken 15k
koşusunda ham HDBSCAN noise'unun %12.9'dan (eski) %30.1'e (yeni,
min_cluster_size=299) çıktığı görüldü — bunun min_cluster_size'ın büyümesinden
mi kaynaklandığı sorgulandı. Test edildi: min_cluster_size 99623 boarda göre
ölçeklenmeyip eski mutlak değerinde (**60**) sabit tutulsaydı ne olurdu?
Sonuç **83 cluster, %68.8 noise** — 299'dan çok daha kötü, iyi değil. Yani
noise artışı min_cluster_size'ın "çok büyük" olmasından kaynaklanmıyor; tam
tersine, sabit tutmak (verideki payını küçültmek) noise'u ciddi biçimde
kötüleştiriyor. Ayrıca ham noise'un büyük kısmı zaten nearest-centroid ile
geri kazanılıyor: nihai (kullanıcıya yansıyan) off_meta oranı %14.41 (eski)
→ %14.98 (yeni) — ham noise'daki %12.9→%30.1 sıçramasının izlenimi verdiği
kadar dramatik değil. Yapılan: hiçbir şey — `min_cluster_size_fraction`
config değeri (%0.3) korundu, sabit mutlak değere geçilmedi.

**Az örneklemde sahte cluster ayrımı (2026-08-31, 15k koşusuyla ortaya
çıktı, geriye dönük doğrulandı).** 2937 maçlık ilk koşuda `c_20` ("Aphelios
— Vanguard") ve `c_21` ("Aphelios & Sentinel — Vanguard") iki ayrı comp
olarak çıkmıştı. Geriye dönük ölçüldü (orijinal ham shard'lardan yeniden
kurulan 23496 board'luk veri + backup'taki orijinal `cluster_labels.npy`
üzerinden, `data/processed/`'a hiçbir şey yazılmadan):

- **Centroid'ler anormal derecede yakındı.** 22 cluster arasındaki 231
  çiftin cosine mesafesi sıralandığında c_20-c_21 çifti **0.0467** ile açık
  ara en yakını — ikinci en yakın çift (`c_10`-`c_11`) **0.3017**, yani
  **6.46 kat** daha uzak. Tipik bir çift-cluster mesafesinin çok altında.
- **Tek gerçek ayırt edici Taric'ti.** `c_20`'de board'ların **%95.2**'sinde
  `DA_Taric18` var, `c_21`'de sadece **%0.8**'inde (94.4 puan fark, en büyük
  fark bu). **Düzeltme — ilk taslakta "diğer tüm unit'lerde fark %1'in
  altında" deniyordu, bu iddia yeniden ölçülünce doğrulanmadı:** Elise
  (%3.1 vs %35.9, 32.8 puan), Rakan (%5.7 vs %36.1, 30.4 puan) ve Alune
  (%28.9 vs %10.1, 18.8 puan) da belirgin farklar gösteriyor — Taric kadar
  büyük değil ama %1'in çok üstünde. Doğru ifade: Taric **en baskın ve tek
  başına açıklayıcı** fark (94.4 puan, ikinci sıradaki Elise'in >2.5 katı),
  ama tek sinyal o değil.
- **avg_placement 4.082 (`c_20`) vs 5.42 (`c_21`)** — 1.3 sıralık fark,
  `comps.json`'daki kayıtlı değerlerle birebir doğrulandı.
- **"Erken ölenler ayrı cluster olmuş" hipotezi test edildi ve
  ÇÜRÜTÜLDÜ.** `c_21`'in geç ölen (early_death=false) alt kümesi (n=684)
  avg_placement **4.917**'de kaldı — `c_20`'nin KENDİ geç-ölen alt
  kümesinin (n=1100) **3.81**'ine hiç yaklaşmadı (yakınsaması beklenirdi,
  yakınsamadı). Ayrıca `c_20`'nin erken-ölen alt kümesinde (n=83) Taric
  hâlâ **%94.0** — yani Taric ayrımı early_death'ten bağımsız, board'un ne
  zaman öldüğüyle ilgisi yok.
- **14513 maçlık koşuda bu ayrım kayboldu.** `c_20` ve `c_21`'in ikisi de
  yeni `c_17`'ye (core_units jaccard sırasıyla **1.000** ve **0.875**) tek
  bir comp olarak birleşti. Aynı desen `c_11` ("Elder Dragon — Juggernaut")
  ve `c_10` ("Draven — Elderwood") için de geçerli — ikisi de yeni `c_09`'a
  birleşti (jaccard **1.000** ve **0.545**).

**Ders:** küçük örneklemde HDBSCAN, tek bir unit'in (burada Taric) varlığı/
yokluğu üzerinden — centroid'leri neredeyse çakışan, avg_placement'ı gerçekten
farklı ama başka hiçbir yapısal temeli olmayan — gerçek olmayan comp ayrımları
üretebiliyor. 22'den 19'a düşen cluster sayısı bir bilgi kaybı değil, bu tür
sahte ayrımların 5 kat veriyle kendiliğinden düzelmesi. Yapılan: hiçbir kod
değişikliği — bu bir HDBSCAN parametre hatası değil, küçük örneklemin doğal
bir sonucu; büyük veri onu zaten çözdü.

**`min_cluster_size`'ı düşürme — reddedildi (2026-08-31, ölçülerek
sorgulandı).** 19 cluster'ın az olabileceği düşünülüp `min_cluster_size_fraction`
gerçek 15k koşusu (99623 eğitim board'u) üzerinde 4 değerle tarandı — **kod
değiştirilmeden**, `data/processed/` ve `models/`'a hiçbir yazma yapılmadan,
sadece ölçüm amaçlı:

| fraction | min_cluster_size | cluster sayısı | ham noise | nihai off_meta |
|---|---|---|---|---|
| 0.003 (mevcut) | 299 | 19 | %30.06 | %14.98 |
| 0.002 | 199 | 26 | %47.73 | %14.51 |
| 0.0015 | 149 | 31 | %53.16 | %14.03 |
| 0.001 | 100 | 51 | %60.20 | %12.33 |

- **0.003 satırı, mevcut baseline'ı birebir reprodükte etti** (19/19 comp,
  core_units jaccard **1.000** hepsinde) — tarama metodolojisi bu şekilde
  doğrulandı, varsayılmadı.
- **Yeni cluster'lar off_meta'dan gelmiyor.** 0.002 ve 0.0015'te, yeni çıkan
  cluster'ların **hiçbiri** (0/26, 0/31) mevcut 19'un tamamına jaccard<0.3
  değil — hepsi bir baseline comp'a jaccard≥0.444 ile eşleşiyor, yani
  off-meta gürültü havuzundan çıkmış yeni, bağımsız bir comp yok, hepsi
  mevcut 19'un alt bölünmesi.
- **0.001'de çıkan 2 düşük-jaccard cluster ikna edici değil.** `Sett —
  İnfazcı` (n=546) ve `Yunara — İnfazcı` (n=426, ikisi toplam board'ların
  sadece %0.84'ü), en yakın baseline'a bile jaccard=0.167. Ama core_units'leri
  birbirine çok yakın (ikisi de Sett+Ezreal+Yunara barındırıyor) — muhtemelen
  tek, zayıf bir İnfazcı-arketifinin ikiye ayrılmış hali, iki bağımsız yeni
  comp değil. Sadece en agresif ayarda (mcs=100) çıkıyorlar, 0.002/0.0015'te
  hiç görünmüyorlar.
- **Asıl desen fragmantasyon, keşif değil.** mcs küçüldükçe off_meta oranı az
  düşüyor (%14.98→%12.33) ama cluster sayısı 19'dan 51'e fırlıyor — fark
  birkaç büyük "aile" comp'un parçalanmasından geliyor: mcs=100'de
  Cassiopeia — Savunucu 7 parçaya, Kayle — Bombardımancı 9 parçaya, Aphelios
  — Öncü 6 parçaya, Ahri — Manabaz 6 parçaya bölünüyor — bu 4 aile tek
  başına 51 cluster'ın 28'ini oluşturuyor. Bu, yukarıdaki "Az örneklemde
  sahte cluster ayrımı" maddesindeki (Taric/Aphelios örneği) desenin aynısı
  — orada 5 kat veriyle kendiliğinden düzelen bir sahte ayrımdı, burada aynı
  riski parametre değişikliğiyle geri getirmiş olurduk.
- **En küçük iki comp dört ayarda da bölünmedi.** `Nidalee — Avcı` (n≈657-661)
  ve `Warwick — Karadiken` (n≈760-761) — n_total'ları fraction'lar arasında
  neredeyse sabit kaldı, mcs=100'de bile parçalanmadılar. Bu, threshold'u zar
  zor geçen kırılgan cluster'lar değil, gerçekten sıkı/tutarlı yapılar
  olduklarını gösteriyor.

Yapılan: hiçbir şey — `min_cluster_size_fraction` (%0.3) korundu. Kanıt,
düşürmenin off_meta'dan yeni comp çıkarmadığını, sadece mevcut büyük
aileleri ikincil unit farkıyla ince varyantlara böldüğünü gösteriyor.

**Cost-ağırlıklı ve item-ağırlıklı vektörleme (Taric'in centroid'lere aşırı
yakınlaştırıcı etkisini gidermek için) — reddedildi (2026-09-01, ölçüldü,
uygulanmadı).** `scripts/diag_taric.py` ile (kod/config değiştirilmeden,
tek seferlik ölçüm) iki karşı-olgusal ağırlıklandırma test edildi: (a) unit
boyutlarını cost'la (1-5) ağırlıklandırıp yeniden normalize etmek, (b) item
taşıyan unit'lerin board-bazlı ağırlığını 3x yapmak. Taric'i core_unit
taşıyan 6 cluster'ın 15 çifti üzerinde:

- **Hedefte fayda yok.** Ortalama Taric-çifti mesafesi cost-ağırlıklandırmayla
  **0.6382 → 0.6209 (Δ=-0.0173)** — düzelmek yerine biraz daha **yakınlaştı**.
  Item-ağırlıklandırma neredeyse etkisiz (Δ=+0.0037). Çift bazında da tutarsız:
  en yakın çift (c_18-c_16) her iki şemada uzaklaşıyor (+0.077/+0.047) ama
  c_15-c_14 ve c_14-c_16 her iki şemada da belirgin yakınlaşıyor (-0.09 ila
  -0.16) — seçici, öngörülemez etki.
- **Kapsam dışında geniş bozulma var.** Aynı iki ağırlıklandırma tüm 19
  cluster'ın 171 çiftine uygulandığında, Taric'le hiçbir ilgisi olmayan
  çiftler beklenmedik şekilde birbirine yaklaştı: cost-ağırlıklandırmada
  **98/171 çift (%57)**, item-ağırlıklandırmada **21/171 çift (%12)**. En
  büyüğü c_04-c_03 (cost: 0.6506→0.4877, Δ=-0.163) — Taric'le alakasız bir
  çift, hedeflenen düzeltmeden daha büyük bir bozulma.

Yapılan: hiçbir şey — board vektörü ağırlıksız (unpresence) kalmaya devam
ediyor, `clustering.py`'ye hiçbir ağırlıklandırma eklenmedi. Gerekçe:
hedefte ölçülebilir fayda yok, kapsam dışında ölçülebilir zarar var; kendi
regresyon kriterine göre ("sadece hedef düzelip başka yer bozulursa iş
yaramaz") ikisi de elenir.

**"Taric ortak frontline yakınlaşma yaratıyor" hipotezi — ölçülüp
çürütüldü (2026-09-01).** Aynı `diag_taric.py` koşusunda, Taric'in 6
cluster'ın centroid cosine-benzerliğine dimension-bazlı katkısı ayrıştırıldı
(bkz. yukarıdaki madde). Sonuç hipotezi doğrulamadı:

- 6 cluster'ın **5'inde** (c_15/c_17/c_18/c_14/c_16 arası 10 çift) Taric'in
  katkısı **%2.7-7.6** — sıralamada 4.-9. sırada. Asıl sürücü paylaşılan
  trait'ler (Manabaz, Öncü — en yakın çift c_18-c_16'da tek başına %35.5) ve
  paylaşılan core unit'ler (Morgana, Gözcü, Diana, Çakıl, Hecarim).
- **c_09 istisna:** diğer 5 cluster'la ortak neredeyse hiçbir şeyi yok, bu
  yüzden Taric o 5 çiftte katkının **%21-34**'ü ile #1 boyut. Ama bu bağ ham
  yoğunluk uzayında güçlü değil — `_single_linkage_tree_` üzerinden (19
  cluster'ın her birinden `probabilities_`'i en yüksek temsilci nokta,
  union-find'le birleşme sırası) c_09, diğer 5'lik Taric grubuna **18
  birleşmenin 14.'sünde**, mesafe **0.6321**'de katılıyor — aradan 4 başka
  cluster (c_10/c_11/c_12/c_13) zaten girmiş durumda. Centroid-cosine'de
  görünen "yakınlık" ham yoğunlukta karşılığını bulmuyor; c_09 yapısal
  olarak ayrı bir comp.

Yapılan: hiçbir şey — hipotez kod/feature değişikliği gerektirmiyordu,
zaten sadece doğrulama amaçlıydı; çürütüldüğü için uygulanacak bir şey yok.

**Not (2026-09-01, yukarıdaki iki maddeden çıkan genel ders):**
centroid-cosine yakınlığı ile ham yoğunluk (single-linkage) yakınlığı
**çelişebiliyor** — c_09 canlı örnek: centroid mesafesinde Taric üzerinden
"yakın" görünüyor, ham ağaçta 18 birleşmenin 14.'sünde, açıkça geç/zayıf
bir bağ. İkisi farklı şeyler ölçüyor (centroid mesafesi sadece 2 ortalama
vektör arası; single-linkage tüm 99623 noktalı mutual-reachability
uzayından). **Bundan sonra varyant/benzerlik kararlarında (iki cluster aynı
comp mi, ayrı mı, ne kadar yakın) condensed tree / single-linkage hiyerarşisi
esas alınacak, çıplak centroid cosine mesafesi değil** — centroid mesafesi
hâlâ raporlama/görselleştirme için kullanılabilir ama tek başına ayrım kararı
için yeterli kanıt sayılmayacak.