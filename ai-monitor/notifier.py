import requests
from datetime import datetime

SEVERITY_COLOR = {
    "low":      "Good",
    "medium":   "Warning",
    "high":     "Attention",
    "critical": "Attention",
}

METRIC_LABEL = {
    "cpu":           "CPU 사용률",
    "memory":        "메모리 사용률",
    "disk":          "디스크 사용률",
    "swap":          "SWAP 사용률",
    "up":            "노드 상태",
    "jvm_heap_old":  "JVM Heap Old Generation 사용률",
}


def send_resolved_action(webhook_url, owner, node, metric, command, result_msg, mention_id=""):
    if not webhook_url:
        print(f"[INFO] [{node}] 자동조치 결과: {result_msg}")
        return
    mention_text = f"<at>{owner}</at>" if mention_id else owner
    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": f"[자동조치 완료] {node} — {mention_text} 확인 요망",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Good",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "노드",    "value": node},
                    {"title": "메트릭",  "value": METRIC_LABEL.get(metric, metric)},
                    {"title": "실행 명령", "value": command or "-"},
                ],
            },
            {
                "type": "TextBlock",
                "text": "**결과**",
                "weight": "Bolder",
                "spacing": "Small",
            },
            {
                "type": "TextBlock",
                "text": result_msg,
                "wrap": True,
                "fontType": "Monospace",
                "spacing": "None",
            },
        ],
    }
    if mention_id:
        content["msteams"] = {
            "entities": [{"type": "mention", "mentioned": {"id": mention_id, "name": owner}, "text": f"<at>{owner}</at>"}]
        }
    _post(webhook_url, {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]})


def send_alert(webhook_url, owner, node, metric, value, threshold, analysis_result, diagnostics=None, mention_id="", action_token=None, callback_base_url="", diag_error=None):
    if not webhook_url:
        print(f"[WARN] {node} Teams webhook URL 미설정 - 콘솔 출력만 함")
        _print_alert(owner, node, metric, value, analysis_result)
        return

    severity = analysis_result.get("severity", "high")
    card = _build_alert_card(owner, node, metric, value, threshold, analysis_result, severity, diagnostics, mention_id, action_token, callback_base_url, diag_error)
    _post(webhook_url, card)


def send_resolved(webhook_url, owner, node, metric, duration_min):
    if not webhook_url:
        print(f"[INFO] [{node}] {METRIC_LABEL.get(metric, metric)} 정상화 - 담당자: {owner} ({duration_min}분 소요)")
        return

    card = _build_resolved_card(owner, node, metric, duration_min)
    _post(webhook_url, card)


def _post(webhook_url, card):
    resp = requests.post(webhook_url, json=card, timeout=10)
    resp.raise_for_status()


def _parse_diagnostics(text):
    if not text:
        return {}, []

    mem_info = {}
    processes = []

    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('==='):
            break  # Java 진단 등 부가 섹션 - ps 프로세스 표는 이미 끝났으므로 파싱 중단
        if s.startswith('Mem:'):
            parts = s.split()
            if len(parts) >= 7:
                mem_info = {
                    'total':     int(parts[1]),
                    'used':      int(parts[2]),
                    'free':      int(parts[3]),
                    'available': int(parts[6]),
                }
        elif s.startswith('Swap:'):
            parts = s.split()
            if len(parts) >= 3:
                mem_info['swap_total'] = int(parts[1])
                mem_info['swap_used']  = int(parts[2])
        elif s and not s.startswith(('USER', '---', 'total', 'Mem', 'Swap')):
            parts = s.split(None, 10)
            if len(parts) >= 11:
                try:
                    cpu = float(parts[2])
                    mem = float(parts[3])
                    cmd = parts[10].split('/')[-1][:28]
                    processes.append({
                        'pid':  parts[1],
                        'cpu':  f"{cpu:.1f}%",
                        'mem':  f"{mem:.1f}%",
                        'cmd':  cmd,
                        '_cpu': cpu,
                        '_mem': mem,
                    })
                except (ValueError, IndexError):
                    pass

    return mem_info, processes[:5]


def _section_header(text):
    return {
        "type": "TextBlock",
        "text": f"**{text}**",
        "weight": "Bolder",
        "separator": True,
        "spacing": "Medium",
        "color": "Accent",
    }


