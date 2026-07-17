# Customer Email Extraction AI

Customer Email Extraction AI is a Streamlit app for extracting customer details from PDF uploads, TXT uploads, bulk email TXT files, pasted manual text, and Microsoft Outlook messages. Outlook runs in Demo Mode until Microsoft Entra credentials are configured through Streamlit Secrets or environment variables.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Secrets

Use Streamlit Cloud Secrets or local `.streamlit/secrets.toml`. Do not commit real secrets.

```toml
[microsoft]
client_id = "YOUR_APPLICATION_CLIENT_ID"
client_secret = "YOUR_NEW_CLIENT_SECRET_VALUE"
tenant_id = "YOUR_DIRECTORY_TENANT_ID"
redirect_uri = "YOUR_REGISTERED_REDIRECT_URI"
```

Environment-variable fallback is also supported:

- `MICROSOFT_CLIENT_ID`
- `MICROSOFT_CLIENT_SECRET`
- `MICROSOFT_TENANT_ID`
- `MICROSOFT_REDIRECT_URI`

The redirect URI must exactly match the URL that opens the Streamlit app and returns to `pages/Outlook Connector.py`. For local testing, use the full local page URL shown by Streamlit for that page. For Streamlit Cloud, use the deployed HTTPS page URL for the Outlook Connector page.

## Microsoft Entra Configuration

1. Open Microsoft Entra admin center.
2. Create or open an App Registration.
3. Copy the Application Client ID.
4. Copy the Directory Tenant ID.
5. Add a Web platform redirect URI that exactly matches `MICROSOFT_REDIRECT_URI`.
6. Create a new client secret and copy the Secret Value, not the Secret ID.
7. Add Microsoft Graph delegated permissions:
   - `User.Read`
   - `Mail.Read`
   - `offline_access`
   - `openid`
   - `profile`
   - `email`
8. Grant admin consent if your tenant requires administrator approval.

## Streamlit Cloud Deployment

1. Open Streamlit Cloud.
2. Go to My Apps.
3. Select the deployed app.
4. Open Settings.
5. Open Secrets.
6. Paste the TOML configuration shown above.
7. Save.
8. Reboot the app.

## Security

- Never place Microsoft secrets in GitHub.
- Never share passwords or client secret values in chat or screenshots.
- Rotate and revoke any exposed old client secret.
- Use the client Secret Value in Streamlit Secrets, not the Secret ID.
- Keep `.streamlit/secrets.toml`, `.env`, token caches, and local databases out of Git.

## Troubleshooting

- `AADSTS50011`: the redirect URI in Entra does not exactly match the configured redirect URI.
- Invalid client secret: create a new client secret and paste the Secret Value into Streamlit Secrets.
- Expired client secret: create a new secret, update Streamlit Secrets, then reboot the app.
- Admin consent required: grant tenant admin consent for the delegated Graph permissions.
- Tenant mismatch: verify the Directory Tenant ID belongs to the app registration.
- `Mail.Read` missing: add delegated `Mail.Read` and grant consent if required.
- App remains in Demo Mode: one or more `[microsoft]` values is missing or empty.

## Project Structure

- `app.py`: Streamlit dashboard and page link to Outlook Connector.
- `config.py`: safe configuration from Streamlit Secrets or environment variables.
- `services/graph_auth.py`: MSAL authorization-code flow with session-only token storage.
- `services/graph_client.py`: Microsoft Graph `/me` and inbox access, plus demo mailbox support.
- `services/email_processor.py`: Outlook/PDF/TXT/manual customer extraction pipeline.
- `storage/database.py`: SQLite schema and parameterized persistence.
- `pages/Outlook Connector.py`: Outlook sign-in, inbox selection, extraction, registry save, and Excel export.
- `pages/settings.py`: safe Microsoft configuration status.

## Limitations

Live Microsoft login cannot be verified without administrator-provided tenant ID, client ID, new client Secret Value, registered redirect URI, mailbox access, and approved Graph delegated permissions. Attachments are listed by metadata only; attachment content extraction is not implemented.
