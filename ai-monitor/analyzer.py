import os
import json

SEVERITY_BY_METRIC = {
    "up":     "critical",
    "disk":   "high",
    "memory": "high",
    "swap":   "high",
    "cpu":    "medium",
}

# 호출 전 심각도를 추정해 critical/high에는 더 강한 모델을,
# medium/low에는 비용 효율적인 모델을 사용한다.
MODEL_BY_SEVERITY = {
    "critical": "gpt-5.2",
    "high":     "gpt-5.2",
    "medium":   "gpt-5-mini",
    "low":      "gpt-5-mini",
}

ACTIONS_BY_METRIC = {
    "up":     "서버 상태 및 네트워크 연결을 즉시 확인하세요.",
    "cpu":    "top 명령으로 CPU 점유 프로세스를 확인하세요.",
    "memory": "메모리 점유 프로세스 확인 후 필요 시 재시작하세요.",
    "disk":   "불필요한 로그 및 파일을 정리하세요. (find /var/log -name '*.gz' -mtime +7 -delete)",
    "swap":   "swapoff -a && swapon -a 로 스왑을 초기화합니다. (자동조치 실행)",
}


def _estimate_severity(metric, history):
    severity = SEVERITY_BY_METRIC.get(metric, "high")
    quick_recoveries = sum(
        1 for h in history
        if h.get("duration_min") and h["duration_min"] < 5 and h["status"] == "resolved"
    )
    if quick_recoveries >= 3 and metric != "disk":
        severity = "low"
    return severity


def analyze(node, metric, value, threshold, history, diagnostics=None):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        estimated_severity = _estimate_severity(metric, history)
        model = os.getenv(
            "OPENAI_MODEL",
            MODEL_BY_SEVERITY.get(estimated_severity, "gpt-5.2"),
        )
        return _analyze_with_openai(node, metric, value, threshold, history, api_key, diagnostics, model)
    return _analyze_simple(node, metric, value, threshold, history, diagnostics)


def _analyze_simple(node, metric, value, threshold, history, diagnostics=None):
    severity = _estimate_severity(metric, history)
    analysis = f"{node} 의 {metric} 가 {value:.1f}% 로 임계값({threshold}%)을 초과했습니다. 과거 유사 이력 {len(history)}건 존재."

    return {
        "is_real_incident": True,
        "severity": severity,
        "analysis": analysis,
        "recommended_action": ACTIONS_BY_METRIC.get(metric, "담당자 직접 확인 필요"),
        "notify": True,
        "auto_remediate": False,
        "remediate_command": None,
    }


