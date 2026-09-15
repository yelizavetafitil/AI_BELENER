"""Проверка нормативов на tnpa.by (Национальный фонд ТНПА)."""

from __future__ import annotations

import http.client
import io
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import certifi

from belener.config import (
    stn_lookup_enabled,
    tnpa_budget_max_sec,
    tnpa_connect_timeout_sec,
    tnpa_max_queries,
    tnpa_parallel_workers,
    tnpa_timeout_sec,
)
from belener.stn_lookup import (
    StnCheckResult,
    _clean_stn_query,
    _core_digits,
    _digits_compatible,
    _norm_code,
    _year_from_code,
    is_stn_checkable,
    search_query,
    validity_status,
)

log = logging.getLogger("belener.tnpa_lookup")

_TNPA_ROUTE_BLOCKED_UNTIL = 0.0
_TNPA_ROUTE_BLOCKED_MSG = ""
_TNPA_ROUTE_LOCK = threading.RLock()


def _tnpa_open_timeout(read_sec: int | float | None = None) -> tuple[float, float]:
    read = float(read_sec if read_sec is not None else tnpa_timeout_sec())
    connect = min(float(tnpa_connect_timeout_sec()), max(5.0, read * 0.5))
    return (connect, read)


def _tnpa_request_timeout(read_sec: int | float | None = None) -> float:
    """Единый timeout для socket (Python 3.12 не принимает tuple в urlopen/HTTPSConnection)."""
    connect, read = _tnpa_open_timeout(read_sec)
    return max(connect, read)


def _tnpa_https_read(req: urllib.request.Request, *, read_sec: int | float | None = None) -> bytes:
    """HTTPS через http.client с одним timeout (совместимо с Python 3.12)."""
    timeout = _tnpa_request_timeout(read_sec)
    url = req.get_full_url()
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "tnpa.by"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {k: v for k, v in req.header_items()}
    ctx = _tnpa_ssl_context()
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    try:
        method = req.get_method()
        conn.request(method, path, body=req.data, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise urllib.error.HTTPError(
                url,
                resp.status,
                resp.reason,
                resp.headers,
                io.BytesIO(body),
            )
        return body
    finally:
        conn.close()


def _tnpa_err_text(exc: BaseException) -> str:
    return str(exc).casefold()


def _tnpa_is_route_error(exc: BaseException) -> bool:
    msg = _tnpa_err_text(exc)
    return any(
        x in msg
        for x in (
            "connection refused",
            "errno 111",
            "network is unreachable",
            "no route to host",
            "name or service not known",
            "getaddrinfo failed",
            "nodename nor servname",
        )
    )


def _tnpa_human_network_error(exc: BaseException) -> str:
    if isinstance(exc, TypeError) and "tuple" in str(exc).casefold():
        return "внутренняя ошибка клиента ТНПА (обновите web-контейнер)"
    msg = _tnpa_err_text(exc)
    if _tnpa_is_route_error(exc):
        return "tnpa.by недоступен с этого хоста (VPN/сеть/Docker)"
    if "timed out" in msg or "timeout" in msg:
        if "handshake" in msg or "ssl" in msg:
            return "таймаут SSL tnpa.by"
        return "таймаут ответа tnpa.by"
    return str(exc)[:240]


def _tnpa_route_blocked_message() -> str | None:
    with _TNPA_ROUTE_LOCK:
        if time.monotonic() < _TNPA_ROUTE_BLOCKED_UNTIL and _TNPA_ROUTE_BLOCKED_MSG:
            return _TNPA_ROUTE_BLOCKED_MSG
    return None


def _tnpa_mark_route_blocked(message: str, *, ttl_sec: float = 120.0) -> None:
    global _TNPA_ROUTE_BLOCKED_UNTIL, _TNPA_ROUTE_BLOCKED_MSG
    with _TNPA_ROUTE_LOCK:
        _TNPA_ROUTE_BLOCKED_UNTIL = time.monotonic() + max(30.0, ttl_sec)
        _TNPA_ROUTE_BLOCKED_MSG = message


def _tnpa_check_is_route_blocked(check: StnCheckResult) -> bool:
    err = (check.error or "").casefold()
    return "недоступен" in err or "connection refused" in err or "errno 111" in err


def tnpa_probe_host(client: TnpaClient) -> None:
    """Лёгкий запрос к API (не главная): таймаут SSL на / не должен блокировать поиск."""
    blocked = _tnpa_route_blocked_message()
    if blocked:
        raise urllib.error.URLError(blocked)
    params = urllib.parse.urlencode(
        {
            "page": 1,
            "per-page": 1,
            "sort": "b.KL",
            "SearchParam": "CH",
            "lang": "ru",
            "stateID": -1,
            "onlyActive": "null",
        }
    )
    req = urllib.request.Request(
        f"{client.base}/api/tnpadocs?{params}",
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{client.base}/",
        },
    )
    try:
        _tnpa_https_read(req, read_sec=client.timeout)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as e:
        if _tnpa_is_route_error(e):
            msg = _tnpa_human_network_error(e)
            _tnpa_mark_route_blocked(msg)
            raise urllib.error.URLError(msg) from e
        raise


