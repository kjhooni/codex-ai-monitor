import threading
from flask import Flask, abort
from db import get_pending_action, update_pending_action_status, update_action
from remediator import run as run_remediation
from notifier import send_resolved_action

app = Flask(__name__)
_webhook_url_map = {}  # node → webhook_url
_mention_map = {}      # node → (mention_id, owner)


def start(nodes_cfg, port=8080):
    for node, cfg in nodes_cfg.items():
        _webhook_url_map[node] = cfg.get("teams_webhook", "")
        _mention_map[node] = (cfg.get("teams_mention_id", ""), cfg.get("owner", ""))

    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port), daemon=True)
    t.start()
    print(f"[Webhook] 액션 서버 시작 - port {port}")


@app.route("/action/<token>/confirm")
def action_confirm(token):
    action = get_pending_action(token)
    if not action:
        abort(404)
    if action["status"] != "pending":
        return _html_done("이미 처리된 요청입니다.", action)

    return f"""
    <html><head><meta charset="utf-8">
    <style>
      body{{font-family:sans-serif;max-width:640px;margin:60px auto;padding:20px;background:#f9f9f9}}
      .box{{background:#fff;border-radius:10px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
      h2{{color:#d9534f;margin-top:0}}
      .info{{color:#555;margin-bottom:24px}}
      .cmd-box{{background:#1e1e1e;color:#d4d4d4;border-radius:6px;padding:16px;font-family:monospace;font-size:14px;word-break:break-all;margin-bottom:28px;white-space:pre-wrap}}
      .desc-box{{background:#fff8e1;border-left:4px solid #ffc107;border-radius:4px;padding:12px 16px;margin-bottom:20px;color:#555;font-size:14px;line-height:1.6;white-space:pre-wrap}}
      .label{{font-size:12px;color:#888;margin-bottom:6px}}
      .actions{{display:flex;gap:12px}}
      .btn{{padding:12px 28px;border:none;border-radius:6px;font-size:15px;cursor:pointer;text-decoration:none;font-weight:bold}}
      .btn-run{{background:#28a745;color:#fff}}
      .btn-skip{{background:#6c757d;color:#fff}}
      .btn:hover{{opacity:.85}}
    </style></head>
    <body><div class="box">
      <h2>자동조치 실행 확인</h2>
      <div class="info">
        <b>노드:</b> {action['node']} &nbsp;|&nbsp;
        <b>메트릭:</b> {action['metric']}
      </div>
      {f'<div class="label">명령어 설명</div><div class="desc-box">{action["description"]}</div>' if action.get('description') else ''}
      <div class="label">실행될 명령어</div>
      <div class="cmd-box">{action['command']}</div>
      <div class="actions">
        <form method="post" action="/action/{token}/run" style="margin:0">
          <button type="submit" class="btn btn-run">실행</button>
        </form>
        <a href="/action/{token}/skip" class="btn btn-skip">취소</a>
      </div>
    </div></body></html>
    """


@app.route("/action/<token>/run", methods=["POST"])
def action_run(token):
    action = get_pending_action(token)
    if not action:
        abort(404)
    if action["status"] != "pending":
        return _html_done("이미 처리된 요청입니다.", action)

    update_pending_action_status(token, "running")
    cmd, result_msg = run_remediation(action["node_config"], action["metric"], action["command"])
    update_pending_action_status(token, "done")
    update_action(action["incident_id"], result_msg)

    webhook = _webhook_url_map.get(action["node"])
    if webhook:
        mention_id, owner = _mention_map.get(action["node"], ("", ""))
        send_resolved_action(webhook, owner, action["node"], action["metric"], cmd, result_msg, mention_id)

    return _html_done(f"조치 완료: {result_msg}", action)


@app.route("/action/<token>/skip")
def action_skip(token):
    action = get_pending_action(token)
    if not action:
        abort(404)
    if action["status"] != "pending":
        return _html_done("이미 처리된 요청입니다.", action)

    update_pending_action_status(token, "skipped")
    update_action(action["incident_id"], "담당자가 자동조치를 건너뜀")
    return _html_done("자동조치를 실행하지 않았습니다. 담당자가 직접 확인하세요.", action)


def _html_done(message, action):
    return f"""
    <html><head><meta charset="utf-8">
    <style>
      body{{font-family:sans-serif;max-width:640px;margin:60px auto;padding:20px;background:#f9f9f9}}
      .box{{background:#fff;border-radius:10px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
      h2{{color:#333;margin-top:0}}p{{color:#555}}
      code{{background:#eee;padding:2px 6px;border-radius:4px;font-size:13px;word-break:break-all}}
    </style></head>
    <body><div class="box">
      <h2>{message}</h2>
      <p>노드: <b>{action['node']}</b> &nbsp;|&nbsp; 메트릭: <b>{action['metric']}</b></p>
      <p>명령어: <code>{action['command']}</code></p>
    </div></body></html>
    """
