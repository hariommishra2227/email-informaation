# Customer Email Extraction AI

Customer Email Extraction AI is a Streamlit app for extracting customer details from PDF uploads, TXT uploads, bulk email TXT files, pasted manual text, and Microsoft Outlook messages. The Outlook layer is prepared for Microsoft 365 delegated authentication, but the default mode is a safe local mock mode that does not require Azure credentials.

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
.venv\Scripts\activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
streamlit run app.py
```

## Mock Outlook Mode

The default `OUTLOOK_MODE` is `mock`. In this mode no Microsoft login, tenant ID, client ID, or client secret is required. Demo employees can be selected in the sidebar, and each employee sees only their own mock Outlook emails and imported customer records.

## Switching To Live Outlook Later

Copy `.env.example` to `.env`, set `OUTLOOK_MODE=live`, and provide:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_REDIRECT_URI`

The Graph placeholders use delegated `User.Read` and `Mail.Read` permissions. The app does not request `Mail.ReadWrite`, does not mark emails as read, and does not delete or modify mailbox messages.

## Security

- Never commit `.env` or real Azure secrets.
- Client secrets are read only from environment variables.
- The Settings page shows whether credentials are configured, but never displays the client secret.
- SQLite local data is stored in `customer_data.db`, which is ignored by git.

## Multi-User Separation

All Outlook messages and customers include `user_id`. Mock users are isolated by demo employee email. Live mode should store the Microsoft user ID/email after login and use that as `user_id`, so one employee cannot see another employee's mailbox data or imported records.

## Project Structure

- `app.py`: Streamlit navigation entry point plus legacy upload helper functions.
- `config.py`: environment configuration and mock/live mode selection.
- `models.py`: Outlook and customer dataclasses.
- `services/`: Graph auth/client placeholders, mock Outlook data, email processing, customer helpers.
- `storage/database.py`: SQLite schema and parameterized queries.
- `pages/`: Outlook Inbox, Manual Extraction, Customer Registry, and Settings page renderers.
- Existing modules such as `extractor.py`, `duplicate_detector.py`, `bulk_email_processor.py`, and `excel_exporter.py` remain in use.

## Current Limitations

- Live Microsoft sign-in is scaffolded but not complete until Azure app registration and redirect URI configuration are supplied.
- Attachments are listed by name only; attachment content extraction is not implemented.
- The local SQLite database is intended for development and demo use, not production hosting.
- The extraction engine is rule-based and may need tuning for unusual email formats.

## Microsoft 365 Administrator Steps

1. Register an Azure app.
2. Configure the redirect URI used by Streamlit.
3. Grant delegated `User.Read` and `Mail.Read` permissions.
4. Provide tenant ID, client ID, and a secure client secret through environment variables.
5. Confirm organizational consent and mailbox access policy.
