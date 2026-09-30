"""Bounded public-page transport for surfaces without local network authority."""
import asyncio
import ipaddress
import socket
import re
from urllib.parse import urlsplit, urljoin

import httpx


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise ValueError('Only public HTTP or HTTPS pages are supported')
    if any(ord(char) < 33 for char in url) or len(url) > 2048:
        raise ValueError('Invalid page URL')
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        if '.' not in parts.hostname or parts.hostname.lower().endswith(('.localhost', '.local', '.internal')):
            raise ValueError('Local network pages are unavailable') from None
    else:
        if not address.is_global:
            raise ValueError('Local network pages are unavailable')
    return parts


async def public_address(host, port):
    rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [row[4][0] for row in rows]
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Local network pages are unavailable')
    return addresses[0]


async def fetch_public(url, timeout, max_length):
    from .web_fetch import _html_to_text, _looks_like_browser_shell, _fetch_user_agent, _LINK_RE
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            for _ in range(5):
                parts = public_url(url)
                address = await public_address(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
                # Pin the validated address; retain original Host and TLS identity.
                pinned = httpx.URL(url).copy_with(host=address)
                async with client.stream('GET', pinned, headers={'Host': parts.netloc, 'User-Agent': _fetch_user_agent()}, extensions={'sni_hostname': parts.hostname}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get('location', ''))
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '').lower()
                    if not any(kind in content_type for kind in ('text/', 'json', 'xml', 'pdf')):
                        raise ValueError('This page format cannot be read')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 2 * 1024 * 1024:
                            raise ValueError('This page is too large to read')
                        data.extend(chunk)
                    links = []
                    if 'pdf' in content_type:
                        from dan.personal.intake import parse_document
                        _, content = await asyncio.to_thread(parse_document, 'page.pdf', bytes(data))
                    else:
                        text = bytes(data).decode(response.encoding or 'utf-8', errors='replace')
                        if 'html' in content_type:
                            for target, label in _LINK_RE.findall(text):
                                if re.search(r'\b(menu|menus|dinner)\b', _html_to_text(label), re.I):
                                    links.append({'url': urljoin(url, target), 'title': _html_to_text(label)[:120]})
                            text = re.sub(r'<(nav|script|style|noscript)\b[^>]*>.*?</\1\s*>', '', text, flags=re.I | re.S)
                            content = _html_to_text(text)
                            content = re.sub(r'\n[ \t\r]*\n(?:[ \t\r]*\n)*', '\n\n', content)
                        else:
                            content = text
                    return {'url': url, 'content': content[:max_length], 'status_code': response.status_code,
                            'links': links[:20], 'content_type': content_type, 'fetch_via': 'http', 'cache_hit': False,
                            'content_requires_browser': _looks_like_browser_shell(content, content_type)}
    raise ValueError('This page redirects too many times')