def _tnpa_probe_soft(client: TnpaClient) -> str | None:
    """Жёсткий стоп только без маршрута; таймаут/SSL — продолжаем пакет."""
    try:
        tnpa_probe_host(client)
    except Exception as e:
        if _tnpa_is_route_error(e):
            return _tnpa_human_network_error(e)
        blocked = _tnpa_route_blocked_message()
        if blocked:
            return blocked
        log.warning("TNPA probe inconclusive, continuing API batch: %s", e)
    return None


def _tnpa_unavailable_results(items: list[dict[str, str]], message: str) -> list[StnCheckResult]:
    out: list[StnCheckResult] = []
    for item in items:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        out.append(
            StnCheckResult(
                kind=kind,
                ref=ref,
                query=search_query(kind, ref),
                found=False,
                status="ошибка проверки",
                error=message,
            )
        )
    return out

_TNPA_INTERMEDIATE_PEM: bytes | None = None
_TNPA_SSL_CTX: ssl.SSLContext | None = None
_TNPA_TRUST_CA_PATH: str | None = None
_TNPA_SSL_LOCK = threading.RLock()
_TNPA_SSL_PROBE_DONE = False

# Известные промежуточные CA AlphaSSL (GlobalSign) — на случай если AIA недоступен.
_TNPA_KNOWN_INTERMEDIATE_URLS = (
    "https://secure.globalsign.com/cacert/gsgccr46alphasslca2025.crt",
    "https://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt",
    "http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt",
    "https://secure.globalsign.com/cacert/gsalphasssl2.crt",
    "https://secure.globalsign.com/cacert/gsalphasha2g2r1.crt",
)

# R6 — стандартный AlphaSSL 2025; R46 — новая иерархия GlobalSign (tnpa.by мог мигрировать).
_TNPA_R46_INTERMEDIATE_PEM_EMBEDDED = b"""-----BEGIN CERTIFICATE-----
MIIFfjCCA2agAwIBAgIRAIRDWG9jliZDgTN8gBouYRgwDQYJKoZIhvcNAQELBQAw
RjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYtc2ExHDAaBgNV
BAMTE0dsb2JhbFNpZ24gUm9vdCBSNDYwHhcNMjUwOTE3MDI1NTMwWhcNMjkwNjIz
MDAwMDAwWjBWMQswCQYDVQQGEwJCRTEZMBcGA1UEChMQR2xvYmFsU2lnbiBudi1z
YTEsMCoGA1UEAxMjR2xvYmFsU2lnbiBHQ0MgUjQ2IEFscGhhU1NMIENBIDIwMjUw
ggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCV0s/1NfwruxzhrfoOWN/B
V8j/6KxInyuIpVJ50pmHimyU5ACjC4ST2ZyU2Ltggfc/ydc1OrTUqDyoTGjWOazH
obK+GQQtBd+MUEykZbnVCfNj14Um7UiqTGOFaOK551Etd9aE/F9/hYl+4XMXJPqC
+V3ziJbO0QQcL4/wZBvqbW3xZjqb/bcLKHttfNF+JMmO+DrT9VWyomNWVeC3Grs4
DybZLgs4RVmg8sp/Ic0quOdBjIhE+W0jYbUW6Z9HP9q2lh7UVCwn1rZ1mFTRhIXa
shGqL43uXnXfRls3T93w2dnCpgTundq28WmWuzb/RZ6kHvALQmu8f891gHfbb+kb
AgMBAAGjggFVMIIBUTAOBgNVHQ8BAf8EBAMCAYYwEwYDVR0lBAwwCgYIKwYBBQUH
AwEwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQUo0yssb+0iO2LdNhfNDjJ
6QolnnswHwYDVR0jBBgwFoAUA1yrc4GHqMywptWU4jaWSf8FmSwwewYIKwYBBQUH
AQEEbzBtMC4GCCsGAQUFBzABhiJodHRwOi8vb2NzcC5nbG9iYWxzaWduLmNvbS9y
b290cjQ2MDsGCCsGAQUFBzAChi9odHRwOi8vc2VjdXJlLmdsb2JhbHNpZ24uY29t
L2NhY2VydC9yb290cjQ2LmNydDA2BgNVHR8ELzAtMCugKaAnhiVodHRwOi8vY3Js
Lmdsb2JhbHNpZ24uY29tL3Jvb3RyNDYuY3JsMCEGA1UdIAQaMBgwCAYGZ4EMAQIB
MAwGCisGAQQBoDIKAQMwDQYJKoZIhvcNAQELBQADggIBAFidiFkGjaslf0o1kWbY
Y1Fe0N/OtR28cj5js0b6mhb0AXgyi8m3IOBBnHFsyGb/OGpGlsfnyOCNHNc4p12Z
f8tqkqtd1qh2oks7+MvEAatwDy4NMlQYmjRpdTzTu6+HFv3waK+UOHbm1NC5s5fb
lPjio082KdjQsG+isWSCUGP7hjVjTcPioy5v0HJDYzmbX1oro7fa7potZ4vjNPRI
mMH2St+E2OphOO4NkrllXtSUw5ThyiFaymFIvWfSXSWOHIcK3HwOlUxgpgrJMDi0
ZuKX3W2+wDVRmrPJXgaX+6R/uBqtdMi2O+ebkjmS5zyk2U7sHsaa9lPQz3PS5hBv
aeW0FHeJK4Yc2yeQ/HBRL3YORG5JQdH1+P/+OJnv7s10Qjipe0tPwHccfMSprzRs
0t/2wG3b2GdTBX9JJjxWp3SJs/Bib7ScMJYyMMgrUBQ/BSCreEpvKvrsw2SAsPwY
dx7fjCpsFBM0Tdrqzc1HUm/qNgETPA6tWTMn+27ot19Q94KpnEHYL1hRyCJ5JB/6
OWVNCbn2YhtwJON6787ZbkVHOz9itAZKajPNH/nO/wB4gtlhnQb1yhZ6nG6LZAsu
gJBL58/BSqH1KWGnHyp9s7VwFJPI3LoSEqLd5BryAOQg/5P5uW339YFmbJCcqS3G
3CVvDrQnx+qrRlxBrS/QeigQ
-----END CERTIFICATE-----
"""

