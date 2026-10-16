"""Fetching a link that came from outside (a download link in an email): https only, every hop checked, never an
address on this server, this network or a cloud's metadata service, redirects followed by hand and capped, and
the size capped. Used wherever the app follows a URL it did not choose itself."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

MAX_HOPS = 5


class Refused(ValueError):
    pass


def check_url(url: str) -> str:
    """The host, if the URL is https and every address the host resolves to is on the public internet."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise Refused("only https links are followed")
    if parts.username or parts.password:
        raise Refused("a link with a user name in it is not followed")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise Refused(f"{host} does not resolve") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        nat64 = address.version == 6 and (address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"))
        if not address.is_global or address.is_multicast or nat64:
            raise Refused(f"{host} points inside a private network")
    return host


def safe_get(url: str, *, max_bytes: int = 50 * 1024 * 1024, timeout: float = 60.0, client: httpx.Client | None = None) -> tuple[bytes, list[str]]:
    """The body at `url`, and every host visited on the way."""
    hosts: list[str] = []
    own = client is None
    http = client or httpx.Client(follow_redirects=False, timeout=timeout)
    try:
        for _ in range(MAX_HOPS + 1):
            hosts.append(check_url(url))
            with http.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise Refused("a redirect with nowhere to go")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise Refused(f"the file is over {max_bytes // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return b"".join(chunks), hosts
        raise Refused("too many redirects")
    finally:
        if own:
            http.close()
