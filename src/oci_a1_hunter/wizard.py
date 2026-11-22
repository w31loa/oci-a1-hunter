"""Interactive setup: asks a few questions, does the OCI plumbing, writes config.txt."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

import oci
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from oci_a1_hunter import keys, notify
from oci_a1_hunter.cloud import Cloud
from oci_a1_hunter.config import (
    FREE_A1_MEMORY_GB,
    FREE_A1_OCPUS,
    FREE_STORAGE_GB,
    Config,
    save_config,
)
from oci_a1_hunter.i18n import t
from oci_a1_hunter.ui import choose, console, die, step

TOTAL_STEPS = 6
SHAPE_PRESETS = [(4, 24), (2, 12), (1, 6)]
CONSOLE_URL = "https://cloud.oracle.com"


# --------------------------------------------------------------------------------------
# 1. access
# --------------------------------------------------------------------------------------


def try_connect(cfg: Config, attempts: int = 7) -> Cloud | None:
    """Connect and make one cheap call. A freshly added API key can take a minute to work."""
    for n in range(attempts):
        try:
            with console.status(
                t("Connecting to Oracle Cloud...", "Подключаюсь к Oracle Cloud...")
            ):
                cloud = Cloud(cfg)
                cloud.availability_domains()
            console.print(f"[green]{t('Connected', 'Подключено')}: {cloud.region}[/]")
            return cloud
        except oci.exceptions.ServiceError as e:
            if e.status == 401 and n < attempts - 1:
                console.print(
                    t(
                        "[yellow]Key not accepted yet (new keys take up to a minute), retrying in 10s...[/]",
                        "[yellow]Ключ пока не принят (новый ключ активируется до минуты), повтор через 10 с...[/]",
                    )
                )
                time.sleep(10)
                continue
            console.print(f"[red]OCI: [{e.status} {e.code}] {e.message}[/]")
        except (oci.exceptions.ClientError, OSError, ValueError, KeyError) as e:
            console.print(f"[red]{type(e).__name__}: {e}[/]")
        return None
    return None


def read_snippet() -> dict[str, str]:
    console.print(
        t(
            "Paste the 'Configuration file preview' text here and press Enter on an empty line:",
            "Вставь сюда текст из 'Configuration file preview' и нажми Enter на пустой строке:",
        )
    )
    lines: list[str] = []
    while True:
        line = console.input("")
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
        found = keys.parse_console_snippet("\n".join(lines))
        if {"user", "tenancy", "region", "fingerprint"} <= found.keys():
            break
    return keys.parse_console_snippet("\n".join(lines))


def setup_access(cfg: Config) -> Cloud:
    step(1, TOTAL_STEPS, t("Access to Oracle Cloud", "Доступ к Oracle Cloud"))

    oci_config = Path(cfg.oci_config_file).expanduser()
    if oci_config.exists() and Confirm.ask(
        t(f"Found {oci_config}. Use it?", f"Нашёл {oci_config}. Использовать его?"), default=True
    ):
        cloud = try_connect(cfg)
        if cloud:
            return cloud

    idx = choose(
        t("How do you want to set up the API key?", "Как настроим API-ключ?"),
        [
            t("Create a key for me (recommended)", "Создай ключ сам (рекомендую)"),
            t(
                "I downloaded a private key (.pem) from Oracle",
                "Я скачал приватный ключ (.pem) в Oracle",
            ),
        ],
    )
    if idx == 0:
        key_path, pem, fingerprint = keys.ensure_api_key()
        console.print(
            t(
                f"\n1. Open {CONSOLE_URL} and sign in.\n"
                "2. Profile icon (top right) -> [bold]My profile[/] -> [bold]API keys[/] -> [bold]Add API key[/].\n"
                "3. Choose [bold]Paste a public key[/], paste the key below, click [bold]Add[/].",
                f"\n1. Открой {CONSOLE_URL} и войди.\n"
                "2. Иконка профиля (справа сверху) -> [bold]My profile[/] -> [bold]API keys[/] -> [bold]Add API key[/].\n"
                "3. Выбери [bold]Paste a public key[/], вставь ключ ниже, нажми [bold]Add[/].",
            )
        )
        console.print(Panel(pem.strip(), title=t("public key", "публичный ключ"), expand=False))
        console.print(
            f"[dim]{t('Private key saved to', 'Приватный ключ сохранён в')} {key_path}[/]"
        )
    else:
        while True:
            raw = Prompt.ask(t("Path to the .pem file", "Путь к .pem файлу")).strip().strip("'\"")
            key_path = Path(raw).expanduser()
            try:
                _, fingerprint = keys.load_api_key(key_path)
                break
            except (OSError, ValueError, TypeError) as e:
                console.print(f"[red]{e}[/]")
        console.print(
            t(
                "In the Oracle console, after adding the key you saw 'Configuration file preview'.\n"
                "(Missed it? API keys -> ⋮ next to the key -> View configuration file.)",
                "В консоли Oracle после добавления ключа было окно 'Configuration file preview'.\n"
                "(Закрыл? API keys -> ⋮ рядом с ключом -> View configuration file.)",
            )
        )

    while True:
        console.print(
            t(
                "\n4. After clicking Add, Oracle shows [bold]Configuration file preview[/]. Copy all of it.",
                "\n4. После Add Oracle покажет [bold]Configuration file preview[/]. Скопируй его целиком.",
            )
        )
        values = read_snippet()
        missing = {"user", "tenancy", "region", "fingerprint"} - values.keys()
        if missing:
            console.print(f"[red]{t('Missing', 'Не хватает')}: {', '.join(sorted(missing))}[/]")
            continue
        if values["fingerprint"] != fingerprint and not Confirm.ask(
            t(
                f"[yellow]Fingerprint {values['fingerprint']} does not match this key ({fingerprint}). "
                "Did you add a different key? Continue anyway?[/]",
                f"[yellow]Fingerprint {values['fingerprint']} не совпадает с этим ключом ({fingerprint}). "
                "Добавлен другой ключ? Всё равно продолжить?[/]",
            ),
            default=False,
        ):
            continue
        cfg.tenancy, cfg.user = values["tenancy"], values["user"]
        cfg.fingerprint, cfg.region = values["fingerprint"], values["region"]
        cfg.key_file = str(key_path)
        cloud = try_connect(cfg)
        if cloud:
            return cloud
        console.print(t("Let's try again.", "Давай ещё раз."))


# --------------------------------------------------------------------------------------
# 2-6
# --------------------------------------------------------------------------------------


def setup_region(cfg: Config, cloud: Cloud) -> Cloud:
    step(2, TOTAL_STEPS, t("Location (region)", "Локация (регион)"))
    regions = cloud.regions()
    home = next((name for name, is_home in regions if is_home), cloud.region)
    if len(regions) <= 1:
        console.print(
            t(
                f"Region: [bold]{home}[/] (your home region; Always Free servers can only be created here).",
                f"Регион: [bold]{home}[/] (домашний; бесплатные серверы создаются только в нём).",
            )
        )
        chosen = home
    else:
        labels = [f"{n}{t(' (home)', ' (домашний)') if is_home else ''}" for n, is_home in regions]
        default = next(i for i, (n, _) in enumerate(regions, 1) if n == home)
        chosen = regions[choose(t("Region", "Регион"), labels, default)][0]
        if chosen != home:
            console.print(
                t(
                    "[yellow]Always Free resources only exist in the home region; elsewhere you pay.[/]",
                    "[yellow]Бесплатные ресурсы есть только в домашнем регионе, в других платно.[/]",
                )
            )
    if chosen != cloud.region:
        cfg.region = chosen
        cloud = Cloud(cfg)
    else:
        cfg.region = cfg.region or chosen
    return cloud


def setup_server(cfg: Config, cloud: Cloud) -> None:
    step(3, TOTAL_STEPS, t("Server", "Сервер"))
    with console.status(t("Checking what you already use...", "Смотрю, что уже занято...")):
        usage = cloud.usage(cloud.availability_domains())
    free_cpu = max(0.0, FREE_A1_OCPUS - usage.a1_ocpus)
    free_mem = max(0.0, FREE_A1_MEMORY_GB - usage.a1_memory_gb)
    free_disk = max(0, FREE_STORAGE_GB - usage.storage_gb)
    console.print(
        t(
            f"Free quota left: [bold]{free_cpu:g} CPU, {free_mem:g} GB RAM, {free_disk} GB disk[/]",
            f"Осталось бесплатно: [bold]{free_cpu:g} CPU, {free_mem:g} ГБ RAM, {free_disk} ГБ диска[/]",
        )
    )
    if free_cpu == 0:
        console.print(
            t(
                "[yellow]Your free A1 quota is used up, anything you create will be paid.[/]",
                "[yellow]Бесплатная квота A1 занята, новый сервер будет платным.[/]",
            )
        )

    presets = [p for p in SHAPE_PRESETS if p[0] <= free_cpu and p[1] <= free_mem] or SHAPE_PRESETS
    labels = [
        f"{c} CPU / {m} {t('GB RAM', 'ГБ RAM')}"
        + (t("  (max free)", "  (максимум бесплатно)") if c == 4 else "")
        for c, m in presets
    ]
    labels.append(t("Custom", "Свой вариант"))
    idx = choose(t("Size", "Размер"), labels)
    if idx < len(presets):
        cfg.ocpus, cfg.memory_gb = presets[idx]
    else:
        cfg.ocpus = IntPrompt.ask("CPU (1-4)", default=int(free_cpu) or 1)
        cfg.memory_gb = IntPrompt.ask(t("RAM GB", "RAM ГБ"), default=int(cfg.ocpus * 6))

    disk_default = max(50, min(200, free_disk))
    while True:
        cfg.boot_volume_gb = IntPrompt.ask(
            t("Disk GB (50-200)", "Диск ГБ (50-200)"), default=disk_default
        )
        if cfg.boot_volume_gb >= 50:
            break
        console.print(t("[red]Minimum is 50 GB[/]", "[red]Минимум 50 ГБ[/]"))
    if cfg.boot_volume_gb > free_disk:
        console.print(
            t(
                f"[yellow]Only {free_disk} GB of free storage left, a bigger disk will be paid.[/]",
                f"[yellow]Бесплатно осталось {free_disk} ГБ, диск больше будет платным.[/]",
            )
        )
    cfg.display_name = Prompt.ask(t("Server name", "Имя сервера"), default=cfg.display_name)


def setup_os(cfg: Config, cloud: Cloud) -> None:
    step(4, TOTAL_STEPS, t("Operating system", "Операционная система"))
    with console.status(t("Loading images...", "Загружаю образы...")):
        images = cloud.images()
    families: dict[tuple[str, str], Any] = {}
    for img in images:
        if "GPU" not in (img.display_name or ""):
            families.setdefault((img.operating_system, img.operating_system_version), img)
    if not families:
        console.print(
            t("No images found, keeping Ubuntu 24.04.", "Образы не найдены, оставляю Ubuntu 24.04.")
        )
        return
    options = sorted(
        families, key=lambda k: (k[0] != "Canonical Ubuntu", k[0], [-ord(c) for c in k[1]])
    )
    default = next((n for n, k in enumerate(options, 1) if k == ("Canonical Ubuntu", "24.04")), 1)
    idx = choose(t("OS", "ОС"), [f"{o} {v}" for o, v in options], default)
    os_name, version = options[idx]
    cfg.image = f"{os_name} {version}"


def setup_network_and_ssh(cfg: Config, cloud: Cloud) -> None:
    step(5, TOTAL_STEPS, t("Network and SSH", "Сеть и SSH"))
    with console.status(t("Looking for a network...", "Ищу сеть...")):
        public = [s for s in cloud.subnets() if s.public]
    if len(public) == 1:
        cfg.subnet_id = public[0].id
        console.print(f"{t('Network', 'Сеть')}: {public[0].vcn_name} / {public[0].name}")
    elif public:
        idx = choose(t("Network", "Сеть"), [f"{s.vcn_name} / {s.name} ({s.cidr})" for s in public])
        cfg.subnet_id = public[idx].id
    else:
        console.print(
            t(
                "You have no public network yet. I'll create one (free): VCN, internet gateway, public subnet.",
                "Публичной сети пока нет. Создам её (бесплатно): VCN, интернет-шлюз, публичная подсеть.",
            )
        )
        if not Confirm.ask(t("Create it?", "Создать?"), default=True):
            raise die(t("A public subnet is required.", "Без публичной подсети сервер не создать."))
        with console.status(t("Creating network...", "Создаю сеть...")):
            subnet = cloud.create_public_network()
        cfg.subnet_id = subnet.id
        console.print(f"[green]{t('Created', 'Создано')}: {subnet.vcn_name} / {subnet.name}[/]")

    existing = keys.default_ssh_public_key()
    if existing and Confirm.ask(
        t(f"Use SSH key {existing}?", f"Использовать SSH-ключ {existing}?"), default=True
    ):
        cfg.ssh_public_key_file = str(existing)
    else:
        pub = keys.ensure_ssh_key()
        cfg.ssh_public_key_file = str(pub)
        console.print(
            t(
                f"[green]SSH key created: {pub.with_suffix('')} (keep it, it's your way into the server)[/]",
                f"[green]SSH-ключ создан: {pub.with_suffix('')} (сохрани его, это вход на сервер)[/]",
            )
        )


def setup_notifications(cfg: Config) -> None:
    step(6, TOTAL_STEPS, t("Notifications", "Уведомления"))
    idx = choose(
        t("How should I tell you when the server is caught?", "Как сообщить, когда сервер пойман?"),
        [
            t("Only on this computer", "Только на этом компьютере"),
            t("Telegram bot", "Telegram-бот"),
            t(
                "ntfy app on the phone (no sign-up)",
                "Приложение ntfy на телефоне (без регистрации)",
            ),
        ],
    )
    cfg.desktop_notify = True
    if idx == 1:
        console.print(
            t(
                "Create a bot with @BotFather, send it any message, get your id from @userinfobot.",
                "Создай бота в @BotFather, напиши ему что угодно, свой id узнай у @userinfobot.",
            )
        )
        while True:
            cfg.telegram_bot_token = Prompt.ask(t("Bot token", "Токен бота")).strip()
            cfg.telegram_chat_id = Prompt.ask(t("Your chat id", "Твой chat id")).strip()
            try:
                notify.telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, "a1-hunter: test ✓")
                console.print(
                    t("[green]Test message sent.[/]", "[green]Тестовое сообщение отправлено.[/]")
                )
                break
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Telegram: {e}[/]")
                if not Confirm.ask(t("Try again?", "Попробовать ещё раз?"), default=True):
                    cfg.telegram_bot_token = cfg.telegram_chat_id = ""
                    break
    elif idx == 2:
        cfg.ntfy_topic = f"a1-hunter-{secrets.token_hex(8)}"
        console.print(
            t(
                f"Install the ntfy app and subscribe to topic [bold]{cfg.ntfy_topic}[/]\n"
                f"(or open {cfg.ntfy_server}/{cfg.ntfy_topic} in a browser).",
                f"Установи приложение ntfy и подпишись на топик [bold]{cfg.ntfy_topic}[/]\n"
                f"(или открой {cfg.ntfy_server}/{cfg.ntfy_topic} в браузере).",
            )
        )
        Prompt.ask(t("Press Enter when subscribed", "Нажми Enter, когда подпишешься"), default="")
        try:
            notify.ntfy(cfg.ntfy_server, cfg.ntfy_topic, "a1-hunter", "test ✓")
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]ntfy: {e}[/]")


# --------------------------------------------------------------------------------------


def run_wizard(path: Path) -> bool:
    """Walk through setup and save to `path`. Returns True if the user wants to start hunting."""
    console.print(
        Panel(
            t(
                "I'll ask a few questions and set everything up. Ctrl+C to quit at any time.",
                "Задам несколько вопросов и всё настрою. Ctrl+C, чтобы выйти в любой момент.",
            ),
            title="a1-hunter",
            expand=False,
        )
    )
    cfg = Config()
    try:
        cloud = setup_access(cfg)
        cloud = setup_region(cfg, cloud)
        setup_server(cfg, cloud)
        setup_os(cfg, cloud)
        setup_network_and_ssh(cfg, cloud)
    except oci.exceptions.ServiceError as e:
        raise die(f"OCI: [{e.status} {e.code}] {e.message}") from e
    setup_notifications(cfg)

    save_config(cfg, path)
    console.print(
        t(
            f"\n[green]Saved to {path}[/]. Edit it any time, or run `a1-hunter init` again.",
            f"\n[green]Сохранено в {path}[/]. Его можно править руками или снова запустить `a1-hunter init`.",
        )
    )
    return Confirm.ask(t("Start hunting now?", "Начать охоту сейчас?"), default=True)


def write_template(path: Path) -> None:
    save_config(Config(), path)
