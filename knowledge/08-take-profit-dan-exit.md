# Take profit dan exit

---

## 1. Teorema yang perlu dihafal

Untuk `X_t = μt + σW_t`, `X_t − μt` adalah martingale, jadi untuk aturan exit `τ` **apa
pun**:

```
E[P&L] = μ · E[τ] − biaya
```

**Ekspektasi dari aturan exit mana pun sama dengan drift dikali lama waktu di trade,
dikurangi biaya. Exit tidak menciptakan edge.** Ia hanya menentukan berapa lama kita
terpapar drift yang sudah ada, dan membentuk ulang distribusi di sekitarnya.

Setiap pertanyaan di bawah ini mereduksi ke sana.

Kaminski & Lo Proposisi 1 menyatakannya untuk kasus stop **(a)**: di bawah return IID
dengan μ > r_f, stopping premium adalah tepat **−p_o(μ − r_f) < 0**. Untuk proses yang
benar-benar tanpa drift (μ = r_f) **stopping premium tepat nol** — exit-nya netral
ekspektasi dan hanya membentuk ulang distribusi. Itu argumen optional-stopping/martingale
dalam bentuk yang bisa dikutip.

Dan Han, Zhou & Zhu membuktikan padanan formalnya: exit stop-loss adalah **transformasi
payoff opsi barrier**.

## 2. Baseline: ekspektasi nol di setiap R

Entry di 0, stop di −S, target di +T, random walk tanpa drift, barrier menyerap. Menurut
optional stopping theorem:

**P(kena TP duluan) = S/(S+T) = 1/(1+R)** di mana R = T/S.

Win rate impas untuk payoff R **juga** `1/(1+R)`. **Keduanya identik di setiap R.**

| R | W* impas | Win rate RW tanpa drift | Selisih | SD per trade (satuan R) |
|---|---|---|---|---|
| 1,0 | 50,0% | 50,0% | 0 | 1,00 |
| 1,5 | 40,0% | 40,0% | 0 | 1,22 |
| 2,0 | 33,3% | 33,3% | 0 | 1,41 |
| 3,0 | 25,0% | 25,0% | 0 | 1,73 |
| 5,0 | 16,7% | 16,7% | 0 | 2,24 |

Ekspektasi = `[1/(1+R)]·R − [R/(1+R)] = 0` untuk semua R. **Tepat nol, di mana-mana.**
Varians per trade di kasus tanpa drift tepat `R`, jadi **SD = √R**.

**Jadi: melebarkan target membeli lebih banyak varians untuk imbal hasil harapan tepat
nol. "Pakai 1:3" bukan nasihat; itu perubahan satuan.**

## 3. Klaim "1:3 di 35% mengalahkan 1:1 di 55%" — dinyatakan dengan benar

Secara aritmetika benar:
- 1:3 @ 35%: E = 0,35(3) − 0,65(1) = **+0,40R**
- 1:1 @ 55%: E = 0,55(1) − 0,45(1) = **+0,10R**

Tapi framing itu menyembunyikan **empat** hal:

### (i) Edge yang dibutuhkan jauh lebih besar

Sistem 1:3 harus mengalahkan baseline 25%-nya sebesar **+10pp (perbaikan relatif 40%)**.
Sistem 1:1 harus mengalahkan 50% sebesar **+5pp (relatif 10%)**. Apakah edge relatif 40%
bisa dicapai adalah pertanyaan empiris, bukan aritmetika.

### (ii) Per satuan waktu, selisihnya hampir hilang

Waktu harapan sampai resolusi di bawah random walk tanpa drift adalah `E[τ] = S·T/σ²`.
Trade 1:3 menempati **3× waktu pasar**:

- 1:3: 0,40R / 3 = **0,133 R per satuan waktu**
- 1:1: 0,10R / 1 = **0,100 R per satuan waktu**

Keunggulan 33%, bukan 4×. **Untuk EA yang bisa masuk lagi, R-per-trade adalah fungsi
objektif yang salah.**

### (iii) Drawdown-nya kira-kira dua kali lipat

Ekspektasi rentetan kalah terpanjang ≈ `ln(N)/ln(1/q)`:

| R | Win rate | Rentetan terpanjang, N=1000 | DD pada risiko 1% |
|---|---|---|---|
| 1 | 50% | ~10 | ~9,6% |
| 2 | 33,3% | ~17 | ~15,7% |
| 3 | 25% | ~24 | **~21,4%** |
| 5 | 16,7% | ~38 | ~31,7% |

