"""Settings: a plain `config.txt` (key = value) or TOML, merged with CLI flags."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from oci_a1_hunter.i18n import t

APP_NAME = "oci-a1-hunter"
CONFIG_NAME = "config.txt"
USER_CONFIG_DIR = Path.home() / ".config" / APP_NAME

# Always Free limits for Ampere A1 and block storage (per tenancy).
FREE_A1_OCPUS = 4
FREE_A1_MEMORY_GB = 24
FREE_STORAGE_GB = 200

OS_ALIASES = {
    "ubuntu": "Canonical Ubuntu",
    "canonical ubuntu": "Canonical Ubuntu",
    "oracle linux": "Oracle Linux",
    "oraclelinux": "Oracle Linux",
    "oracle": "Oracle Linux",
    "ol": "Oracle Linux",
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # --- OCI access (either these five fields, or an existing ~/.oci/config) ---
    tenancy: str = ""
    user: str = ""
    fingerprint: str = ""
    key_file: str = ""
    region: str = ""
    oci_config_file: str = "~/.oci/config"
    oci_profile: str = "DEFAULT"
    compartment_id: str = ""

    # --- server ---
    ocpus: float = 4
    memory_gb: float = 24
    boot_volume_gb: int = 200
    image: str = "Ubuntu 24.04"
    display_name: str = "a1-free"
    ssh_public_key_file: str = ""  # empty = ~/.ssh/id_ed25519.pub, or generate one

    # --- advanced ---
    availability_domains: list[str] = field(default_factory=list)  # empty = all
    subnet_id: str = ""  # empty = first public subnet, created if missing
    image_id: str = ""  # exact image OCID, overrides `image`
    shape: str = "VM.Standard.A1.Flex"
    assign_public_ip: bool = True

    # --- retry ---
    interval: float = 60
    jitter: float = 0.2
    max_backoff: float = 600

    # --- notifications ---
    desktop_notify: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    output_file: str = "instance.json"

    def __post_init__(self) -> None:
        self.telegram_bot_token = self.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = self.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.ntfy_topic = self.ntfy_topic or os.getenv("NTFY_TOPIC", "")

    def validate(self) -> None:
        if not self.image_id and not self.image:
            raise ConfigError(
                t("Set `image` (e.g. Ubuntu 24.04).", "Укажи `image` (например Ubuntu 24.04).")
            )
        if self.ocpus <= 0 or self.memory_gb <= 0:
            raise ConfigError(
                t("ocpus and memory_gb must be positive.", "ocpus и memory_gb должны быть > 0.")
            )
        if not 50 <= self.boot_volume_gb <= 32768:
            raise ConfigError(
                t("boot_volume_gb must be 50..32768.", "boot_volume_gb должен быть от 50 до 32768.")
            )
        if self.interval < 1:
            raise ConfigError(
                t(
                    "interval must be at least 1 second.",
                    "interval должен быть не меньше 1 секунды.",
                )
            )
        if not 0 <= self.jitter < 1:
            raise ConfigError(t("jitter must be in [0, 1).", "jitter должен быть от 0 до 1."))
        auth = [self.tenancy, self.user, self.fingerprint, self.key_file]
        if any(auth) and not all(auth):
            raise ConfigError(
                t(
                    "Fill all of tenancy, user, fingerprint, key_file (or none to use ~/.oci/config).",
                    "Заполни все поля tenancy, user, fingerprint, key_file (или ни одного, тогда возьмётся ~/.oci/config).",
                )
            )

    @property
    def has_inline_auth(self) -> bool:
        return bool(self.tenancy and self.user and self.fingerprint and self.key_file)

    def image_spec(self) -> tuple[str, str | None]:
        return parse_image(self.image)


def parse_image(spec: str) -> tuple[str, str | None]:
    """'Ubuntu 24.04' -> ('Canonical Ubuntu', '24.04'); 'Oracle Linux' -> ('Oracle Linux', None)."""
    spec = " ".join(spec.split())
    m = re.match(r"^(.+?)\s+(\d.*)$", spec)
    name, version = (m.group(1), m.group(2)) if m else (spec, None)
    return OS_ALIASES.get(name.lower(), name), version


# --------------------------------------------------------------------------------------
# where config files live
# --------------------------------------------------------------------------------------


def app_dir() -> Path:
    """Directory of the standalone binary, or the current directory when run from Python."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def config_candidates() -> list[Path]:
    paths = [Path.cwd() / CONFIG_NAME, app_dir() / CONFIG_NAME, USER_CONFIG_DIR / CONFIG_NAME]
    paths += [Path.cwd() / "hunter.toml", USER_CONFIG_DIR / "config.toml"]
    seen: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen


