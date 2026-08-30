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

Scraper'ın her aşaması **kaldığı yerden devam edebilir** olacak. Diske durum
yazsın (basit bir JSON state dosyası veya SQLite yeter):

- işlenmiş PUUID'ler
- indirilmiş maç ID'leri
- bekleyen kuyruk

Süreç öldüğünde, key expire olduğunda veya bilgisayar kapandığında baştan
başlanmayacak. Bu opsiyonel değil — 20-30k maçlık scrape dev key'le ~11 saat
sürüyor.

---

## 4. Static data (`static_data.py`)

Kaynak: CommunityDragon TFT dump'ı
(`https://raw.communitydragon.org/latest/cdragon/tft/en_us.json`).

İndir, `data/static/` altına cache'le, şu eşlemeleri üret:

- `character_id` → unit display name, cost, traits
- trait ID → display name, breakpoint'ler
- item ID → display name
- augment ID → display name **ve tier (silver / gold / prismatic)**

**Augment tier'ı için:** cdragon augment kayıtlarında bir `rarity` alanı var.
Önce onu incele ve gerçekten tier'a karşılık geldiğini birkaç bilinen augment'le
doğrula (örneğin bilinen bir prismatic ve bilinen bir silver seç, değerleri
karşılaştır). Doğrulamadan varsayma. Eşleme tutmuyorsa alternatif bir türetme
yolu bul ve hangi yöntemi kullandığını README'ye yaz.

Config'teki set numarasına göre filtrele — dump tüm setleri içeriyor.

---

## 5. Ladder scrape (`scrape_ladder.py`)

Endpoint'ler (platform host):
```
/tft/league/v1/challenger
/tft/league/v1/grandmaster
/tft/league/v1/master
```

Bunlar `LeagueListDTO` döndürür. Entry'lerde `puuid` alanı varsa doğrudan onu
kullan; yoksa `summonerId` üzerinden `/tft/summoner/v1/summoners/{summonerId}`
ile çöz. (Riot summoner ID'lerden PUUID'ye geçiş yapıyor, alan varlığını runtime'da
kontrol et, varsayma.)

Bölge önerisi: `euw1` + `na1`. Hacim yetmezse `kr` ekle.

Yüksek elo seçilmesinin sebebi: comp'lar daha temiz oynanıyor, cluster'lar daha
net ayrışıyor. Bunu README'de bir kapsam kısıtı olarak yaz — sonuçlar yüksek elo
metasını temsil eder, tüm oyuncu tabanını değil.

Çıktı: `data/processed/puuids.json`

---

## 6. Maç ID toplama (`scrape_matches.py`, 1. faz)

Regional host:
```
/tft/match/v1/matches/by-puuid/{puuid}/ids?count=20
```

Her lobide 8 oyuncu olduğu için aynı maç ID'si defalarca gelecek. **Set halinde
dedupe et.** Dedupe oranını logla — beklenen ciddi bir çakışma.

Hedef: 20-30k benzersiz maç ID'si.

---

## 7. Maç detayı indirme (`scrape_matches.py`, 2. faz)

```
/tft/match/v1/matches/{matchId}
```

**Ham JSON'u olduğu gibi sakla.** Gzipli jsonl shard'ları halinde
(`data/raw/matches_0001.jsonl.gz` gibi, shard başına ~5000 maç). Sadece işlenmiş
feature'ları saklamak yetmez — feature tanımı değişince yeniden scrape etmek
zorunda kalmayalım.

Filtreleme **indirmeden sonra**, ayrı bir adımda yapılacak (ham veri korunsun):

- `info.tft_set_number` == config'teki set
- `info.game_version` config'teki yama prefix'iyle başlıyor
- `info.queue_id` == normal ranked TFT

Queue ID için: ranked TFT'nin `1100`, Hyper Roll'un `1130`, Double Up'ın `1160`
olduğunu biliyoruz ama **bunu veriden doğrula** — birkaç yüz maçın queue_id
dağılımını çıkar, katılımcı sayısı ve `last_round` desenleriyle karşılaştır.
Double Up ve Hyper Roll maçları veri setine karışırsa cluster'lar bozulur.

