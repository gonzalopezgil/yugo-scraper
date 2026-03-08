# student-rooms-cli 🏠

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License MIT](https://img.shields.io/github/license/gonzalopezgil/student-rooms-cli)
![Providers](https://img.shields.io/badge/providers-2_(Yugo_%2B_Aparto)-teal)

Multi-provider student accommodation finder and monitor. Discover properties, scan for availability, and get instant alerts when rooms matching your criteria appear.

Built for students hunting semester accommodation — especially when options appear unpredictably and sell out fast.

## What can I do?

| You're wondering… | Command | What it does |
|---|---|---|
| "What properties are available in Dublin?" | `student-rooms discover --provider all` | Lists all residences from every provider in your target city |
| "Are there Semester 1 rooms in Dublin under €300/week?" | `student-rooms scan --provider all` | One-shot scan filtered by semester & your config criteria |
| "Show me everything, not just Semester 1" | `student-rooms scan --all-options --json` | Returns all tenancy options (full year, semester 2, etc.) as JSON |
| "Alert me the moment something opens up" | `student-rooms watch --provider all` | Continuous loop — sends a notification only when **new** options appear |
| "Can I actually book this room right now?" | `student-rooms probe-booking --residence "Binary Hub"` | Deep-probes the booking flow and returns direct booking links |
| "Is my notification setup working?" | `student-rooms notify --message "Test 🏠"` | Sends a test message through your configured notification backend |
| "Does this tenancy option count as Semester 1?" | `student-rooms test-match --from-year 2026 --to-year 2027` | Tests the semester-matching logic against sample data |

## Providers

### Yugo

**Website:** [yugo.com](https://yugo.com) · **Method:** REST API (undocumented JSON endpoints) · **Coverage:** 11 countries, 70+ cities

<details>
<summary>Full country & city list</summary>

| Country | Cities |
|---------|--------|
| 🇺🇸 USA | Auburn, Fayetteville, Flagstaff, Tempe, Tucson, Fort Collins, Gainesville, Tallahassee, Atlanta, Kennesaw, Champaign, Urbana, West Lafayette, Lexington, Louisville, Minneapolis, Starkville, Charlotte, Greenville, Raleigh, Charleston, Cincinnati, Corvallis, Eugene, State College, Austin, College Station, Salt Lake City, Charlottesville, Pullman, Seattle, Madison |
| 🇬🇧 UK | Birmingham, Bournemouth, Bristol, Cambridge, Cardiff, Edinburgh, Lancaster, Lincoln, Liverpool, London, Manchester, Newcastle, Norwich, Nottingham, Plymouth, Portsmouth, Sheffield, Southampton |
| 🇮🇪 Ireland | Cork, Dublin |
| 🇪🇸 Spain | Alcalá de Henares, Barcelona, Madrid, Salamanca, Sevilla, Valencia |
| 🇩🇪 Germany | Darmstadt, Frankfurt, Hamburg, Leipzig, Münster |
| 🇫🇷 France | Bordeaux, Lille, Lyon, Marseille, Paris |
| 🇵🇹 Portugal | Porto |
| 🇮🇹 Italy | Bologna, Florence, Padova, Turin |
| 🇦🇹 Austria | Vienna |
| 🇦🇺 Australia | Adelaide, Melbourne, Perth |
| 🇦🇪 UAE | Dubai |

</details>

### Aparto

**Website:** [apartostudent.com](https://apartostudent.com) · **Method:** StarRez portal probing + site scraping · **Coverage:** 5 countries, 14 cities (discovered dynamically)

| Country | Cities |
|---------|--------|
| 🇮🇪 Ireland | Dublin |
| 🇪🇸 Spain | Barcelona |
| 🇮🇹 Italy | Milan, Florence |
| 🇬🇧 UK | Aberdeen, Brighton, Bristol, Cambridge, Glasgow, Kingston, Lancaster, Oxford, Reading |
| 🇫🇷 France | Paris *(discover only — no StarRez portal)* |

## Installation

```bash
# Install directly from GitHub
pip install "git+https://github.com/gonzalopezgil/student-rooms-cli.git"

# Or clone for development
git clone https://github.com/gonzalopezgil/student-rooms-cli.git
cd student-rooms-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

```bash
# Copy and edit the sample config
cp config.sample.yaml config.yaml

# List all properties in Dublin
python -m student_rooms discover --provider all

# Scan for Semester 1 availability
python -m student_rooms scan --provider all

# Scan ALL options (including full-year, unfiltered)
python -m student_rooms scan --provider all --all-options --json

# Continuous monitoring with alerts
python -m student_rooms watch --provider all

# Deep-probe a specific option's booking flow
python -m student_rooms probe-booking --provider yugo --residence "Dominick Place"

# Send a test notification
python -m student_rooms notify --message "Test alert 🏠"
```

If installed via `pip install -e .`, you can also use:
```bash
student-rooms discover --provider all
student-rooms scan --provider all
```

## Commands

| Command | Description |
|---------|-------------|
| `discover` | List all properties available from providers in your target city |
| `scan` | One-shot scan for rooms matching your semester/price/privacy criteria |
| `watch` | Continuous monitoring loop — alerts on new availability |
| `probe-booking` | Deep-probe the booking flow for a matched option (generates direct booking links) |
| `notify` | Send a test notification to verify your notification setup |
| `test-match` | Test the semester matching logic against sample data |

## Configuration

Copy `config.sample.yaml` to `config.yaml` and edit:

```yaml
# Target city
target:
  country: "Ireland"
  city: "Dublin"

# Provider settings
providers:
  aparto:
    term_id_start: 1200
    term_id_end: 1600

# Academic year & semester detection
academic_year:
  start_year: 2026
  end_year: 2027
  semester1:
    name_keywords: ["semester 1", "sem 1", "first semester"]
    require_keyword: true
    start_months: [8, 9, 10]
    end_months: [12, 1, 2]

# Filters
filters:
  private_bathroom: null   # true | false | null (any)
  private_kitchen: null    # true | false | null (any)
  max_weekly_price: 350.0
  max_monthly_price: null

# Monitoring interval
polling:
  interval_seconds: 3600
  jitter_seconds: 300

# Notifications (see below)
notifications:
  type: "stdout"
```

Filters apply to both `scan` and `watch`. If a room lacks the required metadata (e.g. private bathroom/kitchen), it will be excluded when that filter is set.

### Notification Backends

Choose one notification backend via `notifications.type`:

#### `stdout` (default)
Just prints to console. No configuration needed.

#### `webhook`
Generic HTTP POST — works with Discord webhooks, Slack, [ntfy.sh](https://ntfy.sh), and more.

```yaml
notifications:
  type: "webhook"
  webhook:
    url: "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
    method: "POST"
    headers: {}
    body_template: '{"content": "{message}"}'
```

#### `telegram`
Direct Telegram Bot API — provide your own bot token and chat ID.

```yaml
notifications:
  type: "telegram"
  telegram:
    bot_token: "YOUR_BOT_TOKEN"
    chat_id: "YOUR_CHAT_ID"
    parse_mode: null
```

#### `openclaw`
[OpenClaw](https://github.com/nichochar/openclaw) CLI integration. Requires OpenClaw installed and configured. Supports message mode, agent mode, and automatic reservation job creation.

```yaml
notifications:
  type: "openclaw"
  openclaw:
    mode: "message"
    channel: "telegram"
    target: "YOUR_CHAT_ID"
    create_job_on_match: false
```

## How It Works

### Yugo Provider
1. Resolves country → city → residences via Yugo's JSON API
2. For each residence, fetches room types and tenancy options
3. Filters by academic year and semester using config-driven name keywords + date rules
4. Supports full booking-flow probing (available beds, flat selection, portal redirect)

### Aparto Provider (StarRez)
1. **Dynamically discovers** properties for the target city by scraping apartostudent.com
2. Establishes session via the EU StarRez portal (auto-selects the correct country)
3. Probes a range of **termIDs** via direct room search URLs on the appropriate regional portal
4. Filters terms by matching property names against the target city's properties (supports abbreviations like PA→Pallars, CdM→Cristobal de Moura)
5. Detects Semester 1 using the same config-driven name keywords + date rules as Yugo
6. Enriches results with pricing data scraped from property pages

**Portal topology:**
- Ireland, Spain, Italy → shared IE portal (`apartostudent.starrezhousing.com`)
- UK → separate UK portal (`apartostudentuk.starrezhousing.com`)
- France → no StarRez portal (discover-only, no term scanning)

### Watch Mode
- Scans all enabled providers at configurable intervals
- Deduplicates: only alerts on **new** options not previously seen
- Persists seen options in `~/.local/share/student-rooms-cli/seen_options.json` (or `$XDG_DATA_HOME`)
- Adds random jitter to avoid request patterns

## Agent Integration

This tool is designed to work well with AI agents and automation:

```bash
# JSON output for programmatic consumption
python -m student_rooms scan --provider all --json
python -m student_rooms discover --provider all --json

# Scan + notify in one command
python -m student_rooms scan --provider all --notify

# Watch mode as a background process
python -m student_rooms watch --provider all &
```

The `--json` flag outputs structured data suitable for parsing by AI agents, scripts, or pipeline tools.

## Project Structure

```
student_rooms/
├── __init__.py
├── __main__.py          # python -m student_rooms entry point
├── cli.py               # CLI argument parsing + command handlers
├── matching.py          # Semester matching logic
├── models/
│   └── config.py        # Configuration dataclasses + YAML loader
├── providers/
│   ├── base.py          # BaseProvider ABC + RoomOption dataclass
│   ├── yugo.py          # Yugo REST API provider
│   └── aparto.py        # Aparto StarRez portal provider
└── notifiers/
    ├── base.py           # BaseNotifier ABC + factory
    ├── webhook.py        # Generic HTTP webhook
    ├── telegram.py       # Direct Telegram Bot API
    └── openclaw.py       # OpenClaw CLI integration (optional)
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_notifiers.py -v
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-provider`)
3. Add tests for new functionality
4. Ensure all tests pass (`python -m pytest tests/ -v`)
5. Submit a pull request

### Adding a New Provider

1. Create `student_rooms/providers/your_provider.py`
2. Implement `BaseProvider` (see `base.py` for the interface)
3. Register it in `cli.py`'s `make_providers()` function
4. Add tests in `tests/test_your_provider.py`

### Adding a New Notifier

1. Create `student_rooms/notifiers/your_notifier.py`
2. Implement `BaseNotifier` (see `base.py`)
3. Add config dataclass in `models/config.py`
4. Register in `notifiers/base.py`'s `create_notifier()` factory
5. Add tests in `tests/test_notifiers.py`

## Roadmap

- [ ] More providers (Collegiate, Host, Unite Students)
- [ ] GitHub Actions CI
- [ ] PyPI package

Actively developed — contributions welcome!

## License

MIT — see [LICENSE](LICENSE).
