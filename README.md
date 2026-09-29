<p align="center">
  <img src="custom_components/smartthings_find/brand/logo@2x.png" alt="SmartThings Find" width="420">
</p>

<p align="center">
  <a href="https://github.com/fapgomes/ha-smartthings-find/releases"><img src="https://img.shields.io/github/v/release/fapgomes/ha-smartthings-find?style=flat-square&label=Release" alt="Release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS Custom"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/fapgomes/ha-smartthings-find?style=flat-square&label=License" alt="License"></a>
</p>

# SmartThings Find for Home Assistant

Brings the location and battery of the Samsung devices registered in
[SmartThings Find](https://smartthingsfind.samsung.com) into Home Assistant:
phones, tablets, watches, earbuds and SmartTags. It can also make them ring
and ask them for their current position.

> [!NOTE]
> Samsung has no public API for SmartThings Find. This integration uses the
> website's session, authenticated with the browser's `Cookie` header. When
> the session expires, Home Assistant asks for a new cookie.

## Installation

### With HACS (recommended)

[![Open the repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fapgomes&repository=ha-smartthings-find&category=integration)

1. Click the button above (or, in HACS: ⋮ → **Custom repositories** →
   `https://github.com/fapgomes/ha-smartthings-find`, type **Integration**).
2. Download **SmartThings Find** and restart Home Assistant.

### Manual

Copy the `custom_components/smartthings_find` folder of this repository to
`<config>/custom_components/` and restart Home Assistant.

## Configuration

[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartthings_find)

Or go to **Settings → Devices & services → Add integration → SmartThings
Find**. You will be asked for the `Cookie` header:

1. On a computer, open a **private browser window** and sign in to
   [smartthingsfind.samsung.com](https://smartthingsfind.samsung.com).
2. Open the developer tools (F12) → **Network** and reload the page.
3. Select the `chkLogin.do` request and copy its complete `Cookie` request
   header.
4. Close the private window **without signing out**, and do not use that
   session in the browser again.

> [!IMPORTANT]
> Do not share one session between the browser and Home Assistant: Samsung
> may end it. Treat the cookie like a password, since it gives access to your
> devices' locations while the session is active.

### Options

| Option | Default | Description |
|---|---|---|
| Update interval | 120 s | How often the devices are read |
| Session keepalive | 180 s | Only used when the update interval is longer than this |
| SmartTag mode | Passive | Active asks the tag for its position on every update |
| Mode for other devices | Passive | Active asks for the position and battery on every update (uses the device's battery) |

The cookie can be replaced in the options or with **Reconfigure**.

## Entities

For each device:

| Entity | Description |
|---|---|
| `device_tracker` | Last known position, with its time and source as attributes |
| `sensor` Battery | Battery level (see below) |
| `sensor` Last update | When the device last reported its position to Samsung |
| `button` Ring / Stop ring | Makes the device ring, or stops it |
| `button` Update location | Asks the device for its position and battery and waits for the answer |

### What to expect

- **Update location** sends the same requests as the website and waits for
  the device to answer. Phones usually answer within 10 seconds, watches can
  take around 30 seconds. While waiting, the *Last update* sensor shows a
  progress icon and its `location_request` attribute is `pending`; it ends as
  `ok` or `timeout`.
- **Battery:** phones and watches report the real percentage when they answer
  a location request. Samsung's cached data and SmartTags only report steps
  (100, 50, 15 or 5 %).
- **SmartTags cannot be stopped remotely.** Samsung does not offer it, so
  *Stop ring* is unavailable for tags: they stop on their own after a while,
  or when you press the tag's button. The same applies to any button whose
  action Samsung does not offer for a device.
- A device that is off, out of coverage or not linked to SmartThings Find
  keeps its last known position, which may be old (see the `location_time`
  attribute).

## Migrating from 1bobby-git/HA-SmartThings-Find

This integration uses the same domain (`smartthings_find`) and the same
entity identifiers, so it can replace the original one without losing
entities, automations or history:

1. In HACS, remove the `1bobby-git/HA-SmartThings-Find` repository. **Do not
   delete** the integration in Settings → Devices & services.
2. Install this integration with HACS (see [Installation](#installation))
   and restart.
3. The existing entry now runs the new code. If the session has expired,
   use **Reauthenticate** with a new cookie.

To start from scratch, also delete the old integration before adding it
again. The entities may then get different IDs.

## Troubleshooting

- **Devices that disappear:** Samsung sometimes keeps accepting a session but
  stops returning some devices. The integration detects this and asks for a
  new cookie. A device you removed from your Samsung account on purpose can
  be deleted from its device page in Home Assistant.
- **Diagnostics:** on the integration page, ⋮ → **Download diagnostics**.
  They include the API responses, with the cookie, user IDs and coordinates
  redacted.
- **Debug logs:**

  ```yaml
  logger:
    logs:
      custom_components.smartthings_find: debug
  ```

## Development

```sh
python3 -m pytest tests
```

The API client (`api.py`) does not depend on Home Assistant. The tests run
against a local server that simulates Samsung, with no account or cookie.
`tools/extract_site_js.py` downloads the website's public JavaScript and
lists the endpoints and operations it uses.

## Credits and license

Rewritten from the work of
[1bobby-git/HA-SmartThings-Find](https://github.com/1bobby-git/HA-SmartThings-Find).
[MIT](LICENSE) license. Unofficial project, not affiliated with Samsung.
SmartThings is a trademark of Samsung Electronics.
