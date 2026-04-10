## 의존성 설치

### Python 패키지
```bash
pip install -r requirements.txt
```

### Shapely (OS별 별도 설치 필요)

#### Rocky Linux 8
```bash
dnf install -y epel-release
dnf install -y geos geos-devel
pip install --no-cache-dir shapely==1.8.5
```

#### Rocky Linux 9+
```bash
dnf config-manager --set-enabled crb
dnf install -y epel-release
dnf install -y geos geos-devel
pip install --no-cache-dir shapely==1.8.5
```

#### Ubuntu/Debian
```bash
apt-get install -y libgeos-dev
pip install --no-cache-dir shapely==1.8.5
```