def _col_row(cols, widths, bold=False, color="Default"):
    return {
        "type": "ColumnSet",
        "columns": [
            {
                "type": "Column",
                "width": w,
                "items": [{
                    "type": "TextBlock",
                    "text": t,
                    "size": "Small",
                    "weight": "Bolder" if bold else "Default",
                    "color": color,
                    "wrap": True,
                }]
            }
            for t, w in zip(cols, widths)
        ]
    }


def _mem_section(mem_info):
    if not mem_info:
        return []

    items = [_section_header("메모리 현황")]
    for label, key in [("전체", "total"), ("사용 중", "used"), ("여유 (available)", "available")]:
        val = mem_info.get(key)
        if val is not None:
            items.append(_col_row([label, f"{val:,} MB"], ["stretch", "auto"]))
    if "swap_total" in mem_info:
        items.append(_col_row(
            ["Swap", f"{mem_info['swap_used']:,} / {mem_info['swap_total']:,} MB"],
            ["stretch", "auto"],
        ))
    return items


def _process_section(processes):
    if not processes:
        return []

    widths = ["auto", "stretch", "auto", "auto"]
    items = [
        _section_header("TOP 프로세스"),
        _col_row(["PID", "프로세스", "CPU", "MEM"], widths, bold=True),
    ]
    for p in processes:
        color = "Attention" if (p['_cpu'] > 30 or p['_mem'] > 30) else "Default"
        items.append(_col_row([p['pid'], p['cmd'], p['cpu'], p['mem']], widths, color=color))
    return items


def _build_alert_card(owner, node, metric, value, threshold, result, severity, diagnostics=None, mention_id="", action_token=None, callback_base_url="", diag_error=None):
    color        = SEVERITY_COLOR.get(severity, "Attention")
    metric_label = METRIC_LABEL.get(metric, metric)
    now          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    mem_info, processes = _parse_diagnostics(diagnostics)

    mention_text = f"<at>{owner}</at>" if mention_id else owner

    body = [
        {
            "type": "TextBlock",
            "text": f"[{severity.upper()}] 서버 장애 감지 — {mention_text} 확인 요망",
            "weight": "Bolder",
            "size": "Large",
            "color": color,
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "담당자",  "value": owner},
                {"title": "노드",    "value": node},
                {"title": "메트릭",  "value": metric_label},
                {"title": "현재값",  "value": f"{value:.1f}% (임계값: {threshold}%)"},
                {"title": "감지시각", "value": now},
            ],
        },
        _section_header("분석"),
        {"type": "TextBlock", "text": result.get("analysis", ""), "wrap": True},
    ]

    if diag_error:
        body.append({
            "type": "TextBlock",
            "text": f"진단 데이터 수집 실패 (SSH 오류): {diag_error}",
            "wrap": True,
            "color": "Attention",
            "spacing": "Medium",
        })

    body += _mem_section(mem_info)
    body += _process_section(processes)

    body += [
        _section_header("권장 조치"),
        {"type": "TextBlock", "text": result.get("recommended_action", ""), "wrap": True, "color": "Warning"},
    ]

    if action_token and callback_base_url:
        body.append({
            "type": "ActionSet",
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "title": "자동조치 실행",
                    "url": f"{callback_base_url}/action/{action_token}/confirm",
                    "style": "positive",
                },
                {
                    "type": "Action.OpenUrl",
                    "title": "실행안함",
                    "url": f"{callback_base_url}/action/{action_token}/skip",
                    "style": "destructive",
                },
            ],
        })

    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }

    if mention_id:
        content["msteams"] = {
            "entities": [
                {
                    "type": "mention",
                    "mentioned": {"id": mention_id, "name": owner},
                    "text": f"<at>{owner}</at>",
                }
            ]
        }

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": content,
            }
        ],
    }


def _build_resolved_card(owner, node, metric, duration_min):
    metric_label = METRIC_LABEL.get(metric, metric)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "[RESOLVED] 장애 해소",
                            "weight": "Bolder",
                            "size": "Large",
                            "color": "Good",
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "담당자",  "value": owner},
                                {"title": "노드",    "value": node},
                                {"title": "메트릭",  "value": metric_label},
                                {"title": "소요시간", "value": f"{duration_min}분"},
                                {"title": "해소시각", "value": now},
                            ],
                        },
                    ],
                },
            }
        ],
    }


def _print_alert(owner, node, metric, value, result):
    print(
        f"[ALERT] 담당자={owner} 노드={node} 메트릭={metric} "
        f"값={value:.1f}% severity={result.get('severity')} "
        f"분석={result.get('analysis')}"
    )
