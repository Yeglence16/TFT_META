# TFT Comp Analyzer

Riot API'den maç verisi toplayıp comp'ları veriden çıkaran (HDBSCAN clustering),
comp'lar arası sıralama karşılaştırması, trait analizi ve item önerileri üreten
bir araç. Sonuçlara bir web arayüzünden veya CLI'dan bakılabiliyor.
Tasarım kararlarının ayrıntısı `SPEC.md` (pipeline) ve `SPEC-WEB.md` (web
arayüzü) içinde.

## Web arayüzü

İşlenmiş veri `web/data/` altında repo'da hazır geliyor. Yerel bir sunucu
gerekiyor — `index.html`'e çift tıklamak çalışmaz, tarayıcı `file://` altında
JSON yüklemeyi engeller:

```
cd web
python -m http.server 8000
```

Sonra `http://localhost:8000` adresini aç.

**Tek dosya sürümü:** sunucu kurmadan paylaşmak için

```
python src/build_standalone.py
```

veri, CSS ve JS'i tek bir `dist/tft-comp-analyzer.html` dosyasına gömer
(~0.3 MB); bu dosya çift tıklayınca açılır. Unit/trait/item ikonları
CommunityDragon'dan yüklendiği için ikonlar internet bağlantısı ister.

## Kurulum (pipeline'ı kendin çalıştırmak için)

```
pip install -r requirements.txt
```

`.env` dosyasına `RIOT_API_KEY` ekle (`.env` zaten `.gitignore`'da).

## Pipeline'ı çalıştırma sırası

```
python src/static_data.py       # CommunityDragon'dan unit/trait/item/augment verisi çeker
python src/scrape_ladder.py     # ladder'dan PUUID toplar
python src/scrape_matches.py    # maç ID + maç detayı toplar, sete/queue'ya göre filtreler
python src/features.py          # board vektörlerini üretir
python src/clustering.py        # comp'ları HDBSCAN ile bulur
python src/subdivide.py         # seçilmiş comp'ları alt-parçalara böler (config'ten kapatılabilir, SPEC.md bölüm 9a)
python src/matchups.py          # head-to-head sıralama matrisi
python src/traits.py            # trait bazlı güçlü/zayıf analiz
python src/items.py             # item lift analizi
python src/model.py             # placement modeli
python src/build_web_data.py    # web/data/*.json'ı üretir
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

Unit isimleri display name olarak girilir (yazım hataları fuzzy eşleşmeyle
tolere edilir), yıldız seviyesi varsayılan 2'dir. İsimler Türkçe istemcideki
gibidir (`config.yaml`'daki `locale: tr_tr`): champion isimleri çoğunlukla
İngilizce kalıyor, trait isimleri Türkçe. Örnek çıktı:

```
Board (7 unit, hepsi yıldız 2 varsayıldı): Kayacıl, Çakıl, Yampiri Yengeç, Gözcü, Közcük, Alacakurt, Korkabuk

Eşleşen comp: Çakıl — Vadi Canavarı (mesafe=0.037, eşik=0.350)
  Ortalama placement: 4.11  Top4 oranı: 57.1%  n=11639
  En yüksek üstünlük (head-to-head):
    Yavan Cassiopeia: 68.3% (n=2500, CI [66.4%, 70.1%])
    Çakma Kayle: 66.2% (n=3535, CI [64.6%, 67.8%])
    Dikenli Ay: 63.3% (n=5117, CI [62.0%, 64.6%])
    Warwick — Karadiken: 61.3% (n=525, CI [57.1%, 65.4%])
    Morgana — Öncü: 58.7% (n=1873, CI [56.5%, 60.9%])
  En düşük üstünlük (head-to-head):
    Antik Muz: 35.2% (n=1324, CI [32.7%, 37.8%])
    Kayle the Supreme: 37.0% (n=2042, CI [35.0%, 39.1%])
    Cassiopeia-Fortress: 42.6% (n=2472, CI [40.6%, 44.5%])
    Lunari Kardeşler: 45.7% (n=1701, CI [43.3%, 48.1%])
    Şifalı Cassiopeia: 45.8% (n=1498, CI [43.3%, 48.3%])
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
- **Yama:** bilinmiyor — Riot'un `game_version` alanı bu sette her maçta aynı
  placeholder'ı (`"TFT Unreal Version ?.?.?.?"`) döndürüyor (ayrıntı `SPEC.md`
  bölüm 7). Filtre `tft_set_number` + `queue_id`'ye dayanıyor.
- **Bölgeler:** euw1, na1
- **Elo aralığı:** platinum+ (aşağıdaki "Kısıtlar"a bakın)
- **Toplama tarihi:** 2026-08-26 – 2026-08-30 (UTC, maçların `game_datetime`'ı)
- **Maç sayısı:** 14513
- **Katılımcı (board) sayısı:** 116104
- **Comp sayısı:** 24 — HDBSCAN'in bulduğu 19 comp'tan üçü (Kayle, Cassiopeia,
  Aphelios — Öncü) `subdivide.py` ile daha ince varyantlara bölündü
  (`SPEC.md` bölüm 9a)

## Kısıtlar

- **Head-to-head ≠ counter.** Riot API tur tur savaş kaydı vermiyor, sadece
  final board ve final sıralama. Raporlanan oranlar "aynı lobide A'nın B'den
  yukarıda bitirme oranı" — hangi comp'un hangisini "yendiği" değil.
- **Platinum+ örneklemi, master+ değil.** Toplama sırasında ranked reset
  henüz tamamlanmamıştı; gerçek yüksek-elo hacmi yoktu (euw1: master 16
  kayıt, grandmaster 1, challenger 0; na1: master 15, grandmaster 0,
  challenger 0). Ladder doldukça bu aralık daralacak.
- **Item lift confounding.** Bir item'ı tamamlamış olmak zaten iyi giden bir
  maçla ilişkili (yeterli altın/roll, erken elenmemiş olmak) — ölçülen lift
  kısmen item'ın kendi katkısı, kısmen "bu item'ı toplayabilmiş olmak".
  Ayrıntı `SPEC.md` bölüm 15.
- **Augment analizi yok.** Katılımcı verisinde `augments` anahtarı hiç
  bulunmuyor (Set 16/17 maçlarında da yok). Bkz. `SPEC.md` bölüm 11.
- **Tek set, tek yama.** Kod parametrik ama veri sadece Set 18'in şu anki
  halini kapsıyor; dengeleme yamalarıyla sayılar eskir, periyodik olarak
  yeniden koşulması gerekir.
- **Erken elenen board'lar istatistiklere dahil.** Çok erken elenen board'lar
  (`last_round` < eşik) sadece comp centroid'lerinin hesaplanmasından
  çıkarılıyor; ortalama placement, top-4 oranı, head-to-head ve trait
  istatistiklerinin hepsi onları da içeriyor — aksi halde her comp olduğundan
  iyi görünürdü. Ayrıntı `SPEC.md` bölüm 8.

Bu proje Riot Games ile ilişkili değildir ve Riot tarafından onaylanmamıştır.