Max drawdown terealisasi lebih buruk lagi, karena pemulihan parsial memperpanjang
peak-to-trough.

### (iv) Dan yang mematikan — kita tidak bisa mengukurnya

Pada R=3 dengan win rate sejati 25%, N=1000 memberi SE = 1,37pp, jadi CI 95% adalah
**22,3%–27,7%**, dengan impas di **tepat tengah**.

**1.000 trade tidak bisa membedakan sistem 1:3 yang menguntungkan dari yang merugi.** Pada
2–3 trade/hari di emas M15 itu ~18 bulan data live yang tidak memberi tahu apa pun secara
konklusif.

Trade yang dibutuhkan untuk t = 2, di mana δ adalah edge win-rate di atas baseline:
`N = 4R / (δ²(R+1)²)`

| δ | R=1 | R=2 | R=3 | R=5 |
|---|---|---|---|---|
| 2pp | 2500 | 2222 | 1875 | 1389 |
| 3pp | 1111 | 988 | 833 | 617 |
| 5pp | 400 | 356 | 300 | 222 |

**Ambang "<1000 basket = terlalu awal untuk dipercaya" di dashboard kita kira-kira benar
untuk edge 3pp dan jauh terlalu longgar untuk edge 2pp.**

### Reframing yang jujur

Sharpe per trade adalah `δ(R+1)/√R`, yang *naik* dengan R — jadi kalau δ konstan terhadap
jarak target, target lebar akan selalu menang. **Premis itu hampir selalu salah**: drift
meluruh dengan waktu dan jarak, jadi δ mengecil saat R tumbuh.

**Ini mengubah pertanyaan R:R jadi sesuatu yang bisa diukur: seberapa jauh, dan untuk
berapa lama, drift forward dari sinyal kita benar-benar bertahan?** Ukur distribusi
MFE/MAE dan drift forward yang dikondisikan pada waktu-dalam-trade. **Jangan memilih
rasio.**

## 4. Geometri mengikuti proses

Carr & López de Prado (2014) **(a)**, Monte Carlo 100.000 jalur atas mesh 21×21:

| Rezim | Geometri optimal |
|---|---|
| **Mean-reverting** (OU half-life pendek) | **Stop lebar, target sempit.** *"tahan inventori cukup lama sampai muncul profit kecil, bahkan dengan risiko kerugian 5 atau 7 kali lipat."* Sharpe sampai **3,2** |
| **Momentum** | Kebalikannya: stop rapat, target lebar |
| **Random walk** | Semua geometri identik; setiap tuning adalah overfitting |

Dan peringatan eksplisit mereka: saat half-life tumbuh menuju random walk, *"tidak ada
area yang bisa dikenali di mana kinerja bisa dimaksimalkan… Mengkalibrasi aturan trading
pada random walk lewat simulasi historis akan menghasilkan backtest overfitting."*

**Ini yang menempatkan geometri V5 dalam konteks.** → [14](14-audit-ea-v1-v5.md)

## 5. Apa yang benar-benar dicapai trader intraday

**FXCM / DailyFX, "Traits of Successful Traders"** — **43 juta trade nyata, Q2 2014 –
Q1 2015, 15 pasangan.**

| Pasangan | % trade ditutup untung | Rata-rata menang | Rata-rata kalah |
|---|---|---|---|
| EUR/USD | 61% | 48 pip | 83 pip |
| GBP/USD | 59% | 43 pip | 83 pip |

Semua 15 pasangan menang >50% waktu dan trader tetap rugi. Studi lanjutan (19 juta trade,
93.000+ akun) memberi EUR/USD win rate 59%, **18 pip menang / 32 pip kalah**.

**Nilai (a) pada ukuran sampel. TAPI kesimpulan R:R yang terkenal bukan seperti yang
terlihat.** Studi melaporkan "53% akun yang beroperasi pada R:R ≥1:1 menghasilkan laba
bersih vs 17% di bawah 1:1." R:R-nya tampaknya diukur **ex post dari Average Gain vs
Average Loss terealisasi**, bukan dari stop dan limit yang ditempatkan trader.

