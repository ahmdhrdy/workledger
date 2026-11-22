"""Minimal ONVIF Profile S media discovery; credentials never enter logs."""

import base64
import hashlib
import ipaddress
import secrets
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    ProxyHandler,
    Request,
    build_opener,
)
from xml.sax.saxutils import escape


def validate_host(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError(
            "Enter the camera's private IPv4 or IPv6 address, without http:// or a path"
        ) from exc
    if (
        not address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or address.is_link_local
    ):
        raise ValueError("Use the camera's private LAN address")
    return str(address)


def hostname(host):
    return f"[{host}]" if ":" in host else host


def rtsp_url(config):
    host = validate_host(config["host"])
    path = config.get("path", "")
    if not path.startswith("/") or any(c in path for c in "\r\n#") or len(path) > 1000:
        raise ValueError("RTSP path must begin with / and must not contain a URL or credentials")
    auth = ""
    if config.get("username"):
        auth = (
            quote(config["username"], safe="")
            + ":"
            + quote(config.get("password", ""), safe="")
            + "@"
        )
    return f"rtsp://{auth}{hostname(host)}:{int(config.get('rtsp_port', 554))}{path}"


def _same_camera(url, host):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != host
        or parsed.username
        or parsed.password
    ):
        raise ValueError("ONVIF returned a service outside the configured camera")
    return url


def soap(url, action, body, username, password):
    nonce = secrets.token_bytes(16)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    envelope = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
        <s:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{escape(username)}</wsse:Username>
        <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
        <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>
        <wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>
        <s:Body>{body}</s:Body></s:Envelope>"""
    credentials = HTTPPasswordMgrWithDefaultRealm()
    credentials.add_password(None, url, username, password)
    # LAN connections must not route through environment HTTP proxies.
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = build_opener(ProxyHandler({}), HTTPDigestAuthHandler(credentials), NoRedirect())
    request = Request(
        url,
        data=envelope.encode(),
        headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
        method="POST",
    )
    with opener.open(request, timeout=5) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000 or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Invalid ONVIF response")
    root = ET.fromstring(data)
    if any(node.tag.endswith("}Fault") for node in root.iter()):
        raise ValueError("ONVIF request rejected; check ONVIF access and camera clock")
    return root


def discover(config):
    host = validate_host(config["host"])
    origin = f"http://{hostname(host)}:{int(config.get('onvif_port', 80))}"
    user, password = config.get("username", ""), config.get("password", "")
    root = soap(
        origin + "/onvif/device_service",
        "http://www.onvif.org/ver10/device/wsdl/GetCapabilities",
        '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Category>Media</tds:Category></tds:GetCapabilities>',
        user,
        password,
    )
    media = next(
        (
            child.text
            for node in root.iter()
            if node.tag.endswith("}Media")
            for child in node
            if child.tag.endswith("}XAddr")
        ),
        None,
    )
    if not media:
        raise ValueError("Camera did not expose ONVIF Media; enable ONVIF or supply an RTSP path")
    media = _same_camera(media, host)
    root = soap(
        media,
        "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
        '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
        user,
        password,
    )
    profiles = [
        node for node in root.iter() if node.tag.endswith("}Profiles") and node.attrib.get("token")
    ]
    if not profiles:
        raise ValueError("No ONVIF media profiles found")

    def pixels(profile):
        width = next((int(n.text) for n in profile.iter() if n.tag.endswith("}Width")), 99999)
        height = next((int(n.text) for n in profile.iter() if n.tag.endswith("}Height")), 99999)
        return width * height

    profiles.sort(key=pixels)
    # Prefer a substream sufficient for face recognition, else highest available.
    minimum = 1280 * 720 if config.get("role") in {"entry", "exit"} else 640 * 360
    profile = next((p for p in profiles if pixels(p) >= minimum), profiles[-1])
    body = f"""<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{escape(profile.attrib["token"])}</trt:ProfileToken></trt:GetStreamUri>"""
    root = soap(media, "http://www.onvif.org/ver10/media/wsdl/GetStreamUri", body, user, password)
    uri = next((n.text for n in root.iter() if n.tag.endswith("}Uri")), "")
    parsed = urlsplit(uri)
    if parsed.scheme != "rtsp" or parsed.hostname not in {host, "0.0.0.0"}:
        raise ValueError("Camera returned an unsupported RTSP destination")
    result = dict(config)
    result["rtsp_port"] = parsed.port or 554
    result["path"] = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    result["profile"] = next((n.text for n in profile if n.tag.endswith("}Name")), "ONVIF")
    return result