def _analyze_with_openai(node, metric, value, threshold, history, api_key, diagnostics=None, model="gpt-5.2"):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    history_text = _format_history(history)

    system_prompt = """당신은 서버 인프라 장애 분석 전문가입니다.
Prometheus 메트릭, 과거 장애 이력, 그리고 서버에서 직접 수집한 진단 정보를 바탕으로
현재 상황의 근본 원인을 추론하고 대응 방안을 JSON으로 답해야 합니다.

응답 형식 (반드시 이 JSON만 출력):
{
  "is_real_incident": true/false,
  "severity": "low|medium|high|critical",
  "analysis": "분석 내용 (한국어, 2-3문장, 진단 데이터 기반 원인 추론 포함)",
  "recommended_action": "권장 조치 (번호 매겨 단계별로, 프로세스명/PID/명령어 구체적으로)",
  "notify": true/false,
  "auto_remediate": true/false,
  "remediate_command": "자동 실행할 bash 명령어 또는 null"
}

판단 기준:
- 진단 데이터(ps, free 등)가 있으면 반드시 원인 프로세스나 원인을 특정해서 분석
- 원인 프로세스가 java 인 경우 jcmd/jstat/jstack 진단 결과가 함께 제공됩니다.
  jstat -gcutil 의 GC 비율/빈도(예: FGC 급증, O 영역 포화)로 GC 압박 여부를 판단하세요.
  CPU 원인 스레드는 "CPU 상위 스레드의 jstack 스택트레이스" 섹션(spid를 hex 변환해 jstack nid=와 매칭한 결과)을
  최우선 근거로 사용해 특정하세요. jstack 전체 덤프만 보고 RUNNABLE 스레드를 임의로 추측해 CPU 원인으로 지목하지 마세요 -
  jstack 하나만으로는 어떤 스레드가 실제로 CPU를 많이 쓰는지 알 수 없고, 위 매칭 섹션에 나온 스레드만 CPU 사용량이 확인된 것입니다.
  근거가 불충분하면 JVM 프로세스를 함부로 kill/재시작하는 명령을 생성하지 말고
  auto_remediate=false, remediate_command=null 로 두고 recommended_action에 확인이 필요한 스레드/GC 지표를 명시하세요.
- 과거 같은 시간대에 반복된 패턴이면 is_real_incident=false 고려
- 지속 시간이 짧고 자동 회복된 이력 있으면 severity 낮게
- disk 90% 이상이면 항상 notify=true
- node down은 항상 critical, notify=true

auto_remediate 및 remediate_command 판단 기준:
- 명확한 원인 프로세스가 있고 kill/재시작이 안전하다고 판단되면 auto_remediate=true
- remediate_command에는 실제 실행 가능한 bash 명령어를 지정 (예: "kill -15 1234", "systemctl restart nginx", "sync && echo 3 > /proc/sys/vm/drop_caches")
- remediate_command는 반드시 진단 데이터(diagnostics)에 실제로 나타난 파일/디렉토리/프로세스를 근거로만 생성하세요. 진단 데이터로 확인되지 않은 조건(예: 임의의 -mtime 기간, 임의의 보관 정책)을 추측해서 명령어에 넣지 마세요.
- 디스크 조치의 경우 진단 데이터의 "최근 수정 파일" 목록을 반드시 확인하세요. 용량을 차지하는 파일들이 최근에 생성/수정된 것이라면(고의적 테스트 파일, 급증하는 로그 등) -mtime 기반 삭제는 효과가 없으므로 사용하지 말고, 진단 데이터에 나온 실제 파일/디렉토리 경로를 직접 지정해서 삭제하세요. 어떤 파일을 지워도 안전한지 확신할 수 없으면 auto_remediate=false, remediate_command=null 로 두세요.
- 원인 불명확하거나 운영 프로세스 여부 불확실하면 auto_remediate=false, remediate_command=null
- 여러 명령이 필요하면 &&로 연결"""

    diagnostics_text = f"\n\n서버 진단 데이터:\n{diagnostics}" if diagnostics else ""

    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=(
            f"노드: {node}\n메트릭: {metric}\n현재값: {value:.1f}% (임계값: {threshold}%)\n"
            f"과거 이력 (최근 10건):\n{history_text}{diagnostics_text}\n\n이 상황을 분석해주세요."
        ),
        max_output_tokens=4096,
        store=False,
        text={
            "format": {
                "type": "json_schema",
                "name": "incident_analysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "is_real_incident": {"type": "boolean"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "analysis": {"type": "string"},
                        "recommended_action": {"type": "string"},
                        "notify": {"type": "boolean"},
                        "auto_remediate": {"type": "boolean"},
                        "remediate_command": {"type": ["string", "null"]},
                    },
                    "required": [
                        "is_real_incident", "severity", "analysis", "recommended_action",
                        "notify", "auto_remediate", "remediate_command",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    return json.loads(response.output_text)


def _format_history(history):
    if not history:
        return "  (이력 없음 - 첫 발생)"
    lines = []
    for h in history:
        resolved = h["status"] == "resolved"
        duration = f"{h['duration_min']}분 후 해소" if h.get("duration_min") else ("미해소" if not resolved else "해소")
        lines.append(
            f"  - {h['detected_at'][:16]}  값={h['value']:.1f}%  {duration}"
            + (f"  분석={h['claude_analysis'][:40]}..." if h.get("claude_analysis") else "")
        )
    return "\n".join(lines)
