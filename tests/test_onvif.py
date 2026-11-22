import xml.etree.ElementTree as ET

import pytest

from workledger.vision.onvif import discover


def fixture_soap(url, action, body, username, password):
    if action.endswith("GetCapabilities"):
        return ET.fromstring(
            '<r xmlns:t="urn:test"><t:Media><t:XAddr>http://192.168.0.10/onvif/media</t:XAddr></t:Media></r>'
        )
    if action.endswith("GetProfiles"):
        return ET.fromstring(
            '<r xmlns:t="urn:test"><t:Profiles token="low"><t:Resolution><t:Width>640</t:Width><t:Height>360</t:Height></t:Resolution></t:Profiles><t:Profiles token="high"><t:Resolution><t:Width>1920</t:Width><t:Height>1080</t:Height></t:Resolution></t:Profiles></r>'
        )
    token = "high" if ">high<" in body else "low"
    return ET.fromstring(
        f'<r xmlns:t="urn:test"><t:Uri>rtsp://192.168.0.10:554/{token}</t:Uri></r>'
    )


def test_discovery_prefers_face_resolution_for_portals_and_substream_for_workplace(monkeypatch):
    monkeypatch.setattr("workledger.vision.onvif.soap", fixture_soap)
    config = {"host": "192.168.0.10", "username": "admin", "password": "private"}
    assert discover({**config, "role": "entry"})["path"] == "/high"
    assert discover({**config, "role": "exit"})["path"] == "/high"
    assert discover({**config, "role": "interior"})["path"] == "/low"


def test_onvif_service_cannot_redirect_credentials_outside_the_camera(monkeypatch):
    monkeypatch.setattr(
        "workledger.vision.onvif.soap",
        lambda *args: ET.fromstring(
            '<r xmlns:t="urn:test"><t:Media><t:XAddr>http://192.168.0.20/stolen</t:XAddr></t:Media></r>'
        ),
    )
    with pytest.raises(ValueError, match="outside"):
        discover({"host": "192.168.0.10", "username": "admin", "password": "private"})