Kalau begitu, **statistik itu nyaris identitas akuntansi**: sebuah akun menguntungkan
jika dan hanya jika `R_realisasi > (1−W)/W`. Jadi "P(untung | R_realisasi)" adalah
pernyataan tentang distribusi bersama dua *hasil dari trade yang sama*. **Ia tidak membawa
informasi apa pun tentang apa yang terjadi kalau seorang trader mengubah targetnya.**

Penjelasan yang lebih mungkin untuk gap-nya: kelompok sub-1:1 didominasi akun tanpa stop
atau dengan stop raksasa relatif terhadap ekuitas, yang kena **margin call** — barrier
menyerap yang diabaikan model P&L. Dibaca begitu, ini hasil tentang **risiko kehancuran
dari stop yang kebesaran**, yaitu leverage, bukan take-profit.

**Base rate dari regulator (a):** ESMA — **74–89% akun CFD ritel rugi**, kerugian rata-rata
€1.600–€29.000. Barber, Lee, Liu & Odean, catatan lengkap day trader Taiwan 1992–2006 —
**"Kurang dari 1% populasi day trader mampu meraih abnormal return positif secara andal
setelah biaya."** Dan: **"praktis seluruh kerugian trading individu dapat ditelusuri ke
order agresif mereka."**

**Kalimat terakhir itu adalah baris paling penting secara operasional di seluruh folder
ini untuk EA yang memakai market order.**

## 6. Perbandingan head-to-head metode TP: tidak ada

**Tidak ada perbandingan publik yang ketat antara fixed-R vs struktur vs ATR vs trailing
yang bisa ditemukan**, dan ada alasan struktural mengapa:

- **Permukaan TP/SL bersifat gabungan, bukan terpisah.** Hasil TP marginal tidak berarti
  tanpa SL dan entry difiksasi bersamanya.
- **Bergantung entry.** TP optimal sepenuhnya ditentukan distribusi kondisional yang
  dipilih entry. Tidak ada jawaban tingkat-instrumen, hanya tingkat-sistem.
- **Tandanya membalik menurut rezim.** Take-profit adalah cermin dari stop-loss. Di bawah
  korelasi serial positif, TP **merugikan** (ia memotong ekor kanan yang sedang disuapi
  momentum); di bawah mean reversion, TP **membantu**.
- **Multiple testing.** Menyapu grid TP × SL pada satu simbol adalah bahaya overfitting
  berat.
- **Tidak ada cerita risk premium**, jadi aturan TP tidak dipublikasikan.

Yang paling mendekati: **Vezeris, Kyrgos & Schinas (2018)**, *JRFM* 11(3):56 **(b)** —
Forex, **Logam**, Energi, Kripto. Verbatim: *"Strategi Take Profit berdasarkan sinyal take
profit yang lebih cepat pada MACD **tidak** lebih baik daripada strategi MACD sederhana"*;
dari varian SL berbasis ATR, *"jendela ATR yang bergeser dan variabel memberi hasil
terbaik untuk **periode 12 dan multiplier 6**."* **Teks lengkap tidak bisa diambil** (MDPI
403). Parameter dioptimasi per-aset ⇒ risiko overfitting tinggi yang tidak bisa saya nilai.

**Jangkar (a) terdekat** adalah literatur ORB: Zarattini & Aziz memakai stop struktural,
target **10R** yang jarang tercapai, dan **exit akhir hari yang melakukan sebagian besar
pekerjaan exit sebenarnya** — rata-rata **0,13R/trade** (QQQ) dan **0,18R/trade**
(stocks-in-play).

**Wawasan desain yang bisa dipindahkan adalah bentuknya, bukan angkanya:** stop struktural
rapat + target cukup jauh sehingga jarang mengikat + **barrier waktu** yang melakukan
pekerjaan sesungguhnya. Itu juga persis konstruksi triple-barrier López de Prado
(*Advances in Financial Machine Learning*, Bab 3): dua barrier horizontal berskala
volatilitas plus satu barrier waktu vertikal.

## 7. Scaling out: pengurangan varians, bukan peningkatan ekspektasi

**Bisa dibuktikan.** Dengan ukuran posisi q(t), `E[P&L] = μ·E[∫q(t)dt] − biaya`.
Scaling out **mengurangi integralnya dan membayar satu spread ekstra.** Di bawah random
walk tanpa drift ia mengubah ekspektasi tepat nol dan mengurangi varians. Di bawah drift
positif ia **mengurangi return harapan** dan mengurangi varians.