# Встроенный промежуточный CA R6 на случай, если belener/certs/… недоступен в контейнере.
_TNPA_INTERMEDIATE_PEM_EMBEDDED = b"""-----BEGIN CERTIFICATE-----
MIIFjTCCA3WgAwIBAgIRAIN9TriekS/nLK07x2kt3CAwDQYJKoZIhvcNAQELBQAw
TDEgMB4GA1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjYxEzARBgNVBAoTCkds
b2JhbFNpZ24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMjUwNTIxMDIzNjUyWhcN
MjcwNTIxMDAwMDAwWjBVMQswCQYDVQQGEwJCRTEZMBcGA1UEChMQR2xvYmFsU2ln
biBudi1zYTErMCkGA1UEAxMiR2xvYmFsU2lnbiBHQ0MgUjYgQWxwaGFTU0wgQ0Eg
MjAyNTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAJ/oiu0Bviq52UUE
ADbFWmgu3rC7KDSMoorLN1Wd03McG3Z1aP71DlPCE33838r72Dfuj5M9LXfiQLJp
Au6MwNExmKOzothw4x0zGf5oBYyrCMGm3fBpLPafwYQ3MchBOWMTbf83rKUPLH48
KCJ0MnU8GUl8oA/J81wIvbbKPuNrFf6hvJDccjzc4NyxLz3A89zjV2g5whCg5O0u
9YX4Zxk9JHuc/LvllOJO4waAYLjbWBJkz3rV3ts1SmSYnJqmyRTIjXwQgRvhEYqt
DbRskt0W7M6cPwCze3GTBN2UHNpHkMs3YmVxku68I0aOQn5+uz//fDROP3z1Z/7I
APteRtECAwEAAaOCAV8wggFbMA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBggr
BgEFBQcDAQYIKwYBBQUHAwIwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQU
xbSTj28r3B5Iv7cQMIXO0bK7SC0wHwYDVR0jBBgwFoAUrmwFo5MT4qLn4tcc1sfw
f8hnU6AwewYIKwYBBQUHAQEEbzBtMC4GCCsGAQUFBzABhiJodHRwOi8vb2NzcDIu
Z2xvYmFsc2lnbi5jb20vcm9vdHI2MDsGCCsGAQUFBzAChi9odHRwOi8vc2VjdXJl
Lmdsb2JhbHNpZ24uY29tL2NhY2VydC9yb290LXI2LmNydDA2BgNVHR8ELzAtMCug
KaAnhiVodHRwOi8vY3JsLmdsb2JhbHNpZ24uY29tL3Jvb3QtcjYuY3JsMCEGA1Ud
IAQaMBgwCAYGZ4EMAQIBMAwGCisGAQQBoDIKAQMwDQYJKoZIhvcNAQELBQADggIB
AB/uvBuZf4CiuSahwiXn4geF52roAH+6jxsEPTXTfb7bbeMDXsYgRRsOTNA70ruZ
Tnz5DfFMuBhNoFhIFb0qR1izdy6VkdKOqFPNF2dOFI1EcnY9l2ory9mrzHqVbrL4
vzUd17FLUVyjTVU7PAv4nxyhnO1GTeT83YlrdRF31NyR6bvZVTEERHmpbWSgeveJ
LRtaMzlGWiLZ8IwkH7o6GH3jp/KPtDW4Npu8w64HrRZdN2pqQhi7+YKwfHM7H+2U
dM1BGN0sjOWMVbMSB9MtCsleS2Mb7TRZEbOHxECJLLIluQypZr7Pol3+hAqrhyKI
k+6y+Da0NeDuWxW59Ku4NvClqW1UFX1SpfNGhzVfp/CH+vPM1tySomx2jE0EnYZu
GwVucXPBsp5nUWqUV9+143glVuS7GTg9hFPjNBInn17HbCoIIQIOzj5Vd9bK3A9U
GxXNpwenDHEalCsD/4eQYDHPhFE7sNe0D/OXu+FAM02VZkARx37Jp4bDdujvgL9P
vZPR3wThvDN1CTU8Bc3xea3yKFAraKcPZLkhReQUAm2VpR+HSJRPlUpYizlF9WkL
h3KcAVCBJWvnOkVwxyU5QJMcnwW95JlOtx+9100GL99jHE5rs3gXp7F4bg8H01QT
9jVOhBBmQ7nQoXuwI0tqal2QUqZz3eeu62CU7xBwtfYR
-----END CERTIFICATE-----
"""


