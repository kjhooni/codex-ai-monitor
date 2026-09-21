#!/usr/bin/env python3
"""
시연용 부하 트리거 스크립트. ai-monitor 컨테이너 안에서 실행한다.
config.yaml의 kimjh-test01 노드에 SSH로 접속해 CPU/메모리/디스크 부하를 발생시키거나 정리한다.

사용법:
  docker compose exec ai-monitor python demo_trigger.py status            # 현재 상태 확인
  docker compose exec ai-monitor python demo_trigger.py cpu [초]          # CPU 100% 부하 (기본 90초)
  docker compose exec ai-monitor python demo_trigger.py memory [초] [MB]  # 메모리 부하 (기본 90초, 500MB)
  docker compose exec ai-monitor python demo_trigger.py disk [MB]         # 디스크 더미 파일 생성 (기본 300MB)
  docker compose exec ai-monitor python demo_trigger.py cleanup           # 부하 프로세스 종료 + 더미 파일 삭제
"""
import shlex
import sys
import paramiko

from main import load_config

NODE = "kimjh-test01"


def _load_node():
    return load_config()["nodes"][NODE]


def _ssh_run(node_cfg, command, wait=False):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(node_cfg["ip"], username=node_cfg["ssh_user"], key_filename=node_cfg["ssh_key_path"], timeout=10)
    _, stdout, stderr = ssh.exec_command(f"sudo -n bash -c {shlex.quote(command)}")
    out = err = ""
    if wait:
        out = stdout.read().decode()
        err = stderr.read().decode()
        stdout.channel.recv_exit_status()
    ssh.close()
    return out, err


def cpu(duration=90):
    node = _load_node()
    # 코어 수만큼 워커를 띄워야 전체 CPU 사용률(코어 평균)이 100%까지 올라감
    _ssh_run(node, f"nohup stress-ng --cpu $(nproc) --timeout {duration} > /dev/null 2>&1 &")
    print(f"[CPU] {NODE} ({node['ip']}) 에 {duration}초간 CPU 100% 부하 시작 (전체 코어)")


def memory(duration=90, mb=500):
    node = _load_node()
    _ssh_run(node, f"nohup stress-ng --vm 1 --vm-bytes {mb}M --vm-keep --timeout {duration} > /dev/null 2>&1 &")
    print(f"[MEMORY] {NODE} ({node['ip']}) 에 {duration}초간 {mb}MB 메모리 점유 시작")


def disk(mb=300):
    node = _load_node()
    _ssh_run(node, f"nohup dd if=/dev/zero of=/data/log/demo_dummy bs=1M count={mb} 2>/dev/null &")
    print(f"[DISK] {NODE} ({node['ip']}) 에 {mb}MB 더미 파일 생성 시작 (/data/log/demo_dummy)")


def cleanup():
    node = _load_node()
    out, _ = _ssh_run(
        node,
        "pkill -9 stress-ng 2>/dev/null; "
        "rm -f /data/log/demo_dummy /data/log/dummy /data/log/test_5gb.dat; "
        "df -h /data",
        wait=True,
    )
    print(f"[CLEANUP] {NODE} 부하 프로세스 종료 + 더미 파일 삭제 완료")
    print(out)


def status():
    node = _load_node()
    out, _ = _ssh_run(
        node,
        "echo '--- CPU/MEM (top) ---'; top -bn1 | head -6; "
        "echo '--- DISK ---'; df -h /data",
        wait=True,
    )
    print(out)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    args = sys.argv[2:]

    if action == "cpu":
        cpu(int(args[0]) if args else 90)
    elif action == "memory":
        memory(int(args[0]) if len(args) > 0 else 90, int(args[1]) if len(args) > 1 else 500)
    elif action == "disk":
        disk(int(args[0]) if args else 300)
    elif action == "cleanup":
        cleanup()
    elif action == "status":
        status()
    else:
        print(__doc__)
