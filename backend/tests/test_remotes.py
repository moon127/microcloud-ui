from pathlib import Path

import httpx
import pytest

from mcui.lxd.remotes import RemoteCatalog, parse_products

PRODUCTS = {
    "products": {
        "alpine:edge:amd64:default": {
            "aliases": "alpine/edge,alpine/edge/default",
            "os": "Alpine",
            "release": "edge",
            "release_title": "edge",
            "variant": "default",
            "arch": "amd64",
            "versions": {
                "20260101_01:00": {"items": {"a": {"ftype": "lxd.tar.xz"}}},
                "20260102_01:00": {
                    "items": {
                        "a": {"ftype": "lxd.tar.xz"},
                        "b": {"ftype": "squashfs"},
                        "c": {"ftype": "disk-kvm.img"},
                    }
                },
            },
        },
        "com.ubuntu.cloud:server:24.04:arm64": {
            "aliases": "24.04,noble,lts",
            "os": "ubuntu",
            "release": "noble",
            "release_title": "24.04 LTS",
            "variant": None,
            "arch": "arm64",
            "versions": {
                "1": {"items": {"m": {"ftype": "lxd.tar.xz"}, "r": {"ftype": "root.tar.xz"}}}
            },
        },
        "old:no-lxd": {
            "aliases": "10.04",
            "arch": "amd64",
            "versions": {"1": {"items": {"x": {"ftype": "tar.gz"}}}},
        },
        "nometa:vm": {
            "arch": "amd64",
            "versions": {"1": {"items": {"x": {"ftype": "disk-kvm.img"}}}},
        },
        "empty": {"versions": {}},
    }
}


def test_parse_products():
    items = parse_products(PRODUCTS)
    assert [(i.alias, i.container, i.vm, i.variant) for i in items] == [
        ("alpine/edge", True, True, "default"),
        ("24.04", True, False, "default"),
    ]
    assert items[0].version == "20260102_01:00" and items[0].aliases == [
        "alpine/edge",
        "alpine/edge/default",
    ]
    assert items[1].release_title == "24.04 LTS" and items[1].as_dict()["arch"] == "arm64"
    assert parse_products({}) == []


def test_alias_falls_back_to_product_key():
    doc = {
        "products": {
            "k": {
                "versions": {
                    "1": {"items": {"a": {"ftype": "lxd.tar.xz"}, "b": {"ftype": "squashfs"}}}
                }
            }
        }
    }
    (item,) = parse_products(doc)
    assert item.alias == "k" and item.aliases == [] and item.release_title == ""


INDEX = {
    "index": {
        "other": {"datatype": "content-download", "path": "x.json"},
        "images": {"datatype": "image-downloads", "path": "streams/v1/images.json"},
    }
}


async def test_catalog_fetches_and_caches():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("index.json"):
            return httpx.Response(200, json=INDEX)
        return httpx.Response(200, json=PRODUCTS)

    now = [0.0]
    cat = RemoteCatalog(transport=httpx.MockTransport(handler), clock=lambda: now[0])
    items = await cat.images("images")
    assert len(items) == 2
    assert calls == [
        "https://images.lxd.canonical.com/streams/v1/index.json",
        "https://images.lxd.canonical.com/streams/v1/images.json",
    ]
    await cat.images("images")
    assert len(calls) == 2  # cached
    now[0] = 7 * 3600
    await cat.images("images")
    assert len(calls) == 4
    with pytest.raises(KeyError):
        await cat.images("https://evil.example")
    await cat.aclose()


async def test_catalog_http_error():
    cat = RemoteCatalog(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        await cat.images("ubuntu")
    await cat.aclose()


RECORDED = Path(__file__).parent / "fixtures/recorded-6.10"


def test_parse_registry_images_recorded():
    """LXD 6.10's registry catalogue: every serial, each type apart (recorded on the VM)."""
    import json

    from mcui.lxd.remotes import parse_registry_images

    ubuntu = json.loads((RECORDED / "image-registry_ubuntu_images_slice.json").read_text())
    got = parse_registry_images(ubuntu)
    assert len(ubuntu) == 414 and len(got) == 4  # 24.04 and 26.04, amd64 and arm64
    noble = got[0]
    assert (noble.alias, noble.os, noble.release_title, noble.arch) == (
        "24.04",
        "ubuntu",
        "24.04 LTS",
        "amd64",
    )
    assert noble.container and noble.vm and noble.version == "20261017"
    assert "noble/amd64" in noble.aliases
    images = parse_registry_images(
        json.loads((RECORDED / "image-registry_images_images_slice.json").read_text())
    )
    arm = next(i for i in images if i.alias == "alpine/3.21/default/arm64")
    assert arm.container and not arm.vm  # no arm64 VM image there


def test_parse_registry_images_gaps():
    from mcui.lxd.remotes import parse_registry_images

    got = parse_registry_images(
        [
            {"type": "container", "aliases": None, "properties": {"os": "X", "release": "1"}},
            {"type": "container", "properties": {}},
        ]
    )
    assert [(g.alias, g.variant, g.release_title) for g in got] == [
        ("/", "default", ""),
        ("x/1", "default", "1"),
    ]
