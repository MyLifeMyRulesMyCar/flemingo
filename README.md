# Flemingo — Edge Controller for Purple Pi OH2

Industrial edge controller: GPIO digital I/O, CAN bus (MCP2515), Modbus RTU,
MQTT bridges, JWT-authenticated REST API, real-time WebSocket dashboard.

## Quick start

```bash
python3 api/app.py                    # Flask API at http://localhost:5000
cd dashboard && npm run dev           # Vite dashboard at http://localhost:5173
```

## Install on a fresh Purple Pi

```bash
sudo bash scripts/setup.sh
```

## Field update

```bash
sudo bash scripts/update.sh           # latest master
sudo bash scripts/update.sh v0.12.0   # specific tag
```

## Test

```bash
pytest tests/ -v                      # 151 tests, zero hardware needed
pytest tests/ -v -m "not hardware"    # CI-safe subset
```

## Service management

```bash
systemctl status flemingo             # check the service
journalctl -u flemingo -f             # follow live logs
systemctl restart flemingo            # restart after api/app.py changes
```

## Docs

- [Deployment](docs/DEPLOYMENT.md)
- [Changelog](CHANGELOG.md)
