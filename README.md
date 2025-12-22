Analisis Perbandingan Algoritma Routing SDN: Bellman-Ford vs Johnson

Proyek ini adalah implementasi Software Defined Networking (SDN) menggunakan Controller Ryu dan Emulator Mininet. Proyek ini membandingkan kinerja algoritma routing Bellman-Ford (On-Demand) dan Johnson (All-Pairs Shortest Path) pada berbagai topologi jaringan (Ring, Mesh) dengan skala node yang bervariasi (10, 50, hingga 100 node).

📋 Prasyarat Sistem

Sistem Operasi: Ubuntu 20.04 / 22.04 / 24.04 (Disarankan Linux Native atau VM dengan RAM min. 4GB).

Python: Wajib menggunakan Python 3.9 (Ryu tidak kompatibel dengan Python 3.10+ tanpa penyesuaian khusus).

Mininet: Versi terbaru.

🛠️ Instalasi & Setup

Ikuti langkah ini untuk mengatur lingkungan kerja dari nol (khususnya untuk Ubuntu versi baru yang tidak menyertakan Python 3.9 secara default).

1. Install System Dependencies

Jalankan perintah berikut di terminal:

sudo apt update
sudo apt install mininet openvswitch-switch python3.9 python3.9-venv python3.9-dev build-essential -y


(Catatan: Jika Python 3.9 tidak ditemukan di Ubuntu 24.04, tambahkan PPA deadsnakes: sudo add-apt-repository ppa:deadsnakes/ppa lalu update kembali)

2. Buat Virtual Environment (Venv)

Kita wajib menggunakan venv agar dependensi tidak merusak sistem utama.

# Masuk ke folder proyek
cd ~/skripsi-sdn

# Buat venv menggunakan Python 3.9
python3.9 -m venv venv

# Aktifkan venv
source venv/bin/activate


3. Install Python Libraries

Pastikan file requirements.txt sudah ada di folder proyek, lalu jalankan:

pip install -r requirements.txt


📂 Struktur File

Controller (Otak Jaringan):

controller_bellman_ring.py: Algoritma Bellman-Ford untuk Ring (Optimized: Strict Flood & Cache).

controller_johnson_ring.py: Algoritma Johnson untuk Ring (Pre-calculated Paths).

controller_bellman_mesh.py: Algoritma Bellman-Ford untuk Mesh (Optimized for High Density).

controller_johnson_mesh.py: Algoritma Johnson untuk Mesh (Thread Pool & Batch Processing).

Topologi & Otomasi:

skrip_topologi.py: Class pembangun topologi kustom (Ring, Mesh, Tree, FatTree) untuk Mininet.

otomasi_skripsi.py: Skrip utama untuk menjalankan eksperimen otomatis (Convergence, Throughput, Recovery) dengan penanganan timeout cerdas.

🚀 Cara Menjalankan Eksperimen

Anda membutuhkan 2 Terminal yang keduanya sudah masuk ke folder proyek dan mengaktifkan venv (source venv/bin/activate).

Skenario 1: Topologi RING (10, 50, 100 Node)

Terminal 1 (Controller):
Jalankan controller yang sesuai. Flag --observe-links WAJIB ada.

# Contoh untuk Johnson:
ryu-manager controller_johnson_ring.py --ofp-tcp-listen-port 6653 --observe-links


Terminal 2 (Mininet Otomatis):

Buka otomasi_skripsi.py dengan text editor (misal nano).

Edit bagian if __name__ == '__main__': paling bawah.

Aktifkan baris yang sesuai, misal: run_automated_test('ring', 100, algo_name="JOHNSON").

Jalankan:

sudo python3 otomasi_skripsi.py


Skenario 2: Topologi MESH (Berat: 10, 50, 100 Node)

Khusus Mesh 50/100 node, pastikan bersabar karena waktu tunggu (initial wait) diset sangat lama (10-20 menit) untuk mencegah timeout.

Terminal 1 (Controller):
Gunakan controller khusus Mesh yang sudah dioptimasi.

ryu-manager controller_johnson_mesh.py --ofp-tcp-listen-port 6653 --observe-links


Terminal 2 (Mininet Otomatis):

Buka otomasi_skripsi.py.

Aktifkan baris Mesh, misal: run_automated_test('mesh', 50, algo_name="JOHNSON_MESH").

Jalankan:

sudo python3 otomasi_skripsi.py


⚠️ Troubleshooting & Tips Anti-Gagal

Error: "Multiple connections from ..." / Switch Reconnect:

Penyebab: CPU Overload saat memproses ribuan link, switch mengira controller mati.

Solusi: Jangan tutup terminal. Skrip otomasi_skripsi.py sudah memiliki fitur Inactivity Probe Fix (180s) yang akan menstabilkan koneksi setelah turbulensi awal (3-5 menit). Biarkan berjalan hingga waktu tunggu selesai.

Error: "Address already in use" (Error 98):

Penyebab: Port 6653 masih terpakai oleh proses Ryu sebelumnya.

Solusi: Matikan proses Ryu di Terminal 1 (Ctrl+C). Jika membandel, jalankan sudo mn -c di Terminal 2 untuk membersihkan semua proses Mininet/OVS.

Log "Topology Update" stuck di 0 Link:

Penyebab: Ryu tidak mengirim paket discovery LLDP.

Solusi: Pastikan Anda menyertakan flag --observe-links saat menjalankan ryu-manager.

Timeout Convergence > 600s/1200s:

Penyebab: Jaringan terlalu padat (terutama Mesh 50/100 Node pada hardware terbatas).

Solusi: Ini adalah hasil valid (limitasi hardware/overhead controller). Catat sebagai data skripsi.

📊 Variabel Pengujian

Skrip otomasi akan menghasilkan output di terminal:

Convergence Time: Waktu yang dibutuhkan jaringan untuk stabil (Ping pertama sukses dari ujung ke ujung).

Throughput: Kapasitas bandwidth jaringan (diukur dengan iperf selama 5 detik).

Recovery Time: Waktu pemulihan jalur saat link diputus (Failover otomatis).

Dibuat untuk keperluan Skripsi SDN - Afriza

