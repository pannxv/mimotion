# -*- coding: utf-8 -*-
"""
反查云端当天步数。

刷完之后调用华密的读接口，把服务器上「今天」的 summary.stp.ttl 读回来，
用来验证步数是不是真的写进去了（而不只是接口返回 success）。

用法：python3 check_steps.py
环境变量：CONFIG（与主流程相同）、AES_KEY（可选，用于复用加密保存的 app_token）
"""
import base64
import json
import math
import os
import sys
import uuid
from datetime import datetime

import pytz
import requests

from util.aes_help import decrypt_data
import util.zepp_helper as zeppHelper

# 不同账号的可用域名不一样，按顺序探测
API_HOSTS = [
    "https://api-mifit-cn.huami.com",
    "https://api-mifit-cn2.huami.com",
    "https://api-mifit-cn3.zepp.com",
    "https://api-mifit.huami.com",
]
UA = "MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)"


def today_str() -> str:
    return datetime.now(tz=pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d")


def desensitize(name: str) -> str:
    if len(name) <= 8:
        ln = max(math.floor(len(name) / 3), 1)
        return f"{name[:ln]}***{name[-ln:]}"
    return f"{name[:3]}****{name[-4:]}"


def normalize_user(user: str) -> str:
    if user.startswith("+86") or "@" in user:
        return user
    return "+86" + user


def load_user_tokens(aes_key: str) -> dict:
    path = "encrypted_tokens.data"
    if len(aes_key) != 16 or not os.path.exists(path):
        return dict()
    try:
        with open(path, "rb") as f:
            data = f.read()
        return json.loads(decrypt_data(data, aes_key.encode("utf-8"), None).decode("utf-8"))
    except Exception:
        return dict()


def get_app_token(user: str, password: str, saved: dict):
    """尽量复用已保存的 token，失败再重新登录。返回 (app_token, user_id, 来源说明)"""
    info = saved.get(user) or {}
    app_token = info.get("app_token")
    user_id = info.get("user_id")
    if app_token and user_id:
        ok, _ = zeppHelper.check_app_token(app_token)
        if ok:
            return app_token, user_id, "复用已保存的 app_token"

    login_token = info.get("login_token")
    if login_token and user_id:
        new_token, _ = zeppHelper.grant_app_token(login_token)
        if new_token:
            return new_token, user_id, "用 login_token 换新 app_token"

    access_token, msg = zeppHelper.login_access_token(user, password)
    if access_token is None:
        return None, None, "登录失败：%s" % msg
    device_id = info.get("device_id") or str(uuid.uuid4())
    _, new_token, new_user_id, msg = zeppHelper.grant_login_tokens(
        access_token, device_id, user.startswith("+86"))
    if new_token is None:
        return None, None, "换取 app_token 失败：%s" % msg
    return new_token, new_user_id, "重新登录获取"


def query_cloud_steps(app_token: str, user_id: str, date_str: str):
    """返回 (步数, 域名, 错误信息)。步数为 None 表示没查到。"""
    last_err = None
    for host in API_HOSTS:
        for query_type in ("detail", "summary"):
            params = {
                "query_type": query_type,
                "device_type": "android_phone",
                "userid": user_id,
                "from_date": date_str,
                "to_date": date_str,
                "t": int(datetime.now().timestamp() * 1000),
                "r": str(uuid.uuid4()),
            }
            try:
                resp = requests.get(f"{host}/v1/data/band_data.json", params=params,
                                    headers={"apptoken": app_token, "User-Agent": UA}, timeout=15)
            except Exception as e:
                last_err = f"{host} 请求异常: {e}"
                continue
            if resp.status_code != 200:
                last_err = f"{host} HTTP {resp.status_code}: {resp.text[:160]}"
                continue
            try:
                payload = resp.json()
            except Exception:
                last_err = f"{host} 返回非 JSON: {resp.text[:160]}"
                continue
            days = payload.get("data") or []
            if not days:
                last_err = f"{host} data 为空: {str(payload)[:160]}"
                continue
            for day in days:
                if not str(day.get("date_time", "")).startswith(date_str):
                    continue
                summary = day.get("summary")
                if not summary:
                    continue
                try:
                    parsed = json.loads(base64.b64decode(summary))
                except Exception:
                    continue
                stp = parsed.get("stp") or {}
                total = stp.get("ttl")
                if total is not None:
                    return int(total), host, None
            last_err = f"{host}({query_type}) 里没有 {date_str} 的 summary.stp.ttl"
    return None, None, last_err


def main() -> int:
    config = dict()
    raw_config = os.environ.get("CONFIG")
    if raw_config:
        try:
            config = dict(json.loads(raw_config))
        except Exception:
            print("CONFIG 不是合法 JSON，无法反查")
            return 0
    users = [u.strip() for u in str(config.get("USER", "")).split("#")]
    pwds = [p.strip() for p in str(config.get("PWD", "")).split("#")]
    users = [u for u in users if u]
    if len(users) != len(pwds):
        print(f"账号数[{len(users)}]和密码数[{len(pwds)}]不一致，跳过反查")
        return 0

    aes_key = os.environ.get("AES_KEY") or ""
    saved = load_user_tokens(aes_key)
    date_str = today_str()
    print(f"=== 反查云端步数（北京时间 {date_str}）===")

    ok, failed = 0, 0
    for idx, (user, password) in enumerate(zip(users, pwds), 1):
        norm = normalize_user(user)
        name = desensitize(norm)
        try:
            app_token, user_id, how = get_app_token(norm, password, saved)
            if not app_token:
                print(f"[{idx}/{len(users)}] {name} ❌ {how}")
                failed += 1
                continue
            steps, host, err = query_cloud_steps(app_token, user_id, date_str)
            if steps is None:
                print(f"[{idx}/{len(users)}] {name} ❌ 查询失败：{err}")
                failed += 1
                continue
            print(f"[{idx}/{len(users)}] {name} ✅ 云端当天步数 = {steps}（{how}，数据源 {host}）")
            ok += 1
        except Exception as e:
            print(f"[{idx}/{len(users)}] {name} ❌ 异常：{e}")
            failed += 1

    print(f"=== 反查完成：成功 {ok}，失败 {failed} ===")
    if ok == 0:
        print("::warning::反查没有读到任何账号的云端步数，请查看上面的报错（不影响本次刷步结果）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