def _normalize_pem_cert(raw: bytes) -> bytes:
    if not raw or not raw.strip():
        return b""
    text = raw.decode("utf-8", errors="replace").strip().replace("\r\n", "\n")
    if "-----BEGIN CERTIFICATE-----" not in text:
        return b""
    return (text + "\n").encode("utf-8")


def _tnpa_cert_paths() -> list[Path]:
    base = Path(__file__).resolve().parent / "certs"
    paths = sorted(base.glob("globalsign*.pem"))
    if paths:
        return paths
    return [
        base / "globalsign-r6-alphassl-2025.pem",
        base / "globalsign-r46-alphassl-2025.pem",
    ]


def reset_tnpa_route_cache() -> None:
    global _TNPA_ROUTE_BLOCKED_UNTIL, _TNPA_ROUTE_BLOCKED_MSG
    with _TNPA_ROUTE_LOCK:
        _TNPA_ROUTE_BLOCKED_UNTIL = 0.0
        _TNPA_ROUTE_BLOCKED_MSG = ""


def reset_tnpa_ssl() -> None:
    """Сброс кэша SSL (после обновления CA)."""
    global _TNPA_SSL_CTX, _TNPA_INTERMEDIATE_PEM, _TNPA_TRUST_CA_PATH, _TNPA_SSL_PROBE_DONE
    reset_tnpa_route_cache()
    with _TNPA_SSL_LOCK:
        _TNPA_SSL_CTX = None
        _TNPA_INTERMEDIATE_PEM = None
        _TNPA_TRUST_CA_PATH = None
        _TNPA_SSL_PROBE_DONE = False


def _der_to_pem(data: bytes) -> bytes:
    import base64

    body = base64.encodebytes(data).replace(b"\n", b"")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return b"-----BEGIN CERTIFICATE-----\n" + b"\n".join(lines) + b"\n-----END CERTIFICATE-----\n"


def _load_tnpa_intermediate_pem() -> bytes:
    """Промежуточные CA tnpa.by: GlobalSign AlphaSSL R6 и R46 (2025)."""
    global _TNPA_INTERMEDIATE_PEM
    if _TNPA_INTERMEDIATE_PEM:
        return _TNPA_INTERMEDIATE_PEM

    chunks: list[bytes] = []
    seen: set[bytes] = set()
    for bundled in _tnpa_cert_paths():
        if not bundled.is_file():
            continue
        pem = _normalize_pem_cert(bundled.read_bytes())
        if pem and pem not in seen:
            seen.add(pem)
            chunks.append(pem)

    for fallback in (
        _TNPA_INTERMEDIATE_PEM_EMBEDDED,
        _TNPA_R46_INTERMEDIATE_PEM_EMBEDDED,
    ):
        pem = _normalize_pem_cert(fallback)
        if pem and pem not in seen:
            seen.add(pem)
            chunks.append(pem)

    if not chunks:
        for url in (
            "https://secure.globalsign.com/cacert/gsgccr46alphasslca2025.crt",
            "https://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt",
            "http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt",
        ):
            try:
                with urllib.request.urlopen(url, timeout=12) as resp:
                    raw = resp.read()
                if raw.startswith(b"-----BEGIN"):
                    pem = _normalize_pem_cert(raw)
                else:
                    pem = _normalize_pem_cert(_der_to_pem(raw))
                if pem and pem not in seen:
                    seen.add(pem)
                    chunks.append(pem)
            except Exception:
                continue

    _TNPA_INTERMEDIATE_PEM = b"".join(chunks)
    return _TNPA_INTERMEDIATE_PEM


def _tnpa_download_pem(url: str, *, timeout: float = 15) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; belener-tnpa/1.0)"},
    )
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    if raw.startswith(b"-----BEGIN"):
        return _normalize_pem_cert(raw)
    return _normalize_pem_cert(_der_to_pem(raw))


def _tnpa_probe_leaf(host: str, *, timeout: float = 20) -> tuple[str | None, bytes | None]:
    """Сертификат tnpa.by без verify — узнать issuer и скачать CA по AIA."""
    host = (host or "tnpa.by").split("//")[-1].split("/")[0]
    ss = None
    try:
        sock = socket.create_connection((host, 443), timeout=timeout)
        probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        probe.check_hostname = False
        probe.verify_mode = ssl.CERT_NONE
        ss = probe.wrap_socket(sock, server_hostname=host)
        der = ss.getpeercert(binary_form=True)
        if not der:
            return None, None
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        leaf = x509.load_der_x509_certificate(der, default_backend())
        return leaf.issuer.rfc4514_string(), der
    finally:
        if ss is not None:
            try:
                ss.close()
            except OSError:
                pass


def _tnpa_intermediate_from_aia(leaf_der: bytes) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    leaf = x509.load_der_x509_certificate(leaf_der, default_backend())
    try:
        aia = leaf.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
    except x509.ExtensionNotFound:
        return b""
    for entry in aia.value:
        if entry.access_method != AuthorityInformationAccessOID.CA_ISSUERS:
            continue
        url = getattr(entry.access_location, "value", None)
        if not url:
            continue
        try:
            pem = _tnpa_download_pem(str(url))
            if pem:
                return pem
        except Exception:
            continue
    return b""