def find_config(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.exists():
            raise ConfigError(
                t(f"Config file not found: {explicit}", f"Файл настроек не найден: {explicit}")
            )
        return explicit
    return next((p for p in config_candidates() if p.exists()), None)


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------

TRUE = {"true", "yes", "y", "on", "1", "да", "д"}
FALSE = {"false", "no", "n", "off", "0", "нет", "н", ""}
FIELD_TYPES = {f.name: f.type for f in fields(Config)}


def _convert(key: str, raw: str) -> Any:
    kind = FIELD_TYPES[key]
    if kind == "bool":
        v = raw.lower()
        if v in TRUE:
            return True
        if v in FALSE:
            return False
        raise ConfigError(
            t(f"{key}: expected yes/no, got {raw!r}", f"{key}: нужно да/нет, а не {raw!r}")
        )
    if kind in ("int", "float"):
        try:
            num = float(raw.replace(",", "."))
        except ValueError:
            raise ConfigError(
                t(f"{key}: not a number: {raw!r}", f"{key}: не число: {raw!r}")
            ) from None
        return int(num) if kind == "int" else num
    if kind == "list[str]":
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def parse_txt(text: str, source: str = "config.txt") -> dict[str, Any]:
    data: dict[str, Any] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ConfigError(
                t(
                    f"{source}:{n}: expected `key = value`",
                    f"{source}:{n}: ожидается `ключ = значение`",
                )
            )
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in FIELD_TYPES:
            raise ConfigError(
                t(
                    f"{source}:{n}: unknown setting `{key}`",
                    f"{source}:{n}: неизвестная настройка `{key}`",
                )
            )
        data[key] = _convert(key, value)
    return data


def load_config(path: Path | None, overrides: dict[str, Any] | None = None) -> Config:
    data: dict[str, Any] = {}
    if path is not None:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".toml":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{path}: {e}") from e
            unknown = set(data) - set(FIELD_TYPES)
            if unknown:
                raise ConfigError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
        else:
            data = parse_txt(text, path.name)
    data.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return Config(**data)


# --------------------------------------------------------------------------------------
# writing (the same file doubles as a commented template)
# --------------------------------------------------------------------------------------

SECTIONS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "Oracle Cloud access",
        "Доступ к Oracle Cloud",
        [
            (
                "",
                "Profile (top right) -> My profile -> API keys -> Add API key.",
                "Профиль (справа сверху) -> My profile -> API keys -> Add API key.",
            ),
            (
                "",
                "Either generate a key pair there and download the private key, or run `a1-hunter init`",
                "Можно сгенерировать ключ там и скачать приватный ключ, или запусти `a1-hunter init`,",
            ),
            (
                "",
                "which makes the key for you. Then copy the values from 'Configuration file preview'.",
                "он сделает ключ сам. Потом скопируй значения из 'Configuration file preview'.",
            ),
            (
                "",
                "Leave these empty to use an existing ~/.oci/config.",
                "Оставь пустыми, если уже настроен ~/.oci/config.",
            ),
            ("tenancy", "", ""),
            ("user", "", ""),
            ("fingerprint", "", ""),
            ("key_file", "path to the private key (.pem)", "путь к приватному ключу (.pem)"),
            (
                "region",
                "region (location), e.g. eu-frankfurt-1. Always Free works only in your home region",
                "регион (локация), например eu-frankfurt-1. Always Free работает только в домашнем регионе",
            ),
        ],
    ),
    (
        "Server",
        "Сервер",
        [
            ("ocpus", "CPU cores. Free: up to 4 in total", "ядра CPU. Бесплатно: до 4 суммарно"),
            (
                "memory_gb",
                "RAM in GB. Free: up to 24 in total",
                "память в ГБ. Бесплатно: до 24 суммарно",
            ),
            (
                "boot_volume_gb",
                "disk in GB (50..200). Free: 200 in total across all disks",
                "диск в ГБ (50..200). Бесплатно: 200 на все диски",
            ),
            (
                "image",
                "OS: Ubuntu 24.04, Ubuntu 22.04, Oracle Linux 9, Oracle Linux 8",
                "ОС: Ubuntu 24.04, Ubuntu 22.04, Oracle Linux 9, Oracle Linux 8",
            ),
            ("display_name", "server name", "имя сервера"),
            (
                "ssh_public_key_file",
                "SSH public key; empty = ~/.ssh/id_ed25519.pub or create a new one",
                "публичный SSH-ключ; пусто = ~/.ssh/id_ed25519.pub или создать новый",
            ),
        ],
    ),
    (
        "Notifications (optional)",
        "Уведомления (необязательно)",
        [
            (
                "desktop_notify",
                "notification on this computer (yes/no)",
                "уведомление на этом компьютере (да/нет)",
            ),
            ("telegram_bot_token", "bot token from @BotFather", "токен бота от @BotFather"),
            (
                "telegram_chat_id",
                "your chat id (ask @userinfobot)",
                "твой chat id (спроси у @userinfobot)",
            ),
            (
                "ntfy_topic",
                "ntfy.sh topic, subscribe to it in the ntfy app",
                "топик ntfy.sh, подпишись на него в приложении ntfy",
            ),
            ("ntfy_server", "", ""),
        ],
    ),
    (
        "How often to try",
        "Как часто пробовать",
        [
            ("interval", "seconds between attempts", "секунд между попытками"),
            (
                "jitter",
                "random spread of the interval, 0.2 = +/-20%",
                "случайный разброс интервала, 0.2 = ±20%",
            ),
            (
                "max_backoff",
                "max pause in seconds when Oracle says 'too many requests'",
                "макс. пауза в секундах, если Oracle ответит 'too many requests'",
            ),
        ],
    ),
    (
        "Advanced (usually leave as is)",
        "Дополнительно (обычно не трогать)",
        [
            (
                "availability_domains",
                "comma-separated; empty = try all",
                "через запятую; пусто = пробовать все",
            ),
            (
                "subnet_id",
                "empty = use a public subnet, create one if there is none",
                "пусто = взять публичную подсеть, создать если её нет",
            ),
            (
                "image_id",
                "exact image OCID, overrides `image`",
                "точный OCID образа, заменяет `image`",
            ),
            ("shape", "", ""),
            ("assign_public_ip", "", ""),
            ("compartment_id", "empty = root compartment", "пусто = корневой compartment"),
            (
                "oci_config_file",
                "used when the access fields above are empty",
                "используется, если поля доступа пустые",
            ),
            ("oci_profile", "", ""),
            (
                "output_file",
                "where to save the created server details",
                "куда сохранить данные созданного сервера",
            ),
        ],
    ),
]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return t("yes", "да") if value else t("no", "нет")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def dump_config(cfg: Config) -> str:
    values = asdict(cfg)
    out = [
        t(
            "# a1-hunter settings. Format: key = value. Lines starting with # are comments.",
            "# Настройки a1-hunter. Формат: ключ = значение. Строки с # это комментарии.",
        ),
        t("# Then run:  a1-hunter", "# Потом запусти:  a1-hunter"),
    ]
    written = set()
    for en_title, ru_title, items in SECTIONS:
        out += ["", f"# === {t(en_title, ru_title)} ==="]
        for key, en_doc, ru_doc in items:
            doc = t(en_doc, ru_doc)
            if not key:
                out.append(f"# {doc}")
                continue
            if doc:
                out.append(f"# {doc}")
            out.append(f"{key} = {_fmt(values[key])}")
            written.add(key)
    missing = set(values) - written
    assert not missing, f"undocumented config keys: {missing}"
    return "\n".join(out) + "\n"


def save_config(cfg: Config, path: Path) -> None:
    """Write the config readable only by the owner: it may hold tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(dump_config(cfg))
