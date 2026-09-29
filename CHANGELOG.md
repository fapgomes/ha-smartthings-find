# Changelog

## Unreleased

### Added

- `binary_sensor` Connected, from the last connection check.
- `binary_sensor` Lock: the screen lock at the last connection check (verified
  to follow the phone being locked and unlocked), plus remote lock.
- Diagnostic sensors Network (with the Wi-Fi BSSID) and Last request result.
- `location_type` attribute on the tracker (`basic` or `last`).

## 2.0.0 — 2026-09-29

Complete rewrite, compatible with the config entries and entities of
upstream 1bobby-git/HA-SmartThings-Find v1.4.4: replace the upstream
integration in HACS (without deleting it in Home Assistant) and the existing
entities, automations and history keep working.

### Fixed

- **Update location now works for phones, watches and earbuds.** It sends
  `CHECK_CONNECTION` + `LOCATION` like the website; `CHECK_CONNECTION_WITH_LOCATION`
  is only sent to SmartTags. Upstream sent the tag operation to every device,
  which non-tag devices ignore.
- **Battery for watches and phones.** The answer is read from
  `getOperationResult.do`, with the real percentage; upstream only read the
  cached snapshot, so watch batteries stayed unknown.
- Running operations (`oprnStsCd` 2100) and results of earlier requests are no
  longer taken as the answer to a new request.

### Added

- Current SmartTag location from `getTagLocation.do` after a request.
- Degraded session detection: when known devices stop being returned while the
  cookie is still accepted, Home Assistant asks for a new cookie.
- Devices no longer returned by Samsung can be deleted from Home Assistant.
- Buttons are unavailable when Samsung does not offer the action for a device
  (e.g. *Stop ring* on SmartTags).
- Diagnostics with the raw API responses, cookie, user IDs and coordinates
  redacted.
- English and Portuguese translations.

### Changed

- Reauthentication is requested only after 3 rejected update cycles in a row,
  with retries at setup; commands (ring, locate) are never replayed.
- Session keepalive runs only when the update interval is longer than it.
- Cookie-only authentication: the Samsung Account method and the
  `samsung-re-find` dependency are gone.