def _tnpa_append_trust_pem(pem: bytes) -> bool:
    global _TNPA_INTERMEDIATE_PEM, _TNPA_TRUST_CA_PATH, _TNPA_SSL_CTX
    pem = _normalize_pem_cert(pem)
    if not pem:
        return False
    with _TNPA_SSL_LOCK:
        base = _load_tnpa_intermediate_pem()
        if pem in base:
            return False
        _TNPA_INTERMEDIATE_PEM = base + pem
        _TNPA_TRUST_CA_PATH = None
        _TNPA_SSL_CTX = None
        return True


def warm_tnpa_ssl_trust(host: str | None = None) -> None:
    """Быстрый SSL warm: только bundled R6/R46 (без сетевого probe — он давал 20с+)."""
    global _TNPA_SSL_PROBE_DONE
    del host  # AIA/probe только при verify failed
    with _TNPA_SSL_LOCK:
        if _TNPA_SSL_PROBE_DONE:
            return
    try:
        _load_tnpa_intermediate_pem()
        _tnpa_ssl_context()
    except Exception as e:
        log.warning("TNPA SSL warm (bundled): %s", e)
    with _TNPA_SSL_LOCK:
        _TNPA_SSL_PROBE_DONE = True


def _tnpa_refresh_ssl_on_verify_error(host: str | None = None) -> bool:
    global _TNPA_SSL_PROBE_DONE
    host = (host or "tnpa.by").split("//")[-1].split("/")[0]
    log.warning("TNPA SSL: verify failed — повторная загрузка CA для %s", host)
    with _TNPA_SSL_LOCK:
        _TNPA_SSL_PROBE_DONE = False
    reset_tnpa_ssl()
    try:
        warm_tnpa_ssl_trust(host)
        return True
    except Exception as e:
        log.warning("TNPA SSL: refresh failed: %s", e)
        return False


def _tnpa_trust_ca_path() -> str:
    """Файл с промежуточными CA tnpa.by (R6 + R46) для load_verify_locations."""
    global _TNPA_TRUST_CA_PATH
    if _TNPA_TRUST_CA_PATH and Path(_TNPA_TRUST_CA_PATH).is_file():
        return _TNPA_TRUST_CA_PATH

    with _TNPA_SSL_LOCK:
        if _TNPA_TRUST_CA_PATH and Path(_TNPA_TRUST_CA_PATH).is_file():
            return _TNPA_TRUST_CA_PATH

        intermediates = _load_tnpa_intermediate_pem()
        if not intermediates:
            log.warning("TNPA SSL: промежуточные CA не найдены")
            _TNPA_TRUST_CA_PATH = certifi.where()
            return _TNPA_TRUST_CA_PATH

        cache_candidates: list[Path] = []
        env_path = (os.environ.get("PDF_TNPA_CA_BUNDLE") or "").strip()
        if env_path:
            cache_candidates.append(Path(env_path))
        data_tmp = Path("/app/data/tmp")
        if data_tmp.is_dir():
            cache_candidates.append(data_tmp / "tnpa-trust-intermediates.pem")
        cache_candidates.append(
            Path(os.environ.get("TEMP") or "/tmp") / "belener-tnpa-trust-intermediates.pem"
        )

        for cache_path in cache_candidates:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(intermediates)
                _TNPA_TRUST_CA_PATH = str(cache_path)
                log.info(
                    "TNPA SSL: промежуточные CA (R6+R46) -> %s (%d bytes)",
                    cache_path,
                    len(intermediates),
                )
                return _TNPA_TRUST_CA_PATH
            except OSError:
                continue

        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix="tnpa-trust-", suffix=".pem")
        with os.fdopen(fd, "wb") as fh:
            fh.write(intermediates)
        _TNPA_TRUST_CA_PATH = tmp_path
        log.info("TNPA SSL: промежуточные CA во временном файле %s", tmp_path)
        return _TNPA_TRUST_CA_PATH


def _tnpa_ssl_context() -> ssl.SSLContext:
    global _TNPA_SSL_CTX
    if _TNPA_SSL_CTX is not None:
        return _TNPA_SSL_CTX
    with _TNPA_SSL_LOCK:
        if _TNPA_SSL_CTX is not None:
            return _TNPA_SSL_CTX
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=certifi.where())
        trust_path = _tnpa_trust_ca_path()
        if trust_path != certifi.where():
            ctx.load_verify_locations(cafile=trust_path)
        _TNPA_SSL_CTX = ctx
        return _TNPA_SSL_CTX


def tnpa_base_url() -> str:
    try:
        from belener.integration_store import get_tnpa_credentials

        creds = get_tnpa_credentials()
        if creds.get("base_url"):
            return creds["base_url"].rstrip("/")
    except Exception:
        pass
    return (os.environ.get("PDF_TNPA_BASE_URL") or "https://tnpa.by").strip().rstrip("/")


