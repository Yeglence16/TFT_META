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
python src/cli.py Kayacıl Çakıl "Yampiri Yengeç" Gözcü Közcük Alacakurt Korkabuk
```

Unit isimleri display name olarak girilir (yazım hatası fuzzy eşleşmeyle
tolere edilir), yıldız seviyesi varsayılan 2'dir. **Unit/trait isimleri artık
Türkçe** (2026-08-31'de `static_data.py`'nin `locale` config'i `tr_tr`'ye
çevrildi — champion isimleri Riot'un Türkçe istemcisiyle aynı şekilde çoğunlukla
İngilizce kalıyor, trait isimleri Türkçe; ayrıntı `SPEC-WEB.md`'de yok, bu
oturumun konuşma geçmişinde). Gerçek çıktı örneği (14513 maçlık koşu,
2026-08-31):

```
Board (7 unit, hepsi yıldız 2 varsayıldı): Kayacıl, Çakıl, Yampiri Yengeç, Gözcü, Közcük, Alacakurt, Korkabuk

Eşleşen comp: Çakıl — Vadi Canavarı (mesafe=0.037, eşik=0.350)
  Ortalama placement: 4.11  Top4 oranı: 57.1%  n=11639
  En yüksek üstünlük (head-to-head):
    Warwick — Karadiken: 61.3% (n=525, CI [57.1%, 65.4%])
    Aphelios — Öncü: 59.0% (n=6952, CI [57.9%, 60.2%])
    Morgana — Öncü: 58.7% (n=1873, CI [56.5%, 60.9%])
    Malphite — İnfazcı: 58.6% (n=6578, CI [57.4%, 59.8%])
    Morgana — Manabaz: 58.3% (n=2153, CI [56.2%, 60.4%])
  En düşük üstünlük (head-to-head):
    Draven — Alev: 48.7% (n=3151, CI [47.0%, 50.5%])
    Master Yi — Ahenkli: 50.0% (n=4182, CI [48.5%, 51.6%])
    Çakıl — Vadi Canavarı: 50.0% (n=5324, CI [48.7%, 51.3%])
    Cassiopeia — Savunucu: 50.2% (n=7794, CI [49.1%, 51.3%])
    Veigar — Zıpırgil: 51.5% (n=2756, CI [49.6%, 53.3%])
  Zorlandığı trait'ler (rakipte aktifken placement kötüleşiyor):
    Üstün Yırtıcı: delta +0.21 (n=3405)
    Avatar: delta +0.09 (n=6207)
    Ahenkli: delta +0.07 (n=5773)
    Doğasever Baba: delta +0.06 (n=6646)
    Zıpırgil: delta +0.05 (n=4962)
  Rahat ettiği trait'ler (rakipte aktifken placement iyileşiyor):
    Cadılar Meclisi: delta -0.10 (n=2330)
    Hasım: delta -0.04 (n=4867)
    Güneş: delta -0.03 (n=5494)
    Ödül Avcısı: delta -0.01 (n=3737)
    Sihirkâr: delta -0.00 (n=11416)

Model tahmini (placement): 6.09

Augment tavsiyesi: veri yok — Riot API şu an bu maç setinde augment verisi döndürmüyor.
```

## Mevcut verinin kapsamı

- **Set:** 18
- **Yama:** bilinmiyor — Riot'un `game_version` alanı bu sette hiç
  kullanılamıyor (her maç aynı placeholder'ı, `"TFT Unreal Version
  ?.?.?.?"`, döndürüyor; ayrıntı `SPEC.md` bölüm 7'de). Filtre
  `tft_set_number` + `queue_id`'ye dayanıyor, yama numarasına değil.
- **Bölgeler:** euw1, na1
- **Elo aralığı:** platinum+ (`meta.json`'ın `elo_range` alanı hâlâ "dev
  sample, ranked reset in progress" ibaresini taşıyor — bu artık tam doğru
  değil, 14513 maçlık bu koşu dev sample'ın ötesine geçti, ama ranked reset
  gerçekten hâlâ sürüyor: challenger/grandmaster/master toplama sırasında
  neredeyse boştu, bkz. aşağıda)
- **Toplama tarihi:** 2026-08-26 – 2026-08-30 (UTC), `matches_filtered`
  içindeki min/max `game_datetime`'dan
- **Maç sayısı:** 14513 (ilk dev sample 2937'ydi)
- **Katılımcı (board) sayısı:** 116104 (ilk dev sample 23496'ydı)
- **Comp sayısı (HDBSCAN):** 19 (ilk dev sample'da 22 — 5 kat veriyle bazı
  yakın comp'lar birleşti, ayrıntı `SPEC.md` bölüm 9)

## Kısıtlar

- **Head-to-head ≠ counter.** Riot API tur tur savaş kaydı vermiyor, sadece
  final board ve final sıralama. Raporlanan oranlar "aynı lobide A'nın B'den
  yukarıda bitirme oranı" — hangi comp'un hangisini "yendiği" değil.
- **Yüksek elo örneklemi değil, platinum+ elo aralığı.** Şu anki veri
  platinum+ seviyesinden (bkz. yukarıdaki elo aralığı) — toplama sırasında
  ranked reset henüz tamamlanmamıştı, gerçek yüksek-elo (master+) hacmi yoktu
  (euw1: master 16 kayıt, grandmaster 1, challenger 0; na1: master 15,
  grandmaster 0, challenger 0 — 2026-08-31'de canlı kontrol edildi). Ranked
  doldukça bu aralık daralacak.
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
