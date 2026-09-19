"""Bounded HTTP fetches pinned to validated public IPs, including redirects.

No ambient proxies; HTTPS retains original hostname verification and SNI.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


class FetchError(ValueError):
    pass


def public_target(url: str):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise FetchError('invalid_url')
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username is not None or parts.password is not None or port not in {80, 443}:
            raise FetchError('invalid_url')
        hostname = parts.hostname.encode('idna').decode('ascii')
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise FetchError('dns_failed')
        for info in addresses:
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global or address.is_multicast or address.is_unspecified or getattr(address, 'ipv4_mapped', None):
                raise FetchError('non_public_address')
        return parts, hostname, port, addresses[0][4][0]
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, FetchError):
            raise
        raise FetchError('invalid_url') from exc


def fetch_bytes(url: str, max_bytes: int = 4 * 1024 * 1024) -> tuple[bytes, str]:
    deadline = time.monotonic() + 30
    for _ in range(5):
        parts, hostname, port, address = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError('fetch_timeout')
        connection = http.client.HTTPConnection(hostname, port, timeout=min(10, remaining))
        raw_socket = None
        try:
            raw_socket = socket.create_connection((address, port), timeout=min(10, remaining))
            if parts.scheme == 'https':
                raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=hostname)
            connection.sock = raw_socket
            path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
            connection.request('GET', path, headers={
                'User-Agent': 'Knowpipe/1.0 (podcast transcript reader)',
                'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise FetchError('invalid_redirect')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError('http_error')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise FetchError('unsupported_encoding')
            length = response.getheader('Content-Length')
            if length and int(length) > max_bytes:
                raise FetchError('response_too_large')
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError('fetch_timeout')
                raw_socket.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError('response_too_large')
                chunks.append(chunk)
            return b''.join(chunks), response.getheader('Content-Type', '').split(';')[0].lower()
        except FetchError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError('fetch_failed') from exc
        finally:
            connection.close()
            if raw_socket is not None:
                raw_socket.close()
    raise FetchError('too_many_redirects')
