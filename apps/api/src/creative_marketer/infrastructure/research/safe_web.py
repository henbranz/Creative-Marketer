from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from creative_marketer.research.application import FetchedPage, FetchPolicyError
from creative_marketer.research.domain import FetchFailureCode, canonicalize_url

CONNECT_TIMEOUT_SECONDS = 5.0
OVERALL_TIMEOUT_SECONDS = 15.0
MAX_DECOMPRESSED_BYTES = 5 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "CreativeMarketerResearchBot/1.0"
ACCEPT = "text/html, text/plain;q=0.9, application/xhtml+xml;q=0.8"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class Resolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class PinnedTransport(Protocol):
    async def request(
        self, url: str, approved_ip: str, headers: dict[str, str]
    ) -> HttpResponse: ...


class SystemResolver:
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        try:
            values = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise FetchPolicyError(FetchFailureCode.DNS_FAILED, rejected=False) from error
        return tuple(sorted({str(item[4][0]) for item in values}))


def validate_public_addresses(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise FetchPolicyError(FetchFailureCode.DNS_FAILED, rejected=False)
    parsed: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise FetchPolicyError(FetchFailureCode.DNS_FAILED, rejected=False) from error
        # is_global excludes loopback, private, link-local, unspecified, multicast,
        # documentation/reserved ranges, and cloud metadata endpoints.
        if not address.is_global:
            raise FetchPolicyError(FetchFailureCode.UNSAFE_ADDRESS)
        parsed.append(str(address))
    return tuple(parsed)


class AsyncioPinnedTransport:
    """Small HTTP/1.1 client that connects only to a resolver-approved IP."""

    async def request(self, url: str, approved_ip: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlsplit(url)
        tls = parsed.scheme == "https"
        port = parsed.port or (443 if tls else 80)
        context = ssl.create_default_context() if tls else None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    approved_ip,
                    port,
                    ssl=context,
                    server_hostname=parsed.hostname if tls else None,
                ),
                CONNECT_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            raise FetchPolicyError(FetchFailureCode.CONNECT_TIMEOUT, rejected=False) from error
        except (OSError, ssl.SSLError) as error:
            raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port and parsed.port not in {80, 443}:
            host = f"{host}:{parsed.port}"
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}"]
        lines.extend(f"{key}: {value}" for key, value in headers.items())
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
        try:
            writer.write(request)
            await writer.drain()
            head = await self._read_headers(reader)
            status, response_headers = self._parse_headers(head)
            body = await self._read_body(reader, response_headers)
            return HttpResponse(status, response_headers, body)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _read_headers(self, reader: asyncio.StreamReader) -> bytes:
        try:
            value = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
        if len(value) > MAX_HEADER_BYTES:
            raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False)
        return value

    @staticmethod
    def _parse_headers(value: bytes) -> tuple[int, dict[str, str]]:
        try:
            lines = value.decode("iso-8859-1").split("\r\n")
            status = int(lines[0].split(" ", 2)[1])
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, child = line.split(":", 1)
                lowered = key.strip().lower()
                if lowered in headers and lowered not in {"set-cookie"}:
                    headers[lowered] += "," + child.strip()
                else:
                    headers[lowered] = child.strip()
            return status, headers
        except (ValueError, IndexError) as error:
            raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error

    async def _read_body(self, reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
        transfer = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer:
            raw = await self._read_chunked(reader)
        elif "content-length" in headers:
            try:
                length = int(headers["content-length"])
            except ValueError as error:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
            if length < 0 or length > MAX_DECOMPRESSED_BYTES * 2:
                raise FetchPolicyError(FetchFailureCode.RESPONSE_TOO_LARGE)
            try:
                raw = await reader.readexactly(length)
            except asyncio.IncompleteReadError as error:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
        else:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await reader.read(min(64 * 1024, MAX_DECOMPRESSED_BYTES * 2 + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_DECOMPRESSED_BYTES * 2:
                    raise FetchPolicyError(FetchFailureCode.RESPONSE_TOO_LARGE)
            raw = b"".join(chunks)
        if len(raw) > MAX_DECOMPRESSED_BYTES * 2:
            raise FetchPolicyError(FetchFailureCode.RESPONSE_TOO_LARGE)
        encoding = headers.get("content-encoding", "").lower()
        if encoding in {"gzip", "x-gzip"}:
            try:
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = inflater.decompress(raw, MAX_DECOMPRESSED_BYTES + 1)
                if len(body) <= MAX_DECOMPRESSED_BYTES:
                    body += inflater.flush(MAX_DECOMPRESSED_BYTES + 1 - len(body))
            except (ValueError, zlib.error) as error:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
        elif encoding in {"", "identity"}:
            body = raw
        else:
            raise FetchPolicyError(FetchFailureCode.UNSUPPORTED_MEDIA_TYPE)
        if len(body) > MAX_DECOMPRESSED_BYTES:
            raise FetchPolicyError(FetchFailureCode.RESPONSE_TOO_LARGE)
        return body

    async def _read_chunked(self, reader: asyncio.StreamReader) -> bytes:
        result = bytearray()
        while True:
            try:
                line = await reader.readuntil(b"\r\n")
                size = int(line.split(b";", 1)[0], 16)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as error:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
            if size == 0:
                await reader.readuntil(b"\r\n")
                break
            if len(result) + size > MAX_DECOMPRESSED_BYTES * 2:
                raise FetchPolicyError(FetchFailureCode.RESPONSE_TOO_LARGE)
            try:
                result.extend(await reader.readexactly(size))
                if await reader.readexactly(2) != b"\r\n":
                    raise ValueError
            except (asyncio.IncompleteReadError, ValueError) as error:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False) from error
        return bytes(result)


class SafeWebFetcher:
    def __init__(self, resolver: Resolver | None = None, transport: PinnedTransport | None = None):
        self._resolver = resolver or SystemResolver()
        self._transport = transport or AsyncioPinnedTransport()
        self._headers = {
            "User-Agent": USER_AGENT,
            "Accept": ACCEPT,
            "Accept-Language": "en",
            "Accept-Encoding": "gzip",
            "Connection": "close",
        }

    async def fetch(self, requested_url: str) -> FetchedPage:
        url = canonicalize_url(requested_url)
        try:
            return await asyncio.wait_for(self._fetch(url), OVERALL_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise FetchPolicyError(FetchFailureCode.OVERALL_TIMEOUT, rejected=False) from error

    async def _fetch(self, requested_url: str) -> FetchedPage:
        current = requested_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            await self._enforce_robots(current)
            response = await self._request(current)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise FetchPolicyError(FetchFailureCode.REDIRECT_INVALID)
                if redirect_count == MAX_REDIRECTS:
                    raise FetchPolicyError(FetchFailureCode.REDIRECT_LIMIT)
                try:
                    current = canonicalize_url(urljoin(current, location))
                except ValueError as error:
                    raise FetchPolicyError(FetchFailureCode.REDIRECT_INVALID) from error
                continue
            if response.status < 200 or response.status >= 300:
                raise FetchPolicyError(FetchFailureCode.HTTP_ERROR, rejected=False)
            declared_content_type = response.headers.get("content-type", "")[:100]
            media_type = declared_content_type.split(";", 1)[0].strip().lower()
            if media_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise FetchPolicyError(FetchFailureCode.UNSUPPORTED_MEDIA_TYPE)
            if b"\x00" in response.body[:8192]:
                raise FetchPolicyError(FetchFailureCode.BINARY_CONTENT)
            return FetchedPage(
                requested_url,
                current,
                response.status,
                declared_content_type,
                response.body,
                datetime.now(UTC),
            )
        raise FetchPolicyError(FetchFailureCode.REDIRECT_LIMIT)

    async def _request(self, url: str) -> HttpResponse:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = validate_public_addresses(
            await self._resolver.resolve(parsed.hostname or "", port)
        )
        # The transport receives an IP, never a hostname, closing the DNS rebinding gap.
        return await self._transport.request(url, addresses[0], dict(self._headers))

    async def _enforce_robots(self, url: str) -> None:
        parsed = urlsplit(url)
        robots_url = canonicalize_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        try:
            response = await self._request(robots_url)
        except FetchPolicyError as error:
            if error.code is FetchFailureCode.UNSAFE_ADDRESS:
                raise
            raise FetchPolicyError(FetchFailureCode.ROBOTS_UNAVAILABLE) from error
        if response.status == 404:
            return
        if response.status < 200 or response.status >= 300:
            raise FetchPolicyError(FetchFailureCode.ROBOTS_UNAVAILABLE)
        try:
            text = response.body.decode("utf-8", errors="replace")
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(text.splitlines())
        except Exception as error:
            raise FetchPolicyError(FetchFailureCode.ROBOTS_UNAVAILABLE) from error
        if not parser.can_fetch(USER_AGENT, url):
            raise FetchPolicyError(FetchFailureCode.ROBOTS_DENIED)
