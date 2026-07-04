#!/usr/bin/env python3
"""Surveille l'API climradar et envoie un SMS (Free Mobile) quand une clim
surveillée devient disponible en livraison (FR) ou dans un magasin proche.

Pour éviter le spam (cron */5), l'état des offres disponibles est mémorisé
dans un ConfigMap in-cluster : un SMS n'est envoyé que lorsque cet état change.
"""
import json
import math
import os
import ssl
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get("API_URL", "https://climradar.fr/api/stock")
PRODUCTS = {p.strip() for p in os.environ.get("PRODUCTS", "").split(",") if p.strip()}
REF_LAT = float(os.environ["REF_LAT"])
REF_LON = float(os.environ["REF_LON"])
RADIUS_KM = float(os.environ.get("RADIUS_KM", "30"))
DELIVER_COUNTRIES = {
    c.strip().upper()
    for c in os.environ.get("DELIVER_COUNTRIES", "FR").split(",")
    if c.strip()
}
IN_STOCK = {
    s.strip()
    for s in os.environ.get("IN_STOCK_STATUSES", "en_stock").split(",")
    if s.strip()
}
FREE_USER = os.environ.get("FREE_SMS_USER")
FREE_PASS = os.environ.get("FREE_SMS_PASS")
STATE_CM = os.environ.get("STATE_CONFIGMAP", "climradar-stock-state")

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "climradar-watch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_offers(data):
    stores = {s["id"]: s for s in data.get("stores", [])}
    products = {p["id"]: p for p in data.get("products", [])}
    offers = []
    for store_id, rows in data.get("stockByStore", {}).items():
        store = stores.get(store_id)
        if not store:
            continue
        for row in rows:
            pid = row.get("productId")
            if PRODUCTS and pid not in PRODUCTS:
                continue
            if row.get("status") not in IN_STOCK:
                continue
            if store.get("channel") == "online":
                if store.get("country", "").upper() not in DELIVER_COUNTRIES:
                    continue
                where, dist = "livrable", None
            else:
                lat, lon = store.get("lat"), store.get("lon")
                if lat is None or lon is None:
                    continue
                dist = haversine(REF_LAT, REF_LON, lat, lon)
                if dist > RADIUS_KM:
                    continue
                where = f"~{dist:.0f} km"
            label = (products.get(pid, {}) or {}).get("shortLabel") or pid
            offers.append(
                {
                    "key": f"{store_id}:{pid}",
                    "label": label,
                    "store": store.get("name") or store_id,
                    "city": store.get("city", ""),
                    "price": row.get("price"),
                    "where": where,
                    "dist": dist if dist is not None else 0.0,
                }
            )
    offers.sort(key=lambda o: (o["where"] != "livrable", o["dist"], o["key"]))
    return offers


def format_sms(offers):
    lines = ["🌬️ Clim dispo !"]
    for o in offers[:8]:
        price = f" {o['price']}€" if o.get("price") is not None else ""
        city = f" {o['city']}" if o.get("city") else ""
        lines.append(f"- {o['label']} @ {o['store']}{city}{price} ({o['where']})")
    if len(offers) > 8:
        lines.append(f"… +{len(offers) - 8} autres")
    return "\n".join(lines)


def send_sms(message):
    params = urllib.parse.urlencode(
        {"user": FREE_USER, "pass": FREE_PASS, "msg": message}
    )
    url = f"https://smsapi.free-mobile.fr/sendmsg?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status


def try_send_sms(message):
    """Envoie best-effort : renvoie True si le SMS est parti (HTTP 200)."""
    try:
        code = send_sms(message)
        print(f"SMS envoyé (HTTP {code})")
        return True
    except urllib.error.HTTPError as e:
        hint = {402: "rate limit", 403: "creds/service KO", 400: "param manquant"}
        print(f"Échec SMS: HTTP {e.code} ({hint.get(e.code, '?')})")
    except Exception as e:  # noqa: BLE001
        print(f"Échec SMS: {e}")
    return False


# --- État persistant via ConfigMap in-cluster -------------------------------

def _k8s_ctx():
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    if not host:
        return None
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    with open(f"{SA_DIR}/token") as f:
        token = f.read().strip()
    with open(f"{SA_DIR}/namespace") as f:
        ns = f.read().strip()
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    return f"https://{host}:{port}", token, ns, ctx


def _k8s_req(method, path, body=None):
    base, token, ns, ctx = _K8S
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path.format(ns=ns), data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    ctype = "application/merge-patch+json" if method == "PATCH" else "application/json"
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, None


_K8S = _k8s_ctx()


def load_state():
    """Renvoie le dict `data` du ConfigMap d'état (vide si absent/inaccessible)."""
    if not _K8S:
        return {}
    try:
        status, cm = _k8s_req("GET", "/api/v1/namespaces/{ns}/configmaps/" + STATE_CM)
    except Exception as e:  # noqa: BLE001
        print(f"Lecture état KO: {e}")
        return {}
    if status == 200 and cm:
        return cm.get("data") or {}
    return {}


def save_state(patch):
    """Merge-patch des clés fournies dans le ConfigMap d'état (best-effort)."""
    if not _K8S:
        return
    try:
        path = "/api/v1/namespaces/{ns}/configmaps/" + STATE_CM
        status, _ = _k8s_req("PATCH", path, {"data": patch})
        if status == 404:
            body = {"metadata": {"name": STATE_CM}, "data": patch}
            _k8s_req("POST", "/api/v1/namespaces/{ns}/configmaps", body)
    except Exception as e:  # noqa: BLE001
        print(f"Écriture état KO: {e}")


def notify_offers(offers):
    new_sig = ";".join(o["key"] for o in offers)
    state = load_state()
    old_sig = state.get("signature", "")
    prev_error = state.get("error", "")

    print(f"{len(offers)} offre(s) dispo | sig={new_sig!r} old={old_sig!r}")

    patch = {}
    if new_sig != old_sig:
        if offers:
            # On ne mémorise la nouvelle signature que si le SMS est bien parti,
            # sinon on retentera au prochain tick (utile en cas de rate limit).
            if try_send_sms(format_sms(offers)):
                patch["signature"] = new_sig
        else:
            # Plus rien de dispo : on efface silencieusement.
            patch["signature"] = new_sig
    else:
        print("Pas de changement, aucun SMS.")

    # Le fetch/compute vient de réussir : si on était en erreur, on prévient.
    if prev_error:
        if try_send_sms("✅ climradar : fetch rétabli, surveillance OK"):
            patch["error"] = ""

    if patch:
        save_state(patch)


def notify_error(exc):
    traceback.print_exc()
    err = f"{type(exc).__name__}: {exc}"[:150]
    prev_error = load_state().get("error", "")
    if err != prev_error:
        if try_send_sms(f"⚠️ climradar en panne:\n{err}"):
            save_state({"error": err})
    else:
        print("Erreur déjà notifiée, pas de nouveau SMS.")


def main():
    if not FREE_USER or not FREE_PASS:
        sys.exit("FREE_SMS_USER / FREE_SMS_PASS manquants")
    try:
        data = http_get_json(API_URL)
        offers = find_offers(data)
    except Exception as exc:  # noqa: BLE001
        notify_error(exc)
        return
    notify_offers(offers)


if __name__ == "__main__":
    main()