**Satu-satunya kasus di mana ia MEMPERBAIKI ekspektasi adalah ketika drift meluruh dengan
waktu dalam trade** — kalau μ turun saat trade menua, mengurangi ukuran di akhir adalah
optimal. Itu kondisional yang bisa diuji, dan satu-satunya yang bisa membenarkan teknik
ini. **Ukur; jangan asumsikan.**

### Bukti empiris

**Uji publik terbersih (a)** — strategi mean reversion, Russell 1000, RSI(2) turun di
bawah 5, data bebas survivorship, maks 5 posisi bersamaan:

| Varian | Eksposur % | **CAR** | RAR | Max Sys DD | # Trade | Rata-rata %P/L | Bar ditahan | % Menang |
|---|---|---|---|---|---|---|---|---|
| **Baseline (tanpa scale-out)** | 42,11 | **15,81** | 37,54 | −17,53 | 1608 | 0,48 | 4,34 | 62,75 |
| Scale out 75% | 35,90 | 11,31 | 31,52 | −16,46 | 765 | 0,74 | 14,15 | 59,61 |
| Scale out 50% | 51,26 | 11,01 | 21,49 | −20,30 | 765 | 0,74 | 14,15 | 53,07 |
| Scale out 25% | 66,51 | 10,50 | 15,79 | **−29,27** | 765 | 0,75 | 14,15 | 47,84 |

Penulisnya: *"Kita melihat penurunan besar pada CAR dengan tidak ada perubahan atau
kenaikan besar pada MDD."* Menambahkan stop max-loss 4%/12% di atasnya: *"Angkanya makin
memburuk."*

**Mekanismenya lebih penting daripada judulnya.** Ekspektasi per trade justru **membaik**
(0,48 → 0,74). CAR tetap turun 28–34% karena rata-rata bar ditahan naik tiga kali lipat
(4,34 → 14,15) dan jumlah trade separuhnya. **Ini biaya turnover/modal terkunci, bukan
biaya edge per trade** — artinya kerusakannya sebanding dengan seberapa mengikat batas
posisi bersamaan kita.

**Uji independen kedua (a):** sistem trend-following breakout 200 hari ASX, 1.432 trade,
sapuan lebar profit-target 0,25–1,50 dan reduksi 0–100%:
- Baseline (tanpa parsial): **21,6% CAGR, 41,7% MaxDD, MAR 0,52**
- ***"Setiap instance pengambilan profit parsial menurunkan tingkat return keseluruhan.
  Setiap satu pun."***
- Drawdown turun di semua varian, tapi MAR sama atau lebih buruk di setiap kombinasi
  kecuali satu outlier statistik.

**Uji ketiga (a)** — varian ukuran dinamis pada trend following:

| Varian | Sharpe | Skew (trade) |
|---|---|---|
| Tidak keduanya | 0,19 | 1,95 |
| Kontrol vol dinamis saja | **0,25** | 1,87 |
| Stop loss dinamis saja | **0,05** | 3,07 |
| Keduanya | 0,07 | 4,00 |

*"Menambahkan kontrol vol dinamis mengurangi skew positif, tapi menambah Sharpe...
Menambahkan stop loss dinamis meningkatkan skew positif, tapi **drastis mengurangi SR**."*

**Ini hasil no-free-lunch dalam bentuk terukur paling bersih: setiap aturan exit yang
mengubah ukuran sebagai fungsi P&L terbuka membeli bentuk distribusi dan membayarnya
dengan ekspektasi.**

**Tidak ada uji kuantitatif publik atas scaling out pada emas intraday.**

## 8. Break-even: jangan

**Tidak ada uji publik yang ketat.** Saya memeriksa blog Carver, Alvarez, Robot Wealth,
EarnForex, Quantpedia, Build Alpha, artikel MQL5, dan forum Trading Blox. **Tidak satu
pun.** Yang beredar adalah asersi tingkat (b).

**Tapi matematikanya menyelesaikannya tanpa itu.**

Dari +k R, dengan stop dipindah ke BE (0) dan target di +T: jarak turun = k, jarak naik =
T−k, jadi **P(capai target) = k/T**. Tanpa pemindahan BE (stop masih di −1):
**P = (1+k)/(1+T)**.

**Contoh: BE di +0,5R dengan target 3R:**

