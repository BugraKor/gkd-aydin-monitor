# GKD Aydın İzleme Otomasyonu

Bu klasördeki `gkd_monitor.py`, Tarım ve Orman Bakanlığı Güvenilir Gıda Kamuoyu Duyurusu sayfasındaki üç listeyi kontrol eder ve `FirmaIl` alanı Aydın olan yeni kayıtları yakalar.

## Çalıştırma

İlk çalıştırma mevcut Aydın kayıtlarını başlangıç durumu kabul eder ve bildirim göndermez:

```powershell
python .\gkd_monitor.py
```

Mevcut kayıtları da görmek için:

```powershell
python .\gkd_monitor.py --notify-existing --dry-run
```

## Bildirim

`config.example.json` dosyasını `config.json` olarak çoğaltıp e-posta veya Telegram ayarlarını doldurun. Sonra şöyle çalıştırın:

```powershell
python .\gkd_monitor.py --config .\config.json
```

Gmail için normal şifre yerine Google hesabınızdan alınmış uygulama şifresi kullanın. Telegram için BotFather ile bot oluşturup `bot_token`, ardından botun mesaj atacağı `chat_id` bilgisini girin.

## Windows Görev Zamanlayıcı

Saatlik kontrol için Görev Zamanlayıcı'da yeni bir görev oluşturup eylem olarak şu komutu kullanabilirsiniz:

```powershell
python C:\Users\Buğra\Desktop\Staj Notları\Gıda Otomasyonu\gkd_monitor.py --config C:\Users\Buğra\Desktop\Staj Notları\Gıda Otomasyonu\config.json
```

## GitHub Actions

`.github/workflows/gkd-aydin-monitor.yml` kontrolü her gün saat 17:07'de
`Europe/Istanbul` saat diliminde çalıştırır. Bilgisayarın açık olması gerekmez.

Gerçek `config.json` depoya eklenmez. E-posta bilgileri aşağıdaki GitHub
Actions repository secret değerlerinden okunur:

- `GKD_SMTP_HOST`
- `GKD_SMTP_PORT`
- `GKD_SMTP_USERNAME`
- `GKD_SMTP_PASSWORD`
- `GKD_EMAIL_FROM`
- `GKD_EMAIL_TO`

Workflow her başarılı kontrolden sonra `gkd_state.json` dosyasını güncelleyerek
aynı kaydın tekrar bildirilmesini engeller. Actions ekranındaki
`workflow_dispatch` seçeneğiyle elle de çalıştırılabilir.
