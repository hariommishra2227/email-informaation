# Customer Email Extraction AI

Customer Email Extraction AI is a Phase 1 reusable extraction engine that reads a business email and extracts contact details using spaCy, regular expressions, phonenumbers, and BeautifulSoup.

## Installation

1. Create and activate a Python 3.11+ virtual environment.
2. Install the dependencies:

```bash
pip install -r requirements.txt
```

3. Download the spaCy English model if needed:

```bash
python -m spacy download en_core_web_sm
```

## Running instructions

Run the extraction engine directly:

```bash
python extractor.py
```

Run the test suite:

```bash
python test_extractor.py
```

## Project architecture

- `extractor.py`: reusable extraction engine with modular functions for HTML cleanup, email extraction, phone normalization, spaCy entity parsing, address detection, and designation detection.
- `requirements.txt`: all Python dependencies required by the project.
- `test_extractor.py`: sample business email scenarios that print the extracted JSON output for each scenario.
- `README.md`: installation and usage guidance.

## Assumptions

- Input is a plain-text or HTML email body.
- The engine is rule-based and deterministic for Phase 1.
- If a field cannot be confidently inferred, the engine returns an empty string.
- International phone numbers are normalized where possible using `phonenumbers`.

## Limitations

- This is a reusable extraction engine only; it does not include UI, API, database storage, Office 365 integration, Excel export, or duplicate detection.
- spaCy entity extraction is language- and formatting-sensitive.
- Designation detection uses keyword rules and may miss unusual titles.
- Address extraction is conservative and may return only the most obvious location-like candidate.