Kaç maçın hangi filtreyle elendiğini logla.

---

## 8. Feature çıkarımı (`features.py`)

Her maçta 8 katılımcı var, her katılımcı bir örnek.

**Ön eleme:** çok erken elenen oyuncuları at (`last_round` eşiği, kabaca stage 4
öncesi). Bunların board'ları yarım kalmış, cluster'ları kirletir. Eşiği config'e
koy ve kaç örnek elendiğini logla.

Board vektörü:
- **Unit bileşeni:** o setteki her unit için bir boyut, değer = star level (0/1/2/3)
- **Trait bileşeni:** her trait için aktif tier (`style` veya `tier_current`)
- Item'lar v1'de dışarıda kalabilir; bırakılırsa README'de belirt

Vektörü L2 normalize et. Clustering cosine mesafesiyle çalışacak.

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
cluster boyutu, ortalama placement, top-4 oranı, en sık 8 unit (frekansla), en sık
3 trait. Bu tabloyu gözle inceleyeceğiz — cluster'ların anlamlı olup olmadığını
ölçecek ground truth yok, tek doğrulama yolu bu.

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

Her (cluster, augment) çifti için ortalama placement ve `n`.

Ham ortalama kullanma. İki düzeltme:

1. **Lift'e çevir:** cluster'ın kendi ortalama placement'ından farkı ölç, böylece
   comp'un genel gücü augment sinyalini bastırmasın.
2. **Empirical Bayes shrinkage:** hücre ortalamasını augment'in global
   ortalamasına doğru çek —
   `shrunk = (n * cell_mean + k * global_mean) / (n + k)`, `k` config'ten (~50 ile başla).

Neden gerekli: ~40 cluster × ~250 augment = 10k hücre, 240k board ile hücre başına
~70 gözlem düşüyor. Düzeltmesiz sonuçlar gürültü olur.

Çıktı: tier başına (silver / gold / prismatic) en iyi 3 augment, yanında `n` ve
shrink edilmiş lift.

**README'ye yazılacak kısıt:** veri sadece *alınan* augment'leri içeriyor, *teklif
edilen*leri değil. Yani ölçtüğümüz şey "iyi augment" değil, "alındığında iyi
sonuç veren augment". Seçim yanlılığı var, gizleme.

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
- En iyi ve en kötü head-to-head eşleşmeler (oran + `n` + güven aralığı)
- Tier başına 3 augment tavsiyesi (lift + `n`)
- Zorlandığı / rahat ettiği trait'ler
- Modelin tahmin ettiği placement

---

## 15. Bitmiş sayılma kriterleri

- [ ] Her aşama tek başına, kaldığı yerden çalıştırılabiliyor
- [ ] Kodda tek bir hardcoded unit/trait/item/augment ismi yok
- [ ] `403` scraper'ı sessizce bozmuyor
- [ ] Cluster inceleme tablosu basılıyor ve gözle bakınca comp'lar tanınıyor
- [ ] Her oran yanında `n` ve güven aralığı ile geliyor
- [ ] Model train/test bölmesi match bazında
- [ ] README'de: veri toplama tarihi, yama, bölgeler, elo aralığı, maç sayısı,
      ve kısıtlar bölümü (head-to-head ≠ counter, augment seçim yanlılığı,
      yüksek elo örneklemi, yama ömrü)

---

## 16. Yapılmayacaklar (kapsam disiplini)

- Web arayüzü yok. CLI yeter.
- Canlı/otomatik güncelleme yok. Tek yama, tek koşu.
- Pozisyon (unit dizilimi) analizi yok — API zaten vermiyor.
- Ekonomi / roll timing analizi yok — final board'dan çıkarılamaz.
- Birden fazla set desteği yok. Kod parametrik olsun ama tek sete koşulacak.