class TnpaClient:
    def __init__(self, base_url: str | None = None, *, timeout_sec: int | None = None) -> None:
        self.base = (base_url or tnpa_base_url()).rstrip("/")
        self.timeout = timeout_sec if timeout_sec is not None else tnpa_timeout_sec()
        self._cache: dict[tuple[str, int, int], list[dict]] = {}
        self._cache_lock = threading.Lock()

    def open_timeout(self) -> tuple[float, float]:
        return _tnpa_open_timeout(self.timeout)

    def search_docs(self, query: str, *, page: int = 1, per_page: int = 30) -> list[dict]:
        q = (query or "").strip()
        cache_key = (q.upper(), page, per_page)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [dict(x) for x in cached]

        params = urllib.parse.urlencode(
            {
                "page": page,
                "per-page": per_page,
                "sort": "b.KL",
                "SearchParam": q.upper(),
                "lang": "ru",
                "stateID": -1,
                "onlyActive": "null",
            }
        )
        req = urllib.request.Request(
            f"{self.base}/api/tnpadocs?{params}",
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": f"{self.base}/",
            },
        )
        last_err: Exception | None = None
        ssl_refreshed = False
        read_timeout_retries = 0
        host = urllib.parse.urlparse(self.base).hostname or "tnpa.by"
        for attempt in range(3):
            try:
                raw = _tnpa_https_read(req, read_sec=self.timeout).decode("utf-8", errors="replace")
                data = json.loads(raw)
                rows: list[dict] = []
                if isinstance(data, list):
                    rows = [dict(x) for x in data if isinstance(x, dict)]
                elif isinstance(data, dict):
                    for key in ("items", "data", "models"):
                        if isinstance(data.get(key), list):
                            rows = [dict(x) for x in data[key] if isinstance(x, dict)]
                            break
                # Пустой ответ не кэшируем: tnpa.by иногда отдаёт [] при перегрузке.
                if rows:
                    with self._cache_lock:
                        self._cache[cache_key] = [dict(x) for x in rows]
                return rows
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ssl.SSLError) as e:
                last_err = e
                msg = _tnpa_err_text(e)
                if _tnpa_is_route_error(e):
                    blocked = _tnpa_human_network_error(e)
                    _tnpa_mark_route_blocked(blocked)
                    raise urllib.error.URLError(blocked) from e
                if "certificate verify failed" in msg and not ssl_refreshed:
                    ssl_refreshed = True
                    if _tnpa_refresh_ssl_on_verify_error(host):
                        continue
                # Полный read-timeout — один повтор; SSL handshake — один повтор.
                if any(x in msg for x in ("timed out", "timeout")):
                    if read_timeout_retries < 1 and "handshake" not in msg:
                        read_timeout_retries += 1
                        time.sleep(2.0)
                        continue
                    if attempt < 1 and ("handshake" in msg or "ssl" in msg):
                        time.sleep(1.5)
                        continue
                    raise
                retryable = any(
                    x in msg
                    for x in (
                        "temporarily",
                        "reset",
                        "eof",
                        "503",
                        "502",
                        "429",
                    )
                )
                if attempt < 2 and retryable:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
        if last_err is not None:
            raise last_err
        return []


def _tnpa_search_queries(kind: str, ref: str) -> list[str]:
    """Короткий приоритетный список запросов для tnpa.by."""
    from belener.stn_lookup import _extract_number_part

    kind = (kind or "").strip()
    full = search_query(kind, ref)
    num = _extract_number_part(kind, ref)
    out: list[str] = []
    for q in (full, num, f"{kind} {num}".strip() if num else ""):
        q = _clean_stn_query(q)
        if q and q not in out:
            out.append(q)
    return out[: tnpa_max_queries()]


def _parse_tnpa_date(raw: object) -> date | None:
    s = str(raw or "").strip()
    if not s or s.lower() in ("null", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10] if fmt.startswith("%Y") else s, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _format_tnpa_date(raw: object) -> str:
    d = _parse_tnpa_date(raw)
    return d.strftime("%d.%m.%Y") if d else ""


