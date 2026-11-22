# oci-a1-hunter

[Русский ниже](#по-русски)

Oracle Cloud's Always Free tier includes an Ampere A1 server with up to **4 CPU / 24 GB RAM / 200 GB disk**,
but popular regions are almost always "Out of host capacity". `a1-hunter` keeps asking until Oracle says yes,
then pings you and prints the `ssh` command.

You need an Oracle Cloud account and nothing else: the guided setup creates the API key, the network and an
SSH key for you.

## Quick start

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/w31loa/oci-a1-hunter/main/install.sh | sh
a1-hunter
```

**Windows**: download `a1-hunter-windows-x86_64.exe` from [Releases](https://github.com/w31loa/oci-a1-hunter/releases)
and double-click it.

On first run it asks how you want to set things up:

1. **Answer questions in the terminal.** It walks you through six steps: Oracle access (it generates the API key,
   you paste it into the Oracle console), region, server size, OS, network + SSH, notifications. Then it saves
   `config.txt` and starts hunting.
2. **Fill in `config.txt` yourself.** It writes a commented template next to the program and opens it in a text
   editor. Fill it in and run `a1-hunter` again.

After that, `a1-hunter` with no arguments just starts hunting with your `config.txt`.

```
$ a1-hunter
settings  ./config.txt
region    eu-frankfurt-1
server    4 CPU / 24 GB RAM / 200 GB disk
OS        Canonical-Ubuntu-24.04-aarch64-2026.10.30-0
Hunting... Ctrl+C to stop.
14:02:11 #1 AD-1 [500 InternalError] Out of host capacity.
14:03:08 #2 AD-1 [500 InternalError] Out of host capacity.
...
16:47:52 #171 AD-1 launched ocid1.instance.oc1.eu-frankfurt-1.an...
Got it after 171 attempts (2h45m)!
ssh ubuntu@203.0.113.17
```

## config.txt

Plain `key = value`, `#` for comments. The full template with explanations is in
[`config.example.txt`](config.example.txt). The parts you are most likely to touch:

```ini
# Oracle access: copy from the console's "Configuration file preview"
tenancy = ocid1.tenancy.oc1..
user = ocid1.user.oc1..
fingerprint = 12:34:...
key_file = ~/.oci/a1_hunter_api_key.pem
region = eu-frankfurt-1        # location; Always Free only works in your home region

# Server
ocpus = 4                      # free: up to 4 in total
memory_gb = 24                 # free: up to 24 in total
boot_volume_gb = 200           # free: 200 across all disks
image = Ubuntu 24.04           # or Ubuntu 22.04, Oracle Linux 9 ...
display_name = a1-free

# Notifications
telegram_bot_token =
telegram_chat_id =
ntfy_topic =
```

Leave the access fields empty to use an existing `~/.oci/config`. `subnet_id` can stay empty: a public subnet is
picked (or created) automatically. Settings are looked up in `./config.txt`, next to the program, then in
`~/.config/oci-a1-hunter/config.txt`.

## Commands

```bash
a1-hunter                 # hunt if config.txt exists, otherwise set up
a1-hunter init            # guided setup again
a1-hunter template        # write a blank commented config.txt
a1-hunter hunt --dry-run  # check everything, launch nothing
a1-hunter status          # your A1 servers and Always Free usage
```

Flags for `hunt` override `config.txt`: `--ocpus 2 --memory 12`, `--boot-gb 100`, `--image "Oracle Linux 9"`,
`--region`, `--interval 45`, `--name`, `--log-file hunt.log`, `--max-attempts 500`, `--allow-paid`,
`--skip-checks`, `--no-keep-awake`, `-c path/to/config.txt`. `--lang ru|en` switches the language
(auto-detected from the system locale).

## What it does better than a bash loop

- **One OCI SDK client for the whole run** instead of starting the `oci` CLI (a Python process) on every attempt.
- **Knows which errors are worth retrying.** "Out of capacity" retries at the normal interval, 429 backs off
  exponentially (capped), network / 5xx errors retry, and errors that never go away (wrong OCID, auth,
  `LimitExceeded`) stop with a hint instead of looping forever.
- **No duplicate servers.** Checks for an existing server first, and after a network error checks whether the
  launch went through anyway.
- **Checks Always Free limits** (A1 CPU / RAM and the 200 GB storage that includes every disk) before it starts,
  so you do not end up with a paid server or a launch that can never succeed.
- **Rotates availability domains**, adds **jitter** to every wait, **resolves the newest image** for the chosen OS.
- **Notifications**: desktop (macOS / Linux), Telegram, or the [ntfy](https://ntfy.sh) phone app. Waits for the
  server to boot and prints its public IP.
- Keeps a Mac awake while hunting.

## Run it on a server (Docker)

A laptop that sleeps does not hunt. Any always-on machine works:

```bash
docker build -t a1-hunter .
docker run -d --name a1-hunter --restart unless-stopped \
  -v "$PWD/config.txt:/app/config.txt:ro" \
  -v ~/.oci:/root/.oci:ro \
  -v ~/.ssh/id_ed25519.pub:/root/.ssh/id_ed25519.pub:ro \
  a1-hunter
docker logs -f a1-hunter
```

Paths in `config.txt` (`key_file`, `ssh_public_key_file`) must be valid inside the container, so use `~/...` paths.

## Tips

- **Upgrading the account to Pay As You Go** (still free while you stay within Always Free limits) is widely
  reported to make A1 capacity much easier to get. Set a budget alert if you do.
- Smaller sizes (1-2 CPU) are usually easier to get than 4 / 24.
- Free-tier limits are per tenancy; if you set `compartment_id`, the checks only see that compartment.
- macOS: if you downloaded the binary with a browser instead of the install script, allow it once with
  `xattr -d com.apple.quarantine ./a1-hunter-macos-arm64`.

## Install from source

```bash
pipx install git+https://github.com/w31loa/oci-a1-hunter
# or: uv tool install git+https://github.com/w31loa/oci-a1-hunter
```

## Development

```bash
uv venv && uv pip install -e '.[dev]'
pytest
ruff check . && ruff format --check .
```

Releases: push a tag `vX.Y.Z` and GitHub Actions builds binaries for Linux (x86_64, arm64), macOS (arm64, Intel)
and Windows and attaches them to a release.

---

## По-русски

`a1-hunter` раз в минуту пытается создать бесплатный сервер Oracle Cloud (Ampere A1, до 4 CPU / 24 ГБ RAM /
200 ГБ диска), пока Oracle не выделит мощности. Потом присылает уведомление и показывает команду `ssh`.

**Установка.** macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/w31loa/oci-a1-hunter/main/install.sh | sh
a1-hunter
```

Windows: скачай `a1-hunter-windows-x86_64.exe` из [Releases](https://github.com/w31loa/oci-a1-hunter/releases)
и запусти двойным кликом.

**Настройка.** При первом запуске программа спросит, как настроить:

1. **Ответить на вопросы в консоли.** Шесть шагов: доступ к Oracle (ключ создаётся сам, ты только вставляешь его
   в консоль Oracle), регион, размер сервера, ОС, сеть и SSH, уведомления. В конце сохраняется `config.txt`
   и сразу начинается охота.
2. **Заполнить `config.txt` самому.** Рядом с программой появится файл с комментариями на русском, он откроется
   в редакторе. Заполни и запусти `a1-hunter` ещё раз.

Дальше просто запускай `a1-hunter`: он возьмёт настройки из `config.txt`. Язык выбирается по системе,
переключить можно через `--lang ru`.

Чтобы охота шла без перерывов, ноутбук не должен засыпать (на macOS программа сама не даёт ему уснуть)
или запусти её на любом постоянно включённом сервере через Docker (см. выше).

## License

MIT. Not affiliated with Oracle.
