# Campus Find — University Lost & Found Mobile App

A cross-platform mobile application built with Flutter, Python, and SQLite to help university students and staff report, track, and recover lost and found items across campus.

## Features

- **Item Reporting:** Post lost or found items with descriptions, categories, and campus locations.
- **Smart Search & Filtering:** Quickly browse and filter reported items to match lost belongings with found entries.
- **Claim & Verification:** Connect finders with rightful owners to verify ownership and arrange item recovery.
- **Lightweight Local & Backend Storage:** Fast, reliable data persistence powered by SQLite (`campus_find.sqlite3`) and a Python backend (`server.py`).

## Tech Stack

- **Frontend:** Flutter / Dart (`main.dart`)
- **Backend:** Python (`server.py`)
- **Database:** SQLite (`campus_find.sqlite3`)

## Project Structure

```text
Lost_and_Found_mobile_app/
├── analysis_options.yaml
├── campus_find.sqlite3
├── main.dart
├── pubspec.lock
├── pubspec.yaml
└── server.py