| | P(capai target) | P(scratch/rugi) |
|---|---|---|
| Stop tetap di −1R | **37,5%** | 62,5% (rugi −1R) |
| Stop dipindah ke BE | **16,7%** | **83,3%** (scratch 0R) |

**Memindahkan ke BE di +0,5R lebih dari MEMBAGI DUA peluang bersyarat mencapai target.**

Di bawah random walk tanpa drift kedua cabang punya ekspektasi identik (+0,5R) — pemindahan
BE adalah pengurangan varians murni. Tapi:

- **Dengan biaya**, BE tegas lebih buruk — kita membayar spread di setiap scratch.
- **Dengan drift positif** — yang justru premis kita mengambil trade momentum — BE tegas
  lebih buruk: `E[P&L] = μ·E[τ]`, dan stop BE tegas mengurangi `E[τ]`.
- **BE hanya membantu kalau prosesnya punya dependensi jalur asli** — kalau trade yang
  retrace ke entry *terukur* lebih mungkin gagal daripada trade baru.

**Dan penempatannya buruk secara struktural.** Harga entry kita, secara konstruksi, adalah
level yang baru saja ditembus pasar — sering level yang baru saja pecah. **Stop BE
karenanya diparkir di tengah pita noise pasar sendiri, tepat di harga tempat pullback
kembali.**

Robert Carver menyatakannya langsung **(b)**: *"Kasus khusus dari fixed stop adalah
breakeven stop... Tebak apa, **tidak ada yang istimewa tentang level entry kita sejauh
menyangkut pasar**. Ini tidak lebih baik dari jenis fixed stop mana pun."*

Dan Robot Wealth memberi formulasinya **(b)**: *"saat kita memakai stop loss, kita
membiarkan sinyal tanpa daya prediksi (P&L kita) mengesampingkan sinyal yang punya daya
prediksi (edge kita)."*

**Rekomendasi: jangan pindahkan ke break-even pada trigger waktu atau R tetap.** Kalau
menggeser stop sama sekali, geser ke **level struktural** — higher low baru di atas entry.
Exit itu informatif (struktur tren benar-benar patah) alih-alih noise.

Satu-satunya argumen sah *untuk* BE bersifat perilaku, dan tidak berlaku bagi kita: Odean
(1998), 10.000 akun, menemukan **PGR = 0,148 vs PLR = 0,098 (t = −35)**. **EA kebal
terhadap disposition effect secara konstruksi, jadi ia tidak butuh tongkat perilaku yang
memakan ekspektasi.**

## 9. Trailing stop

**Satu-satunya hasil (a) yang menunjukkan trailing stop menambah nilai** adalah Lei & Li,
dengan poin metodologis kritis bahwa backtest jalur-terealisasi bias:

> *"strategi ini tidak mengurangi maupun menambah kerugian investor relatif terhadap
> strategi buy-and-hold begitu kami memperluas return sekuritas dari realisasi masa lalu
> ke jalur masa depan yang mungkin. Namun satu mekanisme stop loss yang unik membantu
> investor mengurangi risiko investasi."*

"Mekanisme unik" itu adalah **trailing** stop, dan manfaatnya adalah **pengurangan risiko,
bukan return**. **Metodologi mereka — mem-bootstrap jalur masa depan alternatif alih-alih
menguji pada satu sejarah terealisasi — adalah hal terpenting untuk ditiru. Menguji level
stop pada satu jalur historis adalah overfitting terhadap urutan spesifik jalur itu.**

Dan Clare et al. (2013) memberi sapuan kerapatannya: **Sharpe monoton turun 0,54 (12%) →
−0,11 (3%)**, dan tidak ada level yang mengalahkan aturan tren dasarnya. Kesimpulannya:
**"perubahan tren itu sendiri adalah aturan stop-loss terbaik."** → [07](07-stop-loss.md)

**Parabolic SAR:** Wilder sendiri memperkirakan sistemnya "bekerja terbaik dengan sekuritas
yang sedang tren, yang terjadi kira-kira 30% waktu," dan karenanya "rentan whipsaw lebih
dari 50% waktu." Itu penulisnya sendiri mengakui ketergantungan rezim. Parameter asli
(1978): AF mulai **0,02**, naik **+0,02** di setiap extreme point baru, dibatasi **0,20**.

