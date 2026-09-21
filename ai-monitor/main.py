import os
import time
import yaml
import traceback
from dotenv import load_dotenv

import uuid
from db import init_db, open_incident, get_open_incident, resolve_incident, get_history, update_action, store_pending_action
from prometheus_query import PrometheusClient
from analyzer import analyze
from notifier import send_alert, send_resolved
from remediator import run as run_remediation, collect_diagnostics, REMEDIATION_SCRIPTS
import webhook_server

load_dotenv()


OS_SSH_USER = {
    "rocky": "cloud-user",
    "ubuntu": "ubuntu",
}


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # defaults 블록의 값(teams_mention_id, teams_webhook, ssh_key_path 등)을
    # 노드마다 반복 작성하지 않도록 각 노드 설정에 병합한다. 노드에 이미 있는 값은 유지(override).
    defaults = config.get("defaults", {})
    for node_cfg in config.get("nodes", {}).values():
        for key, value in defaults.items():
            node_cfg.setdefault(key, value)
        if not node_cfg.get("ssh_user"):
            node_cfg["ssh_user"] = OS_SSH_USER.get(node_cfg.get("os"))

    return config


METRIC_THRESHOLDS = {
    "cpu":    "cpu_percent",
    "memory": "memory_percent",
    "disk":   "disk_percent",
    "swap":   "swap_percent",
}


def check_node(node_name, node_cfg, metrics, thresholds, callback_base_url=""):
    owner      = node_cfg["owner"]
    webhook    = node_cfg.get("teams_webhook", "")
    mention_id = node_cfg.get("teams_mention_id", "")

    # ── 노드 다운 ──────────────────────────────────────────────
    up = metrics.get("up", 1)
    open_down = get_open_incident(node_name, "up")

    if up == 0:
        if not open_down:
            result = {
                "is_real_incident": True,
                "severity": "critical",
                "analysis": f"{node_name} 노드가 응답하지 않습니다. node_exporter 연결 불가.",
                "recommended_action": "서버 상태 및 네트워크 연결을 즉시 확인하세요.",
                "notify": True,
                "auto_remediate": False,
                "remediate_command": None,
            }
            incident_id = open_incident(node_name, owner, "up", 0, 1,
                                        result["analysis"], result["recommended_action"])
            send_alert(webhook, owner, node_name, "up", 0, 1, result, mention_id=mention_id)
            print(f"[ALERT] {node_name} DOWN - incident #{incident_id}")
    else:
        if open_down:
            resolve_incident(open_down["id"], open_down["detected_at"])
            send_resolved(webhook, owner, node_name, "up", open_down["duration_min"] or 0)
            print(f"[RESOLVED] {node_name} UP again")

    if up == 0:
        return  # 나머지 메트릭 체크 불필요

    # ── CPU / Memory / Disk ────────────────────────────────────
    for metric, threshold_key in METRIC_THRESHOLDS.items():
        value = metrics.get(metric)
        if value is None:
            continue

        threshold = thresholds[threshold_key]
        open_inc  = get_open_incident(node_name, metric)

        if value >= threshold:
            if not open_inc:
                history = get_history(node_name, metric)
                diagnostics = None
                diag_error = None
                if metric in ("cpu", "memory", "disk", "swap"):
                    diagnostics, diag_error = collect_diagnostics(node_cfg, metric)
                    if diagnostics:
                        print(f"[DIAG] {node_name} {metric} 진단 수집 완료")
                    elif diag_error:
                        print(f"[DIAG-FAIL] {node_name} {metric} 진단 수집 실패: {diag_error}")
                try:
                    result = analyze(node_name, metric, value, threshold, history, diagnostics)
                except Exception as e:
                    print(f"[WARN] OpenAI 분석 실패 ({node_name}/{metric}): {e}")
                    result = {
                        "is_real_incident": True,
                        "severity": "high",
                        "analysis": f"{metric} 이 {value:.1f}% 로 임계값 초과",
                        "recommended_action": "담당자 직접 확인 필요",
                        "notify": True,
                        "auto_remediate": False,
                        "remediate_command": None,
                    }

                if metric == "swap" and not result.get("remediate_command"):
                    result["remediate_command"] = REMEDIATION_SCRIPTS["swap"]

                if result.get("notify", True):
                    incident_id = open_incident(
                        node_name, owner, metric, value, threshold,
                        result.get("analysis"), result.get("recommended_action")
                    )
                    mention_id = node_cfg.get("teams_mention_id", "")

                    action_token = None
                    remediate_cmd = result.get("remediate_command")
                    if remediate_cmd and callback_base_url:
                        action_token = str(uuid.uuid4())
                        store_pending_action(action_token, incident_id, node_name, metric, remediate_cmd, node_cfg,
                                             description=result.get("recommended_action"))

                    send_alert(webhook, owner, node_name, metric, value, threshold, result, diagnostics, mention_id,
                               action_token=action_token, callback_base_url=callback_base_url, diag_error=diag_error)
                    print(f"[ALERT] {node_name} {metric}={value:.1f}% - incident #{incident_id}")

                    if result.get("auto_remediate") and not action_token:
                        cmd, action_result = run_remediation(node_cfg, metric, remediate_cmd)
                        update_action(incident_id, action_result)
                        print(f"[REMEDIATE] {node_name} {metric}: {action_result}")
                else:
                    print(f"[SKIP] {node_name} {metric}={value:.1f}% - OpenAI 판단: 알림 불필요 ({result.get('analysis','')})")
        else:
            if open_inc:
                resolve_incident(open_inc["id"], open_inc["detected_at"])
                send_resolved(webhook, owner, node_name, metric, open_inc["duration_min"] or 0)
                print(f"[RESOLVED] {node_name} {metric} 정상화 ({value:.1f}%)")


def main():
    config   = load_config()
    init_db()

    prom_url  = os.getenv("PROMETHEUS_URL", config.get("prometheus_url", "http://prometheus:9090"))
    interval  = config.get("poll_interval_seconds", 60)
    thresholds = config["thresholds"]
    nodes_cfg  = config["nodes"]
    callback_base_url = config.get("callback_base_url", "").rstrip("/")

    webhook_server.start(nodes_cfg)

    prom = PrometheusClient(prom_url)
    print(f"AI Monitor 시작 - Prometheus: {prom_url}, 주기: {interval}s")

    while True:
        try:
            all_metrics = prom.collect_all(nodes_cfg)
            for node_name, node_cfg in nodes_cfg.items():
                check_node(node_name, node_cfg, all_metrics[node_name], thresholds, callback_base_url)
        except Exception as e:
            print(f"[ERROR] 폴링 실패: {e}")
            traceback.print_exc()

        time.sleep(interval)


if __name__ == "__main__":
    main()
