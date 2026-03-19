# FalconEye Knowledge Base

This folder contains documented patterns, domain knowledge, and data dictionaries
learned by analyzing Gulf Air (GF) Sabre API responses across multiple flights.

## Contents

| File                   | Description                                                                |
| ---------------------- | -------------------------------------------------------------------------- |
| flight_statuses.md     | Flight lifecycle statuses (OPENCI → FINAL → PDC)                           |
| passenger_lifecycle.md | How passenger state changes through check-in, boarding, etc.               |
| data_dictionary.md     | All field values: booking classes, passenger types, indicators, edit codes |
| change_tracking.md     | Design for detecting differences between API snapshots                     |
| data_preservation.md   | Strategy for storing all raw data for data warehouse use                   |
| ingestion_api.md       | FastAPI trigger for Sabre SOAP ingestion and required request inputs       |
| scenarios.md           | Real-world scenarios discovered from data analysis                         |