## 10. Time barrier — exit dengan rasio bukti-terhadap-kompleksitas terbaik

- **Konsensus praktisi dan backtest luas (b):** *"Menambahkan stop jarang menambah nilai
  pada strategi, kecuali stop berbasis waktu."* Time stop membatasi waktu di pasar dan
  mengurangi drawdown dengan permukaan curve-fitting yang sangat kecil.
- **Jangkar kalibrasi (a)** — data CySEC (11 firma, 2 Jan – 15 Mei 2017): waktu penahanan
  komoditas median **2–4 jam**, tapi rata-rata **19,36 jam**. Rata-rata jauh melebihi
  median di setiap kelas karena **ekor panjang posisi yang ditahan terlalu lama. Ekor itu
  adalah populasi yang menolak merealisasi kerugian.**
- Dan di literatur ORB: target 10R jarang tercapai; **exit akhir hari yang melakukan
  sebagian besar pekerjaan exit.**

**Aturan konkret:** `basket_max_age = 4 jam` untuk emas intraday, plus paksa flat sebelum
rollover harian dan sebelum rilis berdampak tinggi terjadwal.

## 11. Mitos "exit lebih penting daripada entry"

Klaim ini berasal dari uji Van Tharp/Tom Basso yang tidak pernah dipublikasikan: sistem
entry acak dengan trailing stop 3,0-ATR yang katanya *"menghasilkan uang 100 persen waktu
pada portofolio 10 pasar selama 10 tahun pengujian."* Aturan lengkapnya tidak pernah
diungkap.

**Satu-satunya replikasi serius menghasilkan 100 run merugi dari 100 (a-metodologi).**
Verbatim:

> *"Saya telah mereproduksi uji Tharp, termasuk $100 per kontrak untuk komisi. Sayangnya,
> bagian bawah gambar menampilkan kabar buruknya: **semua 100 uji dari 100, merugi.**
> Compound Annual Growth Rate mereka negatif. Persis kebalikan dari bukunya: 100 persen
> waktu, ia merugi."*

Diagnosisnya keesokan harinya: *"Trading menguntungkan dari 1973 sampai 1988 (dalam
beberapa kasus, sampai 1995), diikuti kinerja buruk dan kerugian sesudahnya... pasar
sebelum 1988 lebih ramah pada 'trendfollower trailing stop' yang simplistis daripada pasar
setelah 1995."*

**Artinya: hasil Tharp bukan bukti bahwa exit lebih penting daripada entry. Itu bukti
bahwa trailing stop 3-ATR ITU SENDIRI adalah aturan trend-following yang menguntungkan
selama 1973–1988. Exit-nya ADALAH edge entry-nya, di era saat edge itu ada. Buang eranya
dan hasilnya terbalik.**

---

## Apa yang harus dikodekan

1. **Jangan pilih R:R. Ukur di mana drift meluruh.** Catat MFE/MAE dan drift forward yang
   dikondisikan pada waktu-dalam-trade.
2. **Time barrier** sebagai exit primer — bukan target. 4 jam untuk M15.
3. **Jangan pindahkan stop ke break-even** pada trigger R tetap. Kalau menggeser, geser
   ke level struktural.
4. **Trailing hanya di bawah momentum yang terbukti**, dan jangan terlalu rapat — bukti
   monoton bilang makin rapat makin buruk.
5. **Scaling out hanya kalau kita sudah mengukur drift meluruh** dengan umur trade.
   Kalau tidak, ia mengurangi return dan tidak selalu mengurangi drawdown.
6. **Taruh target tepat sebelum kelipatan $50/$100** (klaster take-profit ada di sana).
7. **Ganti market order dengan limit order di mana pun bisa.** Temuan Taiwan: praktis
   seluruh kerugian ritel dapat ditelusuri ke order agresif.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Kaminski, K. & Lo, A. (2014). *When do stop-loss rules stop losses?* *JFM* 18, 234–254.
  https://doi.org/10.1016/j.finmar.2013.07.001 ·
  [`../pdf/extract-kaminski_lo.txt`](../pdf/extract-kaminski_lo.txt)
- Carr, P. & López de Prado, M. (2014). *Determining Optimal Trading Rules Without
  Backtesting.* https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2658641
- Lei, A. & Li, H. (2009). *The Value of Stop Loss Strategies.*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1214737 ·
  [`../pdf/extract-lei_li.txt`](../pdf/extract-lei_li.txt)
