# AI Monitor

OpenAI 기반 서버 장애 자동 감지 및 조치 시스템.
Prometheus로 메트릭을 수집하고, OpenAI 모델이 원인을 분석하여 Microsoft Teams로 알림을 발송합니다.
담당자는 Teams에서 버튼 클릭만으로 자동조치를 승인할 수 있습니다.

## 프로그램 소개

먼저 **Prometheus**가 각 서버에 설치된 node-exporter로부터 CPU, 메모리, 디스크, SWAP 사용률 같은 메트릭을 주기적으로 수집합니다. 그 위에서 동작하는 핵심 파이썬 앱 **ai-monitor**가 60초마다 이 메트릭을 폴링하면서, 임계값(CPU 85%, 메모리·디스크·SWAP 90% 등)을 초과하는지 감시합니다.

임계값을 넘으면, SSH로 해당 서버에 접속해 `ps`, `free`, `df` 같은 진단 명령을 먼저 수집하고, 이 데이터를 **OpenAI API**에 넘겨 장애 원인을 분석합니다. 모델은 심각도, 원인, 권장 조치, 그리고 실행 가능한 조치 명령어까지 생성합니다. API 키가 없으면 단순 임계값 기반 분석으로 자동 전환되는 fallback 구조도 갖추고 있습니다.

분석 결과는 **Microsoft Teams**로 알림이 가고, 담당자는 Teams 카드의 버튼을 눌러 자동조치를 승인할 수 있습니다. 이 승인은 별도의 Flask **webhook 서버**(8080 포트)가 처리하는데, 실제 명령 실행은 POST 요청으로만 가능하게 만들어 URL 스캐너의 오작동을 방지했습니다. 승인되면 SSH로 실제 조치가 실행되고 그 결과가 다시 Teams로 통보됩니다.

모든 장애 이력과 승인 대기 중인 조치는 **SQLite DB**에 기록되어 중복 알림을 막습니다. Prometheus가 수집한 메트릭은 필요하면 별도의 **Grafana**를 붙여 시각화할 수 있습니다(기본 docker-compose에는 포함되어 있지 않음). ai-monitor와 Prometheus는 Docker Compose로 구성되어 있어 `docker compose up -d` 한 번으로 뜨는 구조입니다.

## 전체 흐름

```
Prometheus (메트릭 수집)
        ↓
ai-monitor (임계값 감지 + SSH 진단 수집)
        ↓
OpenAI API (원인 분석 + 조치 명령어 생성)
        ↓
Teams 알림 (분석 결과 + 자동조치 버튼)
        ↓
담당자 승인 → SSH 자동 실행 → 결과 통보
```

## 구성 요소

| 서비스 | 설명 |
|--------|------|
| `ai-monitor` | 핵심 모니터링 애플리케이션 (Python) |
| `prometheus` | 메트릭 수집 서버 (포트 9090) |

## 디렉토리 구조

```
.
├── ai-monitor/
│   ├── main.py                # 진입점. 메트릭 폴링 및 전체 흐름 제어
│   ├── prometheus_query.py    # Prometheus API 쿼리 (CPU/메모리/디스크/SWAP)
│   ├── analyzer.py            # OpenAI 장애 분석 (API 키 없으면 단순 분석으로 fallback)
│   ├── notifier.py            # Teams Adaptive Card 알림 발송
│   ├── remediator.py          # SSH 접속 후 진단 명령 실행 및 자동조치
│   ├── webhook_server.py      # 자동조치 확인/실행/취소 웹 서버 (Flask, 포트 8080)
│   ├── db.py                  # SQLite DB (장애 이력, 자동조치 pending 관리)
│   ├── config.yaml.example    # 설정 파일 예시
│   ├── requirements.txt       # Python 의존성
│   └── Dockerfile
├── prometheus/
│   ├── prometheus.yml.example # Prometheus 수집 대상 설정 예시
│   └── prometheus.yml         # 실제 설정 (사설 IP 노출 방지를 위해 .gitignore 처리, 직접 준비)
├── scripts/
│   ├── install_node_exporter.sh # 모니터링 대상 서버에 node_exporter를 systemd로 설치
│   └── backup_monitor_db.sh   # monitor.db 백업 스크립트 (cron 등록용)
├── docker-compose.yml.example   # docker-compose.yml 예시 (docker-compose.yml은 .gitignore 처리, 직접 준비)
├── docker-compose.yml           # 실제 설정 (SSH 키 파일명이 그대로 노출되므로 .gitignore 처리)
├── *.pem                        # 대상 서버 접속용 SSH 개인키(노드마다 다를 수 있음, 직접 준비, .gitignore 처리, 이미지에는 포함되지 않고 볼륨 마운트됨)
├── .env.example                # 환경변수 예시 (OPENAI_API_KEY)
└── .gitignore
```

## 파일별 설명

### `ai-monitor/main.py`
전체 흐름을 제어하는 진입점.
60초(기본값) 주기로 Prometheus에서 메트릭을 수집하고, 임계값 초과 시 진단 → 분석 → 알림 → 자동조치 흐름을 실행합니다.
장애 발생/해소를 DB에 기록하고 중복 알림을 방지합니다.

