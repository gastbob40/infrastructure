#!/usr/bin/env python3
"""Surveille la disponibilité en livraison/retrait d'une clim chez ManoMano
(promesse de livraison GraphQL) et Castorama (fulfilment-options), et envoie
un SMS (Free Mobile) quand une option apparaît.

Pour éviter le spam (cron */5), l'état par enseigne est mémorisé dans un
ConfigMap in-cluster : un SMS n'est envoyé que lorsque cet état change.
"""
import json
import os
import ssl
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request

MANOMANO_API_URL = os.environ.get(
    "MANOMANO_API_URL", "https://graphql.manomano.fr/api/graphql"
)
MANOMANO_OFFER_ID = os.environ.get("MANOMANO_OFFER_ID", "")
CASTORAMA_API_URL = os.environ.get(
    "CASTORAMA_API_URL", "https://www.castorama.fr/casto-browse-mfe/api/fulfilment-options"
)
CASTORAMA_OFFER_ID = os.environ.get("CASTORAMA_OFFER_ID", "")
POSTAL_CODE = os.environ.get("POSTAL_CODE", "95240")
COUNTRY = os.environ.get("COUNTRY", "FR")
QUANTITY = int(os.environ.get("QUANTITY", "1"))
FREE_USER = os.environ.get("FREE_SMS_USER")
FREE_PASS = os.environ.get("FREE_SMS_PASS")
STATE_CM = os.environ.get("STATE_CONFIGMAP", "climradar-delivery-state")

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
# Castorama (Akamai) renvoie un 503 aux User-Agent non-navigateur.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

MANOMANO_QUERY = """
query DeliveryPromise($offerId: String!, $platform: Platform!, $market: Market!, $quantity: Int, $deliveryAddress: DeliveryPromiseDeliveryAddressInput) {
  deliveryPromise(
    offerId: $offerId
    platform: $platform
    market: $market
    quantity: $quantity
    deliveryAddress: $deliveryAddress
  ) {
    computedForPostalCode
    promises {
      name
      mode
      periodMin
      periodMax
      price {
        amount
        currency
      }
    }
  }
}
""".strip()


def http_get_json(url, params, headers):
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers=headers
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_manomano():
    """Renvoie les promesses de livraison dispo pour l'offre ManoMano."""
    variables = {
        "deliveryAddress": {"country": COUNTRY, "postalCode": POSTAL_CODE},
        "market": "B2C",
        "offerId": MANOMANO_OFFER_ID,
        "platform": "FR",
        "quantity": QUANTITY,
    }
    data = http_get_json(
        MANOMANO_API_URL,
        params={
            "operationName": "DeliveryPromise",
            "query": MANOMANO_QUERY,
            "variables": json.dumps(variables, separators=(",", ":")),
        },
        headers={
            "Accept": "application/json",
            "apollographql-client-name": "spartacux-b2c",
            "apollographql-client-version": "1.0",
            "User-Agent": USER_AGENT,
        },
    )
    if data.get("errors"):
        raise ValueError(f"réponse GraphQL en erreur: {data['errors']}")
    delivery = (data.get("data") or {}).get("deliveryPromise") or {}
    options = []
    for p in delivery.get("promises") or []:
        window = ""
        start, end = _format_period(p.get("periodMin")), _format_period(p.get("periodMax"))
        if start and end:
            window = f" {start}→{end}" if start != end else f" {start}"
        label = p.get("name") or MANOMANO_MODES.get(
            p.get("mode"), (p.get("mode") or "livraison").replace("_", " ").lower()
        )
        options.append(f"{label}{window}{_format_price(p.get('price'))}")
    return options


MANOMANO_MODES = {
    "POST_ORDER_SCHEDULED_DELIVERY": "livraison programmée",
    "SCHEDULED_DELIVERY": "livraison programmée",
    "HOME_DELIVERY": "livraison à domicile",
    "RELAY_DELIVERY": "point relais",
}


