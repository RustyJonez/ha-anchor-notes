# Anchor Notes for Home Assistant

Read-only sensors for [Anchor](https://github.com/ZhFahim/anchor), the self-hosted, offline-first
notes app: pending reminders, pinned notes, and recently created / edited notes.

Each Anchor account is added separately, so every household member gets their own sensors.
You can then use per-user card visibility to give each person their own home screen.

> Unofficial. Not affiliated with the Anchor project. Tested against Anchor v0.18.0.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repo's URL, category **Integration**.
2. Install **Anchor Notes**, then restart Home Assistant.

Manual install: copy `custom_components/anchor_notes` into `/config/custom_components/` and restart.

## Set up

1. In Anchor, sign in as the person and open **Settings → API token**. Copy it.
2. In Home Assistant: **Settings → Devices & services → Add integration → Anchor Notes**.
3. Pick the **Home Assistant user** this Anchor account belongs to, then enter your Anchor URL
   (e.g. `http://192.168.1.10:3000`) and the token. Name is optional and defaults to the user's name.
4. Repeat for each person.

Already added an account without a user? **Configure** → *Home Assistant user*.

The token is stored in Home Assistant's config entry storage. No `secrets.yaml` needed.
If the token is regenerated or revoked in Anchor, Home Assistant shows a re-authenticate prompt.
To change the URL, use **⋮ → Reconfigure** on the entry.

### Options (Configure button)

| Option | Default |
|---|---|
| Home Assistant user | none |
| Refresh every | 5 min |
| Notes in recent lists | 5 |
| Max pinned notes listed | 10 |
| Max upcoming reminders listed | 10 |
| Leave pinned notes out of recent lists | on |

## Sensors

Entity IDs follow the entry name, e.g. for *Alex*: `sensor.anchor_alex_pending_reminders`.
Dashboards shouldn't rely on them, though. See [Dashboards](#dashboards).

| Sensor | State | Attributes |
|---|---|---|
| Pending reminders | count of all reminders (overdue + upcoming) | `overdue_count`, `overdue`, `upcoming` |
| Next reminder | timestamp of the next upcoming reminder | `title`, `note_id`, `recurrence` |
| Pinned notes | count | `notes` |
| Recently created | timestamp of the newest note | `notes` |
| Recently edited | timestamp of the most recent edit | `notes` |
| Recent notes view (select) | `created` / `edited` | A dashboard switch for which recent list to show. Local only. |

Items in `notes`:

```yaml
id: 6f1c…
title: Groceries        # falls back to the start of the text, then "Untitled"
preview: Milk Eggs Bread # first 80 characters of plain text
color: yellow            # Anchor's note background, or null
created: "2026-11-10T14:02:11+00:00"
updated: "2026-11-16T20:15:40+00:00"
shared_by: Sam           # set when someone else shared the note with you
lines:                   # first 6 non-empty lines, with Anchor's line formats
  - { text: Shopping, list: null, indent: 0, header: true, number: null }
  - { text: Milk, list: checked, indent: 0, header: false, number: null }
  - { text: Eggs, list: unchecked, indent: 0, header: false, number: null }
  - { text: Vents, list: bullet, indent: 1, header: false, number: null }
  - { text: Step one, list: ordered, indent: 0, header: false, number: 1 }
line_count: 9            # all non-empty lines, so cards can show "+3 more"
reminder:                # or null
  remind_at: "2026-11-20T09:00:00-05:00"
  recurrence: none       # none | daily | weekly | monthly | yearly
```

Items in `overdue` / `upcoming`: `id`, `title`, `color`, `remind_at`, `recurrence`, `shared_by`.

The list attributes are kept out of the recorder, so they don't grow your database.
To refresh right away, call `homeassistant.update_entity` on any of the sensors.

## Dashboards

Every Anchor entity has two extra attributes: `ha_user_id` (the linked Home Assistant user) and
`anchor_sensor` (`pending_reminders`, `next_reminder`, `pinned_notes`, `recent_created`,
`recent_edited`, `recent_view`). A card can find the viewer's own entities without knowing any
entity IDs. With button-card:

```js
const mine = {};
for (const s of Object.values(states))
  if (s.attributes.anchor_sensor && s.attributes.ha_user_id === user.id) mine[s.attributes.anchor_sensor] = s;
// mine.pending_reminders.state, mine.pinned_notes.attributes.notes, ...
```

Toggle the recent view with `select.select_next` on `mine.recent_view.entity_id`.

### Notes on behaviour

- **Reminder times.** Anchor stores reminders as local wall-clock times with no time zone.
  They're read in Home Assistant's time zone, so set that to match the phones you use Anchor on.
- **Overdue** reminders stay listed until they're cleared in Anchor (Anchor itself shows them as "late").
- Anchor's API leaves out **archived and trashed** notes, so their reminders don't appear.
- Sharing: each person's pins and reminders are their own, even on shared notes.

## Development

```sh
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

CI runs `hassfest`, HACS validation and the tests on every push.
