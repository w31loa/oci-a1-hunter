"""Command-line interface. Run with no arguments for the guided mode."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import oci
import typer
from rich.prompt import Confirm
from rich.table import Table

from oci_a1_hunter import __version__, keys
from oci_a1_hunter.cloud import Cloud
from oci_a1_hunter.config import (
    CONFIG_NAME,
    FREE_A1_MEMORY_GB,
    FREE_A1_OCPUS,
    FREE_STORAGE_GB,
    Config,
    ConfigError,
    app_dir,
    find_config,
    load_config,
)
from oci_a1_hunter.hunter import HuntAborted, HuntStats, Outcome, hunt
from oci_a1_hunter.i18n import set_lang, t
from oci_a1_hunter.notify import notify_all
from oci_a1_hunter.ui import choose, console, die, fmt_duration
from oci_a1_hunter.wizard import run_wizard, write_template

app = typer.Typer(
    add_completion=False,
    help="Keep asking Oracle Cloud for an Always Free Ampere A1 server until it says yes. "
    "Run without arguments for guided setup.",
)

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help=f"Settings file (default: ./{CONFIG_NAME})."),
]

OUTCOME_STYLE = {
    Outcome.CAPACITY: "yellow",
    Outcome.THROTTLED: "bold yellow",
    Outcome.TRANSIENT: "magenta",
    Outcome.FATAL: "bold red",
    None: "green",
}


def get_config(config: Path | None, **overrides: Any) -> tuple[Config, Path | None]:
    try:
        path = find_config(config)
        cfg = load_config(path, overrides)
        cfg.validate()
        return cfg, path
    except (ConfigError, TypeError) as e:
        raise die(str(e)) from e


def connect(cfg: Config) -> Cloud:
    try:
        return Cloud(cfg)
    except (oci.exceptions.ConfigFileNotFound, oci.exceptions.ProfileNotFound):
        raise die(
            t(
                "No Oracle Cloud access configured. Run `a1-hunter init`, or fill the access section of config.txt.",
                "Доступ к Oracle Cloud не настроен. Запусти `a1-hunter init` или заполни раздел доступа в config.txt.",
            )
        ) from None
    except (
        oci.exceptions.InvalidConfig,
        oci.exceptions.InvalidKeyFilePath,
        OSError,
        ValueError,
    ) as e:
        raise die(
            t(f"Invalid Oracle Cloud access settings: {e}", f"Неверные настройки доступа: {e}")
        ) from e


def oci_error(e: oci.exceptions.ServiceError) -> typer.Exit:
    return die(f"OCI: [{e.status} {e.code}] {e.message}")


def read_ssh_key(cfg: Config) -> str:
    if cfg.ssh_public_key_file:
        path = Path(cfg.ssh_public_key_file).expanduser()
    else:
        path = keys.default_ssh_public_key() or keys.ensure_ssh_key()
        cfg.ssh_public_key_file = str(path)
    if not path.exists():
        raise die(t(f"SSH public key not found: {path}", f"SSH-ключ не найден: {path}"))
    key = path.read_text().strip()
    if not key.startswith(("ssh-", "ecdsa-")):
        raise die(t(f"{path} is not an SSH public key", f"{path} не похож на публичный SSH-ключ"))
    return key


def ssh_command(cfg: Config, image: Any, ip: str) -> str:
    user = "ubuntu" if "ubuntu" in (image.operating_system or "").lower() else "opc"
    private = Path(cfg.ssh_public_key_file).expanduser().with_suffix("")
    default = {"id_ed25519", "id_ecdsa", "id_rsa"}
    ident = "" if private.name in default else f"-i {private} "
    return f"ssh {ident}{user}@{ip}"


def keep_awake() -> None:
    """On macOS, stop the Mac from idle-sleeping while this process lives."""
    if platform.system() == "Darwin" and shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])


class Logger:
    def __init__(self, log_file: Path | None):
        self.log_file = log_file

    def __call__(self, outcome: Outcome | None, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{ts}[/] [{OUTCOME_STYLE[outcome]}]{message}[/]")
        if self.log_file:
            label = outcome.value if outcome else "info"
            with self.log_file.open("a") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {label} {message}\n")


def live_sleep(stats: HuntStats):
    def _sleep(seconds: float) -> None:
        end = time.monotonic() + seconds
        with console.status("") as status:
            while (left := end - time.monotonic()) > 0:
                status.update(
                    t(
                        f"[dim]next try in {left:3.0f}s · {stats.attempts} attempts · {fmt_duration(stats.elapsed)}[/]",
                        f"[dim]следующая попытка через {left:3.0f} с · попыток: {stats.attempts} · {fmt_duration(stats.elapsed)}[/]",
                    )
                )
                time.sleep(min(1.0, left))

    return _sleep


# --------------------------------------------------------------------------------------
# hunt
# --------------------------------------------------------------------------------------


@app.command("hunt")
def hunt_cmd(
    config: ConfigOpt = None,
    ocpus: Annotated[float | None, typer.Option(help="CPU cores (free: up to 4 in total).")] = None,
    memory: Annotated[float | None, typer.Option(help="RAM in GB (free: 24).")] = None,
    boot_gb: Annotated[int | None, typer.Option(help="Disk in GB (free: 200).")] = None,
    image: Annotated[str | None, typer.Option(help="OS, e.g. 'Ubuntu 24.04'.")] = None,
    region: Annotated[str | None, typer.Option(help="Region, e.g. eu-frankfurt-1.")] = None,
    interval: Annotated[float | None, typer.Option(help="Seconds between attempts.")] = None,
    name: Annotated[str | None, typer.Option("--name", help="Server name.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Check everything, launch nothing.")] = False,
    skip_checks: Annotated[bool, typer.Option(help="Skip free-tier checks.")] = False,
    allow_paid: Annotated[
        bool, typer.Option(help="Launch even if over Always Free limits.")
    ] = False,
    awake: Annotated[
        bool, typer.Option("--keep-awake/--no-keep-awake", help="Prevent macOS sleep.")
    ] = True,
    log_file: Annotated[Path | None, typer.Option(help="Also append logs to this file.")] = None,
    max_attempts: Annotated[int | None, typer.Option(help="Give up after N attempts.")] = None,
) -> None:
    """Try to create the server until Oracle has capacity."""
    cfg, path = get_config(
        config,
        ocpus=ocpus,
        memory_gb=memory,
        boot_volume_gb=boot_gb,
        image=image,
        region=region,
        interval=interval,
        display_name=name,
    )
    if image:
        cfg.image_id = ""
    ssh_key = read_ssh_key(cfg)
    cloud = connect(cfg)

    try:
        ads = cfg.availability_domains or cloud.availability_domains()
        img = cloud.resolve_image()
        if not skip_checks:
            preflight(cloud, cfg, ads, allow_paid)
        ensure_subnet(cloud, cfg, create=not dry_run)
    except oci.exceptions.ServiceError as e:
        raise oci_error(e) from e
    except LookupError as e:
        raise die(str(e)) from e

    details = cloud.build_launch_details(img.id, ssh_key)
    print_plan(cfg, cloud, ads, img, path)
    if dry_run:
        console.print(
            t(
                "[green]Dry run: all checks passed, nothing launched.[/]",
                "[green]Проверка: всё в порядке, сервер не создавался.[/]",
            )
        )
        return

    if awake:
        keep_awake()

    log = Logger(log_file)
    stats = HuntStats()
    console.print(
        t(
            "[bold green]Hunting... Ctrl+C to stop.[/]",
            "[bold green]Охота началась... Ctrl+C, чтобы остановить.[/]",
        )
    )
    try:
        instance = hunt(
            cloud,
            details,
            ads,
            cfg.display_name,
            interval=cfg.interval,
            jitter=cfg.jitter,
            max_backoff=cfg.max_backoff,
            max_attempts=max_attempts,
            log=log,
            sleep=live_sleep(stats),
            stats=stats,
        )
    except HuntAborted as e:
        raise die(
            t(
                f"Stopped after {stats.attempts} attempts: {e}",
                f"Остановлено после {stats.attempts} попыток: {e}",
            )
        ) from e
    except KeyboardInterrupt:
        console.print(
            t(
                f"\n[yellow]Stopped after {stats.attempts} attempts ({fmt_duration(stats.elapsed)}).[/]",
                f"\n[yellow]Остановлено после {stats.attempts} попыток ({fmt_duration(stats.elapsed)}).[/]",
            )
        )
        raise typer.Exit(130) from None

    on_success(cloud, cfg, instance, img, stats)


def ensure_subnet(cloud: Cloud, cfg: Config, create: bool = True) -> None:
    if cfg.subnet_id:
        subnet = cloud.get_subnet(cfg.subnet_id)
        if cfg.assign_public_ip and subnet.prohibit_public_ip_on_vnic:
            raise die(
                t(
                    f"Subnet {subnet.display_name} is private.",
                    f"Подсеть {subnet.display_name} приватная, публичный IP не выдать.",
                )
            )
        return
    public = [s for s in cloud.subnets() if s.public]
    if public:
        cfg.subnet_id = public[0].id
        return
    if not create:
        console.print(
            t(
                "No public network yet, it will be created on start.",
                "Публичной сети пока нет, она будет создана при запуске.",
            )
        )
        return
    console.print(t("No public network found, creating one...", "Публичной сети нет, создаю..."))
    cfg.subnet_id = cloud.create_public_network().id


def preflight(cloud: Cloud, cfg: Config, ads: list[str], allow_paid: bool) -> None:
    existing = cloud.find_instance(cfg.display_name)
    if existing is not None:
        console.print(
            t(
                f"[green]Server '{cfg.display_name}' already exists ({existing.lifecycle_state}): {existing.id}[/]",
                f"[green]Сервер '{cfg.display_name}' уже есть ({existing.lifecycle_state}): {existing.id}[/]",
            )
        )
        raise typer.Exit(0)

    if cfg.shape != "VM.Standard.A1.Flex":
        return
    usage = cloud.usage(ads)
    problems = []
    if usage.a1_ocpus + cfg.ocpus > FREE_A1_OCPUS:
        problems.append(f"CPU: {usage.a1_ocpus:g} + {cfg.ocpus:g} > {FREE_A1_OCPUS}")
    if usage.a1_memory_gb + cfg.memory_gb > FREE_A1_MEMORY_GB:
        problems.append(f"RAM: {usage.a1_memory_gb:g} + {cfg.memory_gb:g} > {FREE_A1_MEMORY_GB} GB")
    if usage.storage_gb + cfg.boot_volume_gb > FREE_STORAGE_GB:
        problems.append(
            t("disk", "диск")
            + f": {usage.storage_gb} + {cfg.boot_volume_gb} > {FREE_STORAGE_GB} GB"
        )
    if problems:
        msg = (
            t("This exceeds the Always Free limits:", "Это больше бесплатного лимита:")
            + "\n  "
            + "\n  ".join(problems)
        )
        if not allow_paid:
            raise die(
                msg
                + t(
                    "\nFree something up or pick a smaller size (or pass --allow-paid).",
                    "\nОсвободи ресурсы или выбери размер меньше (или добавь --allow-paid).",
                )
            )
        console.print(f"[yellow]{msg}[/]")


def print_plan(cfg: Config, cloud: Cloud, ads: list[str], image: Any, path: Path | None) -> None:
    tbl = Table(show_header=False, box=None, padding=(0, 1))
    tbl.add_row(
        t("settings", "настройки"), str(path) if path else t("(defaults)", "(по умолчанию)")
    )
    tbl.add_row(t("region", "регион"), cloud.region)
    tbl.add_row("AD", ", ".join(a.split(":")[-1] for a in ads))
    tbl.add_row(
        t("server", "сервер"),
        f"{cfg.ocpus:g} CPU / {cfg.memory_gb:g} GB RAM / {cfg.boot_volume_gb} GB disk",
    )
    tbl.add_row(t("OS", "ОС"), image.display_name)
    tbl.add_row(t("name", "имя"), cfg.display_name)
    tbl.add_row(t("interval", "интервал"), f"{cfg.interval:g}s ±{cfg.jitter:.0%}")
    console.print(tbl)


def on_success(cloud: Cloud, cfg: Config, instance: Any, image: Any, stats: HuntStats) -> None:
    console.print(
        t(
            f"[bold green]Got it after {stats.attempts} attempts ({fmt_duration(stats.elapsed)})![/]",
            f"[bold green]Поймал! Попыток: {stats.attempts}, время: {fmt_duration(stats.elapsed)}[/]",
        )
    )
    out = Path(cfg.output_file)
    data = oci.util.to_dict(instance)
    out.write_text(json.dumps(data, indent=2, default=str))

    def warn(w: str) -> None:
        console.print(f"[yellow]{w}[/]")

    title = t("Oracle server caught!", "Сервер Oracle пойман!")
    notify_all(cfg, title, f"{cfg.ocpus:g} CPU / {cfg.memory_gb:g} GB, {cloud.region}", warn)

    ip = None
    try:
        with console.status(
            t("Waiting for the server to boot...", "Жду, пока сервер загрузится...")
        ):
            cloud.wait_running(instance.id)
            ip = cloud.public_ip(instance.id)
    except Exception as e:  # noqa: BLE001 - the instance exists, details are a bonus
        warn(t(f"Could not get the public IP yet: {e}", f"Пока не удалось получить IP: {e}"))

    if ip:
        data["public_ip"] = ip
        out.write_text(json.dumps(data, indent=2, default=str))
        cmd = ssh_command(cfg, image, ip)
        console.print(f"[bold]{cmd}[/]")
        notify_all(cfg, t("Server is running", "Сервер запущен"), cmd, warn)
    console.print(t(f"Details saved to {out}", f"Данные сохранены в {out}"))


# --------------------------------------------------------------------------------------
# status / init / template
# --------------------------------------------------------------------------------------


@app.command()
def status(config: ConfigOpt = None) -> None:
    """Show your A1 servers and how much of the free quota is used."""
    cfg, _ = get_config(config)
    cloud = connect(cfg)
    try:
        ads = cfg.availability_domains or cloud.availability_domains()
        usage = cloud.usage(ads)
    except oci.exceptions.ServiceError as e:
        raise oci_error(e) from e

    console.print(f"{t('Region', 'Регион')} [bold]{cloud.region}[/], AD: {', '.join(ads)}")
    if usage.instances:
        tbl = Table(
            t("name", "имя"), t("state", "статус"), "CPU", "GB", "AD", t("created", "создан")
        )
        for i in usage.instances:
            sc = i.shape_config
            tbl.add_row(
                i.display_name,
                i.lifecycle_state,
                f"{sc.ocpus:g}" if sc else "?",
                f"{sc.memory_in_gbs:g}" if sc else "?",
                i.availability_domain.split(":")[-1],
                i.time_created.strftime("%Y-%m-%d"),
            )
        console.print(tbl)
    else:
        console.print(t("No A1 servers.", "Серверов A1 нет."))

    def bar(used: float, limit: float) -> str:
        return f"[{'green' if used <= limit else 'red'}]{used:g} / {limit:g}[/]"

    console.print(
        t("Always Free used: ", "Занято из бесплатного: ")
        + f"CPU {bar(usage.a1_ocpus, FREE_A1_OCPUS)}, RAM {bar(usage.a1_memory_gb, FREE_A1_MEMORY_GB)}, "
        + t("disk ", "диск ")
        + bar(usage.storage_gb, FREE_STORAGE_GB)
    )


def default_config_path() -> Path:
    return app_dir() / CONFIG_NAME


@app.command()
def init(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Where to save settings.")
    ] = None,
) -> None:
    """Guided setup: answer a few questions, get a ready config.txt."""
    path = config or default_config_path()
    if path.exists() and not Confirm.ask(
        t(f"{path} exists. Overwrite?", f"{path} уже есть. Перезаписать?"), default=False
    ):
        raise typer.Exit(0)
    if run_wizard(path):
        hunt_cmd(config=path)


@app.command()
def template(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Where to write it.")
    ] = None,
) -> None:
    """Write a commented config.txt to fill in by hand."""
    path = config or default_config_path()
    if path.exists():
        raise die(t(f"{path} already exists.", f"{path} уже существует."))
    write_template(path)
    console.print(
        t(
            f"[green]Created {path}[/]. Fill it in, then run [bold]a1-hunter[/] again.",
            f"[green]Создан {path}[/]. Заполни его и снова запусти [bold]a1-hunter[/].",
        )
    )
    open_in_editor(path)


def open_in_editor(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", "-t", str(path)], check=False)
        elif platform.system() == "Windows":
            subprocess.Popen(["notepad", str(path)])
        elif shutil.which("xdg-open") and os.environ.get("DISPLAY"):
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def start() -> None:
    """No arguments: hunt if settings exist, otherwise help the user create them."""
    try:
        path = find_config(None)
    except ConfigError as e:
        raise die(str(e)) from e
    if path is not None:
        console.print(t(f"Using {path}", f"Настройки: {path}"))
        hunt_cmd(config=path)
        return

    idx = choose(
        t("No settings yet. How do you want to set things up?", "Настроек пока нет. Как настроим?"),
        [
            t("Answer questions here (recommended)", "Ответить на вопросы здесь (рекомендую)"),
            t(
                f"Create {CONFIG_NAME} and fill it in myself",
                f"Создать {CONFIG_NAME} и заполнить самому",
            ),
        ],
    )
    if idx == 0:
        init(config=None)
    else:
        template(config=None)


def _version(value: bool) -> None:
    if value:
        console.print(f"a1-hunter {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    lang: Annotated[
        str | None, typer.Option("--lang", help="Interface language: en or ru.")
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    if lang:
        set_lang(lang)
    if ctx.invoked_subcommand is None:
        start()


def run() -> None:
    """Entry point. Keeps the window open when the .exe was double-clicked on Windows."""
    try:
        app()
    except KeyboardInterrupt:
        console.print()
    finally:
        if getattr(sys, "frozen", False) and platform.system() == "Windows":
            input(t("\nPress Enter to close...", "\nНажми Enter, чтобы закрыть..."))