def _format_period(value):
    """periodMin/periodMax sont des horodatages ISO (ex: 2026-07-28T00:00Z)."""
    if value is None:
        return ""
    s = str(value)
    if "T" in s and s.count("-") >= 2:
        year, month, day = s.split("T")[0].split("-")[:3]
        return f"{day}/{month}"
    return s


def _format_price(price):
    amount = (price or {}).get("amount")
    if amount is None:
        return ""
    try:
        if float(amount) == 0:
            return " offerte"
    except (TypeError, ValueError):
        pass
    return f" {amount}{(price or {}).get('currency', '')}"


CASTORAMA_CHANNELS = {
    "homeDelivery": "livraison",
    "clickAndCollectStorePick": "retrait magasin",
    "clickAndCollectWarehousePick": "retrait entrepôt",
    "clickAndCollectFromMktp": "retrait marketplace",
}


def fetch_castorama():
    """Renvoie les canaux livraison/retrait dispo pour l'offre Castorama."""
    data = http_get_json(
        CASTORAMA_API_URL,
        params={"compositeOfferId": CASTORAMA_OFFER_ID},
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    options = []
    for item in data.get("data") or []:
        attrs = item.get("attributes") or {}
        for key, label in CASTORAMA_CHANNELS.items():
            availability = (attrs.get(key) or {}).get("availability")
            # Tout sauf NotAvailable est considéré dispo (enum non documenté).
            if availability and availability != "NotAvailable":
                options.append(f"{label} ({availability})")
    return options


SOURCES = [
    ("ManoMano", MANOMANO_OFFER_ID, fetch_manomano),
    ("Castorama", CASTORAMA_OFFER_ID, fetch_castorama),
]


def format_sms(source, options):
    lines = [f"📦 {source} : dispo pour {POSTAL_CODE} !"]
    for option in options[:4]:
        lines.append(f"- {option}")
    if len(options) > 4:
        lines.append(f"… +{len(options) - 4} autres")
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


def notify_options(source, options, state, patch):
    key = source.lower()
    new_sig = ";".join(options)
    old_sig = state.get(f"signature.{key}", "")

    print(f"{source}: {len(options)} option(s) dispo | sig={new_sig!r} old={old_sig!r}")

    if new_sig != old_sig:
        if options:
            # On ne mémorise la nouvelle signature que si le SMS est bien parti,
            # sinon on retentera au prochain tick (utile en cas de rate limit).
            if try_send_sms(format_sms(source, options)):
                patch[f"signature.{key}"] = new_sig
        else:
            # Plus rien de dispo : on efface silencieusement.
            patch[f"signature.{key}"] = new_sig
    else:
        print(f"{source}: pas de changement, aucun SMS.")

    # Le fetch vient de réussir : si on était en erreur, on prévient.
    if state.get(f"error.{key}"):
        if try_send_sms(f"✅ {source} : fetch rétabli, surveillance OK"):
            patch[f"error.{key}"] = ""


def notify_error(source, exc, state, patch):
    traceback.print_exc()
    err = f"{type(exc).__name__}: {exc}"[:150]
    if err != state.get(f"error.{source.lower()}", ""):
        if try_send_sms(f"⚠️ {source} watch en panne:\n{err}"):
            patch[f"error.{source.lower()}"] = err
    else:
        print(f"{source}: erreur déjà notifiée, pas de nouveau SMS.")


def main():
    if not FREE_USER or not FREE_PASS:
        sys.exit("FREE_SMS_USER / FREE_SMS_PASS manquants")
    state = load_state()
    patch = {}
    for source, offer_id, fetch in SOURCES:
        if not offer_id:
            continue
        try:
            options = fetch()
        except Exception as exc:  # noqa: BLE001
            notify_error(source, exc, state, patch)
            continue
        notify_options(source, options, state, patch)
    if patch:
        save_state(patch)


if __name__ == "__main__":
    main()
