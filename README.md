# TFT Comp Analyzer

Riot API'den maç verisi toplayıp comp'ları veriden çıkaran (HDBSCAN clustering),
comp'lar arası sıralama karşılaştırması, trait analizi ve placement tahmini
üreten bir CLI aracı. Ayrıntılı tasarım kararları için `SPEC.md` ve
`SPEC-WEB.md`'ye bakın — bu dosya sadece kurulum, çalıştırma ve mevcut verinin
kısıtlarını anlatır.

## Kurulum

```
pip install -r requirements.txt
```

`.env` dosyasına `RIOT_API_KEY` ekleyin (`.env` zaten `.gitignore`'da).

## Pipeline'ı çalıştırma sırası

```
python src/static_data.py       # CommunityDragon'dan unit/trait/item/augment verisi çeker
python src/scrape_ladder.py     # ladder'dan PUUID toplar
python src/scrape_matches.py    # maç ID + maç detayı toplar, sete/queue'ya göre filtreler
python src/features.py          # board vektörlerini üretir
python src/clustering.py        # comp'ları HDBSCAN ile bulur
python src/matchups.py          # head-to-head sıralama matrisi
python src/traits.py            # trait bazlı güçlü/zayıf analiz
python src/items.py             # item lift analizi
python src/model.py             # placement modeli
```

Her aşama kaldığı yerden devam edebilir (`src/state_store.py`). `augments.py`
çalıştırılmıyor — aşağıdaki "Kısıtlar" bölümüne bakın.

Testler:

```
pytest tests/
```

## CLI kullanımı

```
python src/cli.py Krug Pebbles Scuttlecrab Sentinel Cinderling Murkwolf Brambleback
```

Unit isimleri display name olarak girilir (yazım hatası fuzzy eşleşmeyle
tolere edilir), yıldız seviyesi varsayılan 2'dir. Gerçek çıktı örneği:

```
Board (7 unit, hepsi yıldız 2 varsayıldı): Krug, Pebbles, Scuttlecrab, Sentinel, Cinderling, Murkwolf, Brambleback

Eşleşen comp: Pebbles — Riftbeast (mesafe=0.037, eşik=0.350)
  Ortalama placement: 4.13  Top4 oranı: 55.5%  n=2244
  En yüksek üstünlük (head-to-head):
    Aphelios & Sentinel — Vanguard: 67.8% (n=577, CI [63.8%, 71.4%])
    Aphelios — Elderwood: 62.5% (n=696, CI [58.8%, 66.0%])
    Morgana — Vanguard: 61.2% (n=340, CI [55.9%, 66.2%])
    Nidalee — Invoker: 61.1% (n=283, CI [55.3%, 66.6%])
    Ahri — Blossom: 59.0% (n=724, CI [55.4%, 62.5%])
  En düşük üstünlük (head-to-head):
    Elder Dragon — Juggernaut: 41.9% (n=191, CI [35.1%, 49.0%])
    Aphelios — Vanguard: 47.4% (n=800, CI [43.9%, 50.8%])
    Veigar — Sprykin: 48.4% (n=498, CI [44.0%, 52.8%])
    Kha'Zix — Rival: 49.3% (n=345, CI [44.0%, 54.5%])
    Draven — Elderwood: 49.8% (n=502, CI [45.4%, 54.2%])
  Zorlandığı trait'ler (rakipte aktifken placement kötüleşiyor):
    Apex Predator: delta +0.18 (n=658)
    Avatar: delta +0.16 (n=1279)
    Greenfather: delta +0.07 (n=1318)
    Old Growth: delta +0.06 (n=1260)
    Ravager: delta +0.06 (n=1824)
  Rahat ettiği trait'ler (rakipte aktifken placement iyileşiyor):
    Coven: delta -0.12 (n=413)
    Rival: delta -0.06 (n=866)
    Monolith: delta -0.03 (n=1460)
    Elderwood: delta -0.01 (n=1800)
    Fae: delta -0.01 (n=1736)

Model tahmini (placement): 6.13

Augment tavsiyesi: veri yok — Riot API şu an bu maç setinde augment verisi döndürmüyor.
```

## Mevcut verinin kapsamı

- **Set:** 18
- **Yama:** bilinmiyor — Riot'un `game_version` alanı bu sette hiç
  kullanılamıyor (her maç aynı placeholder'ı, `"TFT Unreal Version
  ?.?.?.?"`, döndürüyor; ayrıntı `SPEC.md` bölüm 7'de). Filtre
  `tft_set_number` + `queue_id`'ye dayanıyor, yama numarasına değil.
- **Bölgeler:** euw1, na1
- **Elo aralığı:** platinum+ (dev örneklem, set 5 günlük olduğu için ranked
  reset henüz tamamlanmadı — challenger/grandmaster/master neredeyse boş)
- **Toplama tarihi:** 2026-08-26 – 2026-08-30 (UTC), `matches_filtered`
  içindeki min/max `game_datetime`'dan
- **Maç sayısı:** 2937
- **Katılımcı (board) sayısı:** 23496

## Kısıtlar

- **Head-to-head ≠ counter.** Riot API tur tur savaş kaydı vermiyor, sadece
  final board ve final sıralama. Raporlanan oranlar "aynı lobide A'nın B'den
  yukarıda bitirme oranı" — hangi comp'un hangisini "yendiği" değil.
- **Yüksek elo örneklemi değil, dev-örneklem elo aralığı.** Şu anki veri
  platinum+ seviyesinden (bkz. yukarıdaki elo aralığı) — set henüz 5 günlük
  olduğu için gerçek yüksek-elo (master+) hacmi yok. Ranked doldukça bu
  aralık daralacak.
- **Item lift confounding.** Bir item'ı tamamlamış olmak zaten iyi giden bir
  maçla korelasyon içinde (yeterli altın/roll, erken elenmemiş olmak) —
  ölçülen lift kısmen item'ın kendi katkısı, kısmen "bu item'ı
  toplayabilmiş olmak". Ayrıntı `SPEC.md` bölüm 15.
- **Augment analizi yok.** Katılımcı verisinde `augments` anahtarı hiç
  bulunmuyor (Set 18'e özgü de değil — aynı taramada Set 16/17'de de yok).
  Ölçülecek veri olmadığı için bu analiz askıya alındı, bkz. `SPEC.md`
  bölüm 11.
- **Tek set, tek yama — ve yama ömrü kısıtlı.** Kod parametrik ama bu koşu
  sadece Set 18'in şu anki (ve tek elde edilebilir) verisini kapsıyor. Riot
  dengeleme yaması çıkardıkça bu sayılar eskir — otomatik güncelleme yok
  (bkz. `SPEC.md` bölüm 17), periyodik olarak yeniden koşulması gerekir.
- **early_death board'ları raporlamaya dahil.** Çok erken elenen board'lar
  (last_round < eşik) sadece comp centroid'lerinin hesaplanmasından
  (HDBSCAN eğitiminden) çıkarılıyor; ortalama placement, top-4 oranı,
  head-to-head ve trait istatistiklerinin hepsi bu board'ları da içeriyor —
  aksi halde erken ölümleri dışlamak her comp'u olduğundan iyi gösterirdi.
  Ayrıntı `SPEC.md` bölüm 8.