def _tnpa_designation(row: dict) -> str:
    parts = [
        str(row.get("Number") or "").strip(),
        str(row.get("OND") or "").strip(),
        str(row.get("OND1") or "").strip(),
    ]
    if not any(parts):
        parts = [str(row.get("NumRes") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _pick_best_tnpa_match(kind: str, ref: str, rows: list[dict]) -> dict | None:
    """Среди всех строк с тем же номером (годы/«Взамен») — самая свежая действующая."""
    if not rows:
        return None
    target_full = _norm_code(search_query(kind, ref))
    target_digits = _core_digits(kind, ref)

    def _compatible(row: dict) -> bool:
        code = _tnpa_designation(row)
        name = str(row.get("NND") or "")
        code_n = _norm_code(code)
        name_n = _norm_code(name)
        row_digits = re.sub(r"\D", "", code_n)
        name_digits = re.sub(r"\D", "", name_n)
        if target_digits and len(target_digits) >= 4:
            code_ok = _digits_compatible(target_digits, row_digits) or target_full in code_n
            name_ok = _digits_compatible(target_digits, name_digits) or target_full in name_n
            return bool(code_ok or name_ok)
        if target_full and (target_full in code_n or code_n in target_full or target_full in name_n):
            return True
        return False

    candidates = [row for row in rows if _compatible(row)]
    if not candidates:
        return None

    today = date.today()

    def _freshness(row: dict) -> tuple:
        code = _tnpa_designation(row)
        intro = _parse_tnpa_date(row.get("DTTN")) or date.min
        year = _year_from_code(code)
        prizn = str(row.get("PRIZN_BD") or "").strip()
        active = 0 if prizn == "0" else 1
        cancel = _parse_tnpa_date(_tnpa_cancel_raw(row))
        still_ok = 0 if (cancel is not None and cancel <= today) else 1
        return (active, still_ok, intro.toordinal(), year)

    return max(candidates, key=_freshness)


def _tnpa_cancel_raw(row: dict) -> object:
    """Дата отмены/окончания действия на tnpa.by — только DTTK.

    DSMSOS нельзя брать как отмену: у действующих («Взамен» и др.)
    оно часто равно DTTN (дате введения), из‑за чего в таблице
    появлялось «Отменен = Введен».
    """
    return row.get("DTTK")


def _tnpa_status(row: dict, *, today: date | None = None) -> str:
    intro = _parse_tnpa_date(row.get("DTTN"))
    cancel = _parse_tnpa_date(_tnpa_cancel_raw(row))

    # PRIZN_BD=0 — отменён в фонде; без DTTK не подставляем фейковую дату.
    if str(row.get("PRIZN_BD") or "").strip() == "0":
        if cancel is not None:
            return validity_status(intro, cancel, today=today)
        return "отменён"

    return validity_status(intro, cancel, today=today)


def lookup_one_tnpa(
    kind: str,
    ref: str,
    *,
    client: TnpaClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> StnCheckResult:
    kind = (kind or "").strip()
    ref = (ref or "").strip()
    sheet_ref = ref
    queries = _tnpa_search_queries(kind, ref)
    query = queries[0] if queries else search_query(kind, ref)
    out = StnCheckResult(kind=kind, ref=sheet_ref, query=query, found=False)

    if not is_stn_checkable(kind):
        out.status = "не в фонде ТНПА"
        out.query = ""
        return out

    cli = client or _default_client()
    t0 = time.monotonic()
    skipped_budget = False
    tried: list[str] = []
    try:
        if deadline is not None and time.monotonic() >= deadline:
            out.status = "пропущено (бюджет времени)"
            return out
        match: dict | None = None
        for raw_q in queries:
            if deadline is not None and time.monotonic() >= deadline:
                skipped_budget = True
                break
            q = _clean_stn_query(raw_q)
            if not q or q in tried:
                continue
            tried.append(q)
            rows = cli.search_docs(q)
            match = _pick_best_tnpa_match(kind, ref, rows)
            if match:
                break
            # Пустой ответ: сразу следующий вариант, без ожидания.
        if not match:
            out.query = "; ".join(tried[:4])
            out.status = "пропущено (бюджет времени)" if skipped_budget else "нет в ТНПА"
            return out

        rn = str(match.get("RN") or "")
        idglobal = str(match.get("IDGLOBAL") or "")
        code = _tnpa_designation(match)
        out.found = True
        out.doc_id = f"{rn}/{idglobal}" if rn and idglobal else idglobal or rn
        out.stn_code = code
        out.stn_name = str(match.get("NND") or "")
        out.intro_date = _format_tnpa_date(match.get("DTTN"))
        out.cancel_date = _format_tnpa_date(_tnpa_cancel_raw(match))
        out.status = _tnpa_status(match, today=today)
        out.query = "; ".join(tried[:4])
        log.info("TNPA lookup %s %s -> %s in %.1fs", kind, ref, out.status, time.monotonic() - t0)
        return out
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        msg = _tnpa_err_text(e)
        if not _tnpa_is_route_error(e) and any(x in msg for x in ("timed out", "timeout")):
            from belener.stn_lookup import _extract_number_part

            num = _extract_number_part(kind, ref)
            alt_queries: list[str] = []
            for raw_q in (
                search_query(kind, ref),
                f"{kind} {num}".strip() if num else "",
                num,
                *_tnpa_search_queries(kind, ref),
            ):
                q = _clean_stn_query(raw_q)
                if q and q not in alt_queries:
                    alt_queries.append(q)
            for raw_q in alt_queries:
                if raw_q in tried:
                    continue
                q = _clean_stn_query(raw_q)
                if not q:
                    continue
                tried.append(q)
                try:
                    rows = cli.search_docs(q)
                    match = _pick_best_tnpa_match(kind, ref, rows)
                    if match:
                        rn = str(match.get("RN") or "")
                        idglobal = str(match.get("IDGLOBAL") or "")
                        code = _tnpa_designation(match)
                        out.found = True
                        out.doc_id = f"{rn}/{idglobal}" if rn and idglobal else idglobal or rn
                        out.stn_code = code
                        out.stn_name = str(match.get("NND") or "")
                        out.intro_date = _format_tnpa_date(match.get("DTTN"))
                        out.cancel_date = _format_tnpa_date(_tnpa_cancel_raw(match))
                        out.status = _tnpa_status(match, today=today)
                        out.query = "; ".join(tried[:4])
                        log.info(
                            "TNPA lookup %s %s -> %s (retry) in %.1fs",
                            kind,
                            ref,
                            out.status,
                            time.monotonic() - t0,
                        )
                        return out
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                    break
        log.warning("TNPA lookup failed kind=%s ref=%s: %s", kind, ref, e)
        out.error = _tnpa_human_network_error(e)
        out.status = "ошибка проверки"
        return out


def refine_and_check_normative_refs_tnpa(
    refs: list[dict[str, str]],
    *,
    client: TnpaClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, str]], list[StnCheckResult]]:
    if not stn_lookup_enabled():
        return list(refs or []), []

    checkable_refs = [
        dict(item)
        for item in (refs or [])
        if str(item.get("kind") or "").strip() and str(item.get("ref") or "").strip()
    ]
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in checkable_refs:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        key = (kind.casefold(), _norm_code(search_query(kind, ref)))
        if key in seen:
            continue
        seen.add(key)
        items.append(dict(item))
    if not items:
        return list(refs or []), []

    workers = min(tnpa_parallel_workers(), len(items))
    log.info(
        "TNPA batch: %s refs, workers=%s, timeout=%ss, connect=%ss",
        len(items),
        workers,
        tnpa_timeout_sec(),
        tnpa_connect_timeout_sec(),
    )
    t_batch = time.monotonic()
    blocked = _tnpa_route_blocked_message()
    if blocked:
        log.warning("TNPA batch skipped (cached): %s", blocked)
        return list(refs or []), _tnpa_unavailable_results(items, blocked)

    try:
        warm_tnpa_ssl_trust(urllib.parse.urlparse(tnpa_base_url()).hostname or "tnpa.by")
    except Exception as e:
        log.warning("TNPA SSL warm failed: %s", e)
    shared_cli = client or TnpaClient()
    probe_block = _tnpa_probe_soft(shared_cli)
    if probe_block:
        log.warning("TNPA batch skipped (no route): %s", probe_block)
        return list(refs or []), _tnpa_unavailable_results(items, probe_block)

    checks_by_item: dict[tuple[str, str], StnCheckResult] = {}

    def _item_key(item: dict[str, str]) -> tuple[str, str]:
        return (
            str(item.get("kind") or "").strip().casefold(),
            str(item.get("ref") or "").strip().casefold(),
        )

    def _run_one(item: dict[str, str]) -> tuple[tuple[str, str], StnCheckResult]:
        key = _item_key(item)
        result = lookup_one_tnpa(
            str(item.get("kind") or ""),
            str(item.get("ref") or ""),
            client=shared_cli,
            today=today,
            deadline=deadline,
        )
        return key, result

    if workers <= 1:
        for i, item in enumerate(items):
            key, result = _run_one(item)
            checks_by_item[key] = result
            if _tnpa_check_is_route_blocked(result):
                log.warning("TNPA batch stopped: %s", result.error)
                break
            if i + 1 < len(items):
                time.sleep(0.35)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_one, item): item for item in items}
            for fut in as_completed(futs):
                item = futs[fut]
                key = _item_key(item)
                try:
                    _, result = fut.result()
                    checks_by_item[key] = result
                except Exception as e:
                    checks_by_item[key] = StnCheckResult(
                        kind=str(item.get("kind") or ""),
                        ref=str(item.get("ref") or ""),
                        query=search_query(str(item.get("kind") or ""), str(item.get("ref") or "")),
                        found=False,
                        status="ошибка проверки",
                        error=str(e),
                    )

    checks: list[StnCheckResult] = []
    for item in items:
        key = _item_key(item)
        if key in checks_by_item:
            checks.append(checks_by_item[key])
        else:
            msg = _tnpa_route_blocked_message() or "tnpa.by недоступен с этого хоста (VPN/сеть/Docker)"
            checks.append(
                StnCheckResult(
                    kind=str(item.get("kind") or "").strip(),
                    ref=str(item.get("ref") or "").strip(),
                    query=search_query(str(item.get("kind") or ""), str(item.get("ref") or "")),
                    found=False,
                    status="ошибка проверки",
                    error=msg,
                )
            )

    retry_idx = [
        i
        for i, c in enumerate(checks)
        if not c.found
        and not _tnpa_check_is_route_blocked(c)
        and (
            (c.status or "").startswith("пропущено")
            or c.status == "ошибка проверки"
        )
    ]
    if retry_idx:
        # Хватает на полный timeout×число промахов (раньше 180 с — мало для 7 refs).
        retry_sec = min(
            tnpa_budget_max_sec(),
            max(90.0, (float(tnpa_timeout_sec()) + 15.0) * len(retry_idx)),
        )
        retry_deadline = time.monotonic() + retry_sec
        log.warning("TNPA retry %s refs after timeouts/budget (%.0fs)", len(retry_idx), retry_sec)
        for i in retry_idx:
            item = items[i]
            again = lookup_one_tnpa(
                str(item.get("kind") or ""),
                str(item.get("ref") or ""),
                client=shared_cli,
                today=today,
                deadline=retry_deadline,
            )
            if again.found or not (again.status or "").startswith("пропущено"):
                checks[i] = again

    found = sum(1 for c in checks if c.found)
    skipped = sum(
        1
        for c in checks
        if not c.found
        and ((c.status or "").startswith("пропущено") or c.status == "ошибка проверки")
    )
    log.info(
        "TNPA batch: %s refs, found %s, miss/skip %s in %.1fs",
        len(checks),
        found,
        skipped,
        time.monotonic() - t_batch,
    )
    return list(refs or []), checks


_client: TnpaClient | None = None
_client_lock = threading.Lock()


def reset_tnpa_client() -> None:
    global _client
    reset_tnpa_ssl()
    with _client_lock:
        _client = None


def _default_client() -> TnpaClient:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = TnpaClient()
        return _client


def test_tnpa_connection(base_url: str | None = None) -> tuple[bool, str]:
    cli = TnpaClient(base_url=base_url)
    try:
        rows = cli.search_docs("10704-91", per_page=3)
        if rows:
            return True, "Поиск на tnpa.by выполнен успешно"
        return True, "Сайт отвечает, документы по тестовому запросу не найдены"
    except Exception as e:
        return False, str(e)
