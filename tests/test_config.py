import pytest

from oci_a1_hunter import i18n
from oci_a1_hunter.config import (
    Config,
    ConfigError,
    dump_config,
    load_config,
    parse_image,
    parse_txt,
)


def test_defaults_without_file():
    cfg = load_config(None)
    assert cfg.ocpus == 4 and cfg.memory_gb == 24 and cfg.image == "Ubuntu 24.04"


def test_txt_format(tmp_path):
    p = tmp_path / "config.txt"
    p.write_text(
        "﻿# comment\n"
        "region = eu-frankfurt-1   # inline comment\n"
        "ocpus = 2\n"
        "memory_gb = 12,5\n"
        'image = "Oracle Linux 9"\n'
        "desktop_notify = нет\n"
        "availability_domains = AD-1, AD-2\n"
        "telegram_bot_token = 123:ABC#x\n",
        encoding="utf-8",
    )
    cfg = load_config(p, {"ocpus": 3, "boot_volume_gb": None})
    assert cfg.region == "eu-frankfurt-1"
    assert cfg.ocpus == 3  # CLI flag wins
    assert cfg.memory_gb == 12.5
    assert cfg.image == "Oracle Linux 9"
    assert cfg.desktop_notify is False
    assert cfg.availability_domains == ["AD-1", "AD-2"]
    assert cfg.telegram_bot_token == "123:ABC#x"  # '#' without a space is not a comment
    assert cfg.boot_volume_gb == 200


def test_toml_still_supported(tmp_path):
    p = tmp_path / "hunter.toml"
    p.write_text('ocpus = 2\navailability_domains = ["AD-1"]\n')
    assert load_config(p).availability_domains == ["AD-1"]


@pytest.mark.parametrize(
    "text, msg",
    [
        ("ocpu = 4", "ocpu"),
        ("ocpus = four", "ocpus"),
        ("desktop_notify = maybe", "desktop_notify"),
        ("just text", "1"),
    ],
)
def test_txt_errors(text, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_txt(text)


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_dump_is_a_loadable_template(lang, monkeypatch):
    monkeypatch.setattr(i18n, "LANG", lang)
    cfg = Config(
        tenancy="ocid1.tenancy.x",
        ocpus=2,
        jitter=0.25,
        availability_domains=["A", "B"],
        desktop_notify=False,
        telegram_bot_token="t:1",
    )
    text = dump_config(cfg)
    assert Config(**parse_txt(text)) == cfg


def test_env_vars_for_notifications(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    cfg = Config()
    assert cfg.telegram_bot_token == "t" and cfg.telegram_chat_id == "c"


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("Ubuntu 24.04", ("Canonical Ubuntu", "24.04")),
        ("ubuntu   22.04", ("Canonical Ubuntu", "22.04")),
        ("Canonical Ubuntu 22.04 Minimal", ("Canonical Ubuntu", "22.04 Minimal")),
        ("Oracle Linux 9", ("Oracle Linux", "9")),
        ("oracle linux", ("Oracle Linux", None)),
    ],
)
def test_parse_image(spec, expected):
    assert parse_image(spec) == expected


@pytest.mark.parametrize(
    "kw, msg",
    [
        ({"boot_volume_gb": 10}, "boot_volume_gb"),
        ({"interval": 0}, "interval"),
        ({"tenancy": "x"}, "tenancy"),
    ],
)
def test_validate(kw, msg):
    with pytest.raises(ConfigError, match=msg):
        Config(**kw).validate()
