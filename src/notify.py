#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机通知模块（国内可用，无需VPN）
支持: 企业微信机器人 / 钉钉机器人 / PushPlus / Bark(iOS) / Server酱
配置: 同目录 notify_config.json
{
  "enabled": true,
  "provider": "wecom",        # wecom / dingtalk / pushplus / bark / serverchan
  "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<your-key>",
  "token": ""                 # pushplus token / bark key / serverchan sendkey
}
"""
import json, os, time, hmac, hashlib, base64, urllib.request, urllib.parse, re

import config as _cfg

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_F = os.path.join(_cfg.STATE_DIR, "notify_config.json")
AUDIT_F = os.path.join(_cfg.LOG_DIR, "notify_log_all.jsonl")
_LAST = {}
_DEDUP_SEC = 20

def load_config():
    try:
        with open(CONFIG_F, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {"enabled": False, "provider": "wecom", "webhook": "", "token": ""}
    # secrets may come from the environment / .env instead of the json file
    if not d.get("webhook"): d["webhook"] = _cfg.secret("NOTIFY_WEBHOOK", "")
    if not d.get("token"): d["token"] = _cfg.secret("NOTIFY_TOKEN", "")
    if not d.get("topic"): d["topic"] = _cfg.secret("NOTIFY_TOPIC", "")
    return d

def _audit(title,ok,err=""):
    try:
        with open(AUDIT_F,"a",encoding="utf-8") as f:
            f.write(json.dumps({"ts":time.time(),"title":title,"ok":ok,"error":err[:200]},ensure_ascii=False)+"\n")
    except Exception:
        pass

def _post_json(url, payload, timeout=10):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            if attempt >= 2: raise
            time.sleep(0.4)

def _post_form(url, data, timeout=10):
    body = urllib.parse.urlencode(data).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            if attempt >= 2: raise
            time.sleep(0.4)

STATUS_PAT = re.compile(
    r"已挂单[，,]?\s*等成交|立即吃单成交|"
    r"未成交|已成交|部分成交|待成交|成交(?!价|金额|份数|量)|"
    r"已挂单|挂单|已撤单|撤单|已平仓|平仓(?!价)|已止损|止损(?!价)|"
    r"已结算|结算中|已赎回|赎回失败|已过期|已取消|已关闭|持仓中|跳过|失败|成功|赢|输|"
    r"\b(?:FILLED|CANCELLED|CANCELED|SUBMITTED|PENDING|PARTIALLY_FILLED|EXPIRED|FAILED|WIN|LOSS|SKIP|ABORT|OPEN|CLOSED)\b",
    re.IGNORECASE)

def _colorize(text):
    return STATUS_PAT.sub(lambda m: f'<font color="warning">{m.group(0)}</font>', text)

def send(title: str, body: str):
    key = title + "\n" + body
    now = time.time()
    if _LAST.get(key) and now - _LAST[key] < _DEDUP_SEC:
        print("[notify] duplicate suppressed")
        return True
    _LAST[key] = now
    cfg = load_config()
    if not cfg.get("enabled"):
        print("[notify] disabled")
        return False
    provider = (cfg.get("provider") or "").lower()
    text = (title + "\n" + body)[:3500]
    try:
        if provider == "wecom":
            url = cfg.get("webhook", "")
            if not url: return False
            md = _colorize(text)
            resp = _post_json(url, {"msgtype": "markdown", "markdown": {"content": md}})
            ok=False
            try:
                j = json.loads(resp)
                ok=(j.get("errcode") in (0, "0", None))
                if not ok: print("[notify] wecom markdown rejected:", j.get("errcode"), j.get("errmsg"))
            except Exception:
                print("[notify] wecom response:", resp[:200])
            if not ok:
                resp = _post_json(url, {"msgtype": "text", "text": {"content": text}})
                try:
                    j = json.loads(resp)
                    if j.get("errcode") not in (0, "0", None):
                        print("[notify] wecom rejected:", j.get("errcode"), j.get("errmsg"))
                        return False
                except Exception:
                    print("[notify] wecom response:", resp[:200])
        elif provider == "dingtalk":
            url = cfg.get("webhook", "")
            if not url: return False
            secret = cfg.get("secret", "")
            if secret:
                ts = str(int(time.time() * 1000))
                string_to_sign = ts + "\n" + secret
                h = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(h))
                url = url + ("&" if "?" in url else "?") + "timestamp=" + ts + "&sign=" + sign
            resp = _post_json(url, {"msgtype": "text", "text": {"content": text}})
            try:
                j = json.loads(resp)
                if j.get("errcode") not in (0, "0", None):
                    print("[notify] dingtalk rejected:", resp[:200]); return False
            except Exception:
                pass
        elif provider == "ntfy":
            topic = cfg.get("topic", "")
            if not topic: return False
            req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"),
                                         headers={"Title": "BTC signal", "Priority": "high", "Tags": "bell"})
            urllib.request.urlopen(req, timeout=10).read()
        elif provider == "pushplus":
            token = cfg.get("token", "")
            if not token: return False
            resp = _post_form("https://www.pushplus.plus/send",
                              {"token": token, "title": title, "content": body, "template": "txt"})
            try:
                j = json.loads(resp)
                if str(j.get("code")) != "200":
                    print("[notify] pushplus rejected:", j.get("code"), j.get("msg"))
                    return False
            except Exception:
                print("[notify] pushplus response:", resp[:200])
        elif provider == "bark":
            key = cfg.get("token", "")
            if not key: return False
            url = f"https://api.day.app/{key}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
            urllib.request.urlopen(url, timeout=10).read()
        elif provider == "serverchan":
            key = cfg.get("token", "")
            if not key: return False
            _post_form(f"https://sctapi.ftqq.com/{key}.send", {"title": title, "desp": body})
        else:
            print("[notify] unknown provider", provider)
            return False
        print("[notify] sent via", provider)
        _audit(title,True)
        return True
    except Exception as e:
        print("[notify] failed:", str(e)[:120])
        _audit(title,False,str(e))
        return False

if __name__ == "__main__":
    cfg = load_config()
    if not cfg.get("token") and not cfg.get("webhook"):
        print("请先填写 notify_config.json")
    else:
        send("BTC dry-run 测试", "这是一条测试消息")