### `ai-monitor/prometheus_query.py`
Prometheus HTTP API를 호출하여 메트릭을 수집합니다.
- CPU 사용률, 메모리 사용률, 디스크 사용률(전체 파티션 중 최대값), SWAP 사용률
- 노드 다운(up 메트릭) 감지

### `ai-monitor/analyzer.py`
장애 원인을 분석합니다.
- `OPENAI_API_KEY` 환경변수가 있으면 OpenAI API로 심층 분석
- 없으면 임계값 기반 단순 분석으로 자동 fallback
- 과거 장애 이력과 SSH 진단 데이터를 함께 전달하여 원인 프로세스 특정
- 분석 결과: 심각도 / 원인 분석 / 권장 조치 / 자동조치 가능 여부 / 실행 명령어

### `ai-monitor/notifier.py`
Microsoft Teams로 알림을 발송합니다.
- 장애 알림: 심각도, 분석 결과, TOP 프로세스 현황, 자동조치 버튼 포함
- 장애 해소 알림: 소요 시간 포함
- 자동조치 완료 알림: 실행 명령어 및 실제 실행 결과(stdout) 포함

### `ai-monitor/remediator.py`
SSH로 대상 서버에 접속하여 명령어를 실행합니다.
- `collect_diagnostics()`: 장애 분석 전 ps, free, df 등 진단 데이터 수집
- CPU/Memory 알람의 최상위 원인 프로세스가 Java이면 `jcmd`, `jstat`, `jstack`으로 JVM 상태와 CPU 상위 스레드 스택을 추가 수집
- `run()`: AI가 생성한 명령어 또는 기본 조치 스크립트 실행, stdout/exit code 반환

### `ai-monitor/webhook_server.py`
자동조치 확인 웹 페이지를 제공하는 Flask 서버 (포트 8080).
- `GET  /action/<token>/confirm` : 실행될 명령어와 설명을 보여주는 확인 페이지
- `POST /action/<token>/run`     : 담당자 승인 후 실제 명령어 실행 (POST 전용, URL 스캐너 오발동 방지)
- `GET  /action/<token>/skip`    : 자동조치 취소

### `ai-monitor/db.py`
SQLite 기반 데이터 저장.
- `incidents` 테이블: 장애 발생/해소 이력, AI 분석 내용, 조치 결과
- `pending_actions` 테이블: 담당자 승인 대기 중인 자동조치 관리

### `ai-monitor/config.yaml.example`
노드 및 알림 설정 예시.
실제 사용 시 `config.yaml`로 복사 후 수정 (config.yaml은 .gitignore 처리됨).

`defaults` 블록에 모든 노드 공통 값(담당자, Teams 멘션/웹훅, SSH 키 경로 등)을 한 번만 적어두면 각 노드에 자동으로 병합되어, 노드가 많아져도(예: 30대) 노드마다 반복 작성할 필요가 없습니다. 노드별로 값이 다르면 해당 노드 밑에 같은 키를 적어서 덮어쓰면 됩니다.

`ssh_user`는 노드의 `os` 값에 따라 자동으로 결정됩니다(`rocky` → `cloud-user`, `ubuntu` → `ubuntu`). 직접 `ssh_user`를 적으면 그 값이 우선합니다.

`ssh_port`는 기본값 22이며, SSH 포트가 다른 노드는 해당 노드 밑에 `ssh_port`를 적어서 덮어쓰면 됩니다.

Java 프로세스 진단에 쓰는 `jcmd`, `jstat`, `jstack`은 기본적으로 대상 서버의 `PATH`와 일반 JDK 설치 경로(`/usr/lib/jvm`, `/usr/java`, `/opt/java`, `/opt/jdk`)에서 자동 탐색합니다. 서버마다 OpenJDK 경로가 다르면 `defaults`나 각 노드 밑에 `java_home` 또는 `jdk_bin_path`를 지정하면 됩니다.

`ssh_key_path`가 가리키는 실제 키 파일은 `ai-monitor/` 안이 아니라 저장소 최상위에 둡니다. Docker 빌드 컨텍스트(`ai-monitor/`) 밖에 있어야 이미지에 키가 baked-in 되지 않고, `docker-compose.yml`이 컨테이너 내부 경로(예: `/app/ssh_key.pem`)로 볼륨 마운트합니다.

노드마다 접속에 쓰는 키가 다르면(예: 고객사별로 별도 키 발급) `defaults.ssh_key_path`에 기본 키를 지정하고, 다른 키를 쓰는 노드 밑에 `ssh_key_path`를 덮어쓰면 됩니다. 이때 `docker-compose.yml`에도 해당 키 파일을 볼륨으로 추가해야 합니다. `docker-compose.yml`은 키 파일명이 그대로 드러나므로 `.gitignore` 처리되어 있고, 구조만 보여주는 `docker-compose.yml.example`을 커밋해둡니다.

