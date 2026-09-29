# Install and update BidBoard

## Docker Desktop or Linux server

Download and extract the `v0.1.1` deployment ZIP or tarball from [Releases](https://github.com/csnyder256/grain-bids-to-excel/releases). Verify its SHA-256 using `checksums.txt` (`sha256sum -c checksums.txt` on Linux). From the extracted folder:

```sh
docker compose -p bidboard up --build -d
```

Open <http://localhost:8383>. The image includes Python and Chromium; startup does not download dependencies or open a browser. Use Settings to configure companies, folders and the optional schedule. Scanning needs internet access. The web app is for a trusted local user; the port binds to loopback. `BIDBOARD_PORT` changes the host port.

Settings, database and logs live in the `bidboard-data` volume; spreadsheets and archives live in `bidboard-sheets`. Named volumes survive container replacement. Use the same `-p bidboard` project name after extracting a newer version. The UI Quit command stops the container; start it again with `docker compose -p bidboard up -d`.

## Python on Windows, macOS or Linux

Requires Python 3.11+ and the platform's Playwright system dependencies. From a source checkout or deployment bundle:

```sh
python -m venv .venv
# Linux/macOS; Windows uses .venv\Scripts\python.exe
.venv/bin/python -m pip install -r app/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python app/src/launcher.py
```

The launcher selects a local port and opens the browser. Data remains inside the application folder. See the README for desktop launcher setup.

## Upgrade and backup

Stop scanning and run `docker compose -p bidboard stop` before backing up. Use Docker Desktop's volume export, or archive each named volume with a temporary utility container. Keep both volumes together. Extract the new release into a separate folder, use the same Compose project name, and run the install command again. Keep the previous folder and volume backups for rollback. Do not delete volumes while upgrading.

For a Python install, copy `app/data` and `Spreadsheets` to the new folder while the application is stopped. Install its requirements into a fresh virtual environment. No real settings, bid database or generated spreadsheets are shipped in releases.
