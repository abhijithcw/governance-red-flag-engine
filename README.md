# governance-red-flag-engine

Rule-based governance red-flag screener for Indian listed companies, built to run unattended on a Raspberry Pi 5 and publish a phone-readable static dashboard on the LAN.

It ingests public exchange filings (LODR Reg 30 announcements, Reg 31 shareholding XBRL, SAST pledge disclosures, PIT Reg 7(2) insider trades, Reg 23(9) RPT XBRL, Reg 33 results dates), stores them in SQLite keyed by ISIN, and scores companies on configurable indicators: mid-term statutory auditor resignations, rising promoter pledge, RPT spikes vs revenue, delayed results, insider trade clusters, and sharp shareholding shifts. Every number on the dashboard links to its source filing and shows its as-of date.

See `SCHEMA.md`, `docs/DATA_SOURCES.md`, `config/scoring.yaml`, and `STATUS.md`.

*Indicators derived from public filings; not an allegation, investment advice, or legal opinion.*