```yaml
callback_base_url: "http://공인IP또는도메인:8080"  # 담당자가 Teams 버튼을 누를 때 접근할 자동조치 서버 주소
                                                  # 비워두면 승인 절차 없이 AI 판단만으로 자동조치가 즉시 실행됨

thresholds:
  cpu_percent: 85
  memory_percent: 90
  disk_percent: 90
  swap_percent: 90

# 모든 노드에 공통으로 적용되는 값
defaults:
  owner: "담당자명"
  teams_mention_id: "Azure AD Object ID"
  teams_webhook: "Power Automate Webhook URL"
  ssh_key_path: "ssh_key.pem"
  # 공통 JDK 경로가 있으면 지정. 노드별로 다르면 각 노드 밑에서 덮어쓰기 가능.
  # java_home: "/usr/lib/jvm/java-17-openjdk-amd64"

nodes:
  서버이름1:
    ip: "서버IP"
    os: "rocky"    # → ssh_user: cloud-user 로 자동 결정

  서버이름2:
    ip: "서버IP"
    os: "ubuntu"   # → ssh_user: ubuntu 로 자동 결정
    # jdk_bin_path: "/usr/lib/jvm/java-17-openjdk-amd64/bin"
```

### `prometheus/prometheus.yml`
Prometheus 수집 대상 설정.
`node-exporter` job에 모니터링할 서버의 `IP:9100`을 추가합니다.
사설 IP가 git 이력에 노출되지 않도록 `.gitignore` 처리되어 있으며, 실제 사용 시 `prometheus.yml.example`을 복사해 `prometheus.yml`로 만든 뒤 수정합니다.

### `scripts/install_node_exporter.sh`
모니터링 대상 서버(rocky/ubuntu)에 node_exporter를 Docker 없이 systemd 서비스로 설치.
- `/etc/os-release`로 OS를 감지해 Ubuntu는 `apt`, Rocky는 `dnf`(EPEL) 패키지로 설치
- 전용 계정 생성과 systemd 유닛 등록은 패키지가 알아서 처리
```bash
sudo scripts/install_node_exporter.sh
```

### `scripts/backup_monitor_db.sh`
`monitor.db`(ai-monitor 컨테이너의 SQLite DB) 백업 스크립트.
- SQLite Online Backup API로 서비스 중단/락 없이 안전하게 백업
- 백업 파일은 docker volume이 아닌 호스트 디스크(`backups/`)에 gzip으로 저장하여 volume 삭제/손상에도 데이터 보존
- 기본 30일(`RETENTION_DAYS`)보다 오래된 백업은 자동 삭제
- 실행 로그는 `backups/backup.log`에 기록
- cron에 등록해 주기적으로 실행하는 것을 권장합니다.
```bash
# 매일 새벽 3시 백업 (crontab -e)
0 3 * * * ${home}/codex-ai-monitor/scripts/backup_monitor_db.sh
```

### `.env.example`
환경변수 예시. `.env`로 복사 후 OpenAI API 키를 입력합니다.
API 키가 없어도 단순 분석 모드로 동작합니다.

```
OPENAI_API_KEY=sk-...
# 선택 사항: 지정하면 심각도별 기본 모델 대신 이 모델을 사용합니다.
# OPENAI_MODEL=gpt-5.2
```

## 시작하기

1. **설정 파일 준비**
```bash
cp ai-monitor/config.yaml.example ai-monitor/config.yaml
cp prometheus/prometheus.yml.example prometheus/prometheus.yml
cp docker-compose.yml.example docker-compose.yml
cp .env.example .env
# config.yaml, prometheus.yml, docker-compose.yml, .env 에 실제 값 입력

# 대상 서버 접속용 SSH 개인키를 저장소 최상위(ai-monitor/ 안이 아님)에 위치시키고,
# docker-compose.yml에 볼륨 마운트를 추가(노드마다 키가 다르면 여러 개 추가)
cp /path/to/your_key.pem your_key.pem
chmod 600 your_key.pem
```

2. **node_exporter 설치** (모니터링 대상 서버마다)

node_exporter는 Docker 없이 배포판 패키지 매니저(Ubuntu: apt, Rocky: dnf+EPEL)로 설치해 systemd 서비스로 띄웁니다. (`--net=host --pid=host`로 띄우는 컨테이너 방식은 호스트 네임스페이스를 그대로 노출해 격리 이점이 없고, 대상 서버마다 Docker 설치·유지 부담만 늘어남)

```bash
# 각 대상 서버(rocky/ubuntu)에서 root로 실행
sudo scripts/install_node_exporter.sh
```
`scripts/install_node_exporter.sh`가 OS를 감지해 알맞은 패키지를 설치하고 서비스를 기동합니다.

3. **서비스 시작**
```bash
docker compose up -d
```

4. **접속**
- Prometheus: `http://서버IP:9090`
- 자동조치 서버: `http://서버IP:8080`

## 모니터링 항목

| 메트릭 | 기본 임계값 | 설명 |
|--------|------------|------|
| CPU 사용률 | 85% | 5분 평균 |
| 메모리 사용률 | 90% | 가용 메모리 기준 |
| 디스크 사용률 | 90% | 전체 파티션 중 최대값 |
| SWAP 사용률 | 90% | SwapTotal이 0인 노드 제외 |
| 노드 다운 | - | node_exporter 연결 불가 시 즉시 알림 |
