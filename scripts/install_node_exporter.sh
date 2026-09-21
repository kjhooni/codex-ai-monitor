#!/bin/bash
# node_exporter 설치 스크립트 (Docker 미사용)
# - 모니터링 대상 서버(rocky/ubuntu)에서 root 권한으로 실행
# - Ubuntu/Debian: 공식 저장소 패키지(apt) 사용 - 계정 생성/systemd 유닛 등록을 패키지가 처리
# - Rocky/RHEL 계열: EPEL에 node_exporter 패키지가 없어 GitHub 릴리스 바이너리를 받아 직접 systemd 유닛 등록
#
# 사용법:
#   sudo ./install_node_exporter.sh [버전]   # 버전은 rocky/rhel 계열 바이너리 설치에만 사용, 생략 시 기본값

set -euo pipefail

NODE_EXPORTER_VERSION="${1:-1.8.2}"

. /etc/os-release

case "$ID" in
    ubuntu|debian)
        apt-get update
        apt-get install -y prometheus-node-exporter
        systemctl enable --now prometheus-node-exporter
        echo "완료. 상태 확인: systemctl status prometheus-node-exporter"
        ;;
    rocky|almalinux|rhel|centos)
        ARCH="amd64"
        USER_NAME="node_exporter"
        BIN_PATH="/usr/local/bin/node_exporter"
        SERVICE_PATH="/etc/systemd/system/node_exporter.service"
        TMP_DIR=$(mktemp -d)
        trap 'rm -rf "$TMP_DIR"' EXIT

        echo "[1/4] node_exporter ${NODE_EXPORTER_VERSION} 다운로드 (EPEL에 패키지가 없어 GitHub 릴리스 사용)"
        curl -fsSL -o "${TMP_DIR}/node_exporter.tar.gz" \
            "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-${ARCH}.tar.gz"
        tar -xzf "${TMP_DIR}/node_exporter.tar.gz" -C "$TMP_DIR"

        echo "[2/4] 바이너리 설치 (${BIN_PATH})"
        install -m 0755 "${TMP_DIR}/node_exporter-${NODE_EXPORTER_VERSION}.linux-${ARCH}/node_exporter" "$BIN_PATH"

        echo "[3/4] 전용 시스템 계정 생성"
        if ! id -u "$USER_NAME" >/dev/null 2>&1; then
            useradd --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
        fi

        echo "[4/4] systemd 유닛 등록"
        cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
User=${USER_NAME}
Group=${USER_NAME}
Type=simple
ExecStart=${BIN_PATH}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable --now node_exporter
        echo "완료. 상태 확인: systemctl status node_exporter"
        ;;
    *)
        echo "지원하지 않는 OS입니다: $ID (rocky/ubuntu만 지원)" >&2
        exit 1
        ;;
esac