- Clare, A., Seaton, J., Smith, P. & Thomas, S. (2013). *Breaking into the blackbox.*
  *Journal of Asset Management* 14(3). https://doi.org/10.1057/jam.2013.11
- Han, Y., Zhou, G. & Zhu, Y. *Taming Momentum Crashes.*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199 ·
  [`../pdf/extract-han_zhou_zhu.txt`](../pdf/extract-han_zhou_zhu.txt)
- Odean, T. (1998). *Are Investors Reluctant to Realize Their Losses?* *Journal of
  Finance* 53(5), 1775–1798. https://doi.org/10.1111/0022-1082.00072 ·
  https://faculty.haas.berkeley.edu/odean/papers%20current%20versions/areinvestorsreluctant.pdf ·
  [`../pdf/extract-odean.txt`](../pdf/extract-odean.txt)
- Barber, B., Lee, Y.-T., Liu, Y.-J. & Odean, T. (2014). *The cross-section of speculator
  skill: Evidence from day trading.* *JFM* 18, 1–24.
  https://doi.org/10.1016/j.finmar.2013.05.006 ·
  https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf
- Barber, B., Lee, Y.-T., Liu, Y.-J. & Odean, T. (2009). *Just How Much Do Individual
  Investors Lose by Trading?* *RFS* 22(2), 609–632. https://doi.org/10.1093/rfs/hhn046
- Alvarez, C. (2016). *Adding Stops and Scaling Out to a Mean Reversion Strategy.*
  https://alvarezquanttrading.com/blog/adding-stops-and-scaling-out-to-a-mean-reversion-strategy/
- Enlightened Stock Trading. *Is taking partial profits in trend following a good idea?*
  (1.432 trade, sapuan parameter)
  https://enlightenedstocktrading.substack.com/p/is-taking-partial-profits-in-trend
- Carver, R. (2020). *Dynamic trend following.*
  https://qoppac.blogspot.com/2020/12/dynamic-trend-following.html
- ESMA (2018). *Product Intervention Analysis*, ESMA50-162-215 (tabel waktu penahanan
  CySEC). https://www.esma.europa.eu/sites/default/files/library/esma50-162-215_product_intervention_analysis_cfds.pdf
- "sluggo", replikasi sistem entry acak Tharp, Trading Blox forum, 14–15 Feb 2007 —
  https://www.tradingblox.com/tbforum/viewtopic.php?t=3637
- López de Prado, M. *Advances in Financial Machine Learning*, Bab 3 (triple-barrier).

**Tingkat (b) — konsensus praktisi tanpa data**

- Carver, R. (2020). *What is the right way to set stop losses?*
  https://qoppac.blogspot.com/2020/02/what-is-right-way-to-set-stop-losses.html —
  **nol data**, tapi memuat kutipan break-even.
- Robot Wealth. *Stop Losses: Rethinking Conventional Wisdom.*
  https://robotwealth.com/stop-losses-rethinking-conventional-wisdom/ — **nol backtest**,
  framing yang sangat baik.
- EarnForex. *Partial Profit Taking in Forex — Does It Work?*
  https://www.earnforex.com/blog/partial-profit-taking-in-forex-does-it-work/ —
  aritmetika mainan, tanpa backtest.
- Quantified Strategies. *Trading exit strategies.*
  http://www.quantifiedstrategies.com/trading-exit-strategies/
- LuxAlgo. *Scaling out.* https://www.luxalgo.com/library/concept/scaling-out/ — secara
  eksplisit menolak mengklaim scaling out membantu: *"Tidak inheren."*
- Vezeris, D., Kyrgos, T. & Schinas, C. (2018). *JRFM* 11(3):56.
  https://doi.org/10.3390/jrfm11030056 — **teks lengkap tidak bisa diambil.**
- StockCharts ChartSchool, *Parabolic SAR* —
  https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/parabolic-sar

**Yang harus dibuang (c)**

- Klaim "exit lebih penting daripada entry" berdasarkan sistem entry acak Van Tharp —
  data primernya tidak pernah dipublikasikan, dan satu-satunya replikasi serius gagal
  100 dari 100.
- Setiap artikel yang mengklaim "target ATR mengalahkan fixed R sebesar X%" — tidak ada
  perbandingan head-to-head yang ketat yang pernah dipublikasikan.
