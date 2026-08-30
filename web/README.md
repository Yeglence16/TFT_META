# TFT Comp Analyzer — Web Arayüzü

**Bu klasörü `file://` ile (index.html'e çift tıklayarak) AÇMA.** Tarayıcı JSON
dosyalarını CORS nedeniyle yükleyemez, sayfa boş/bozuk görünür. Bunun yerine:

```
cd web
python -m http.server 8000
```

sonra tarayıcıda `http://localhost:8000` adresini aç.

## Ne yapar

Ayrı bir analiz pipeline'ının (`../SPEC.md`) ürettiği comp/istatistik verisini
gösteren, tamamen istemci tarafında çalışan bir araç — backend yok, API
anahtarı yok, dış servise istek atmıyor. Ayrıntılı tasarım kararları
`../SPEC-WEB.md`'de.

## Veri

`data/` klasörü `src/build_web_data.py` tarafından üretilir — elle
düzenlenmez, pipeline'ın çıktısı her değiştiğinde yeniden çalıştırılır.

## Durum (v1 iskelet)

Şu an çalışan: veri yükleme, `_selftest.json` doğrulaması (board vektörünün
pipeline'ınkiyle birebir tuttuğunu açılışta kontrol eder, tutmuyorsa arayüz
durur), boş 3 sütunlu düzen. Board etkileşimi, comp eşleştirme ve sonuç
paneli henüz yok.

## Riot ile ilişki

Bu proje Riot Games ile ilişkili değildir ve Riot tarafından onaylanmamıştır.
