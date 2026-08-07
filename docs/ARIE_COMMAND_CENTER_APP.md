# ARIE Command Center App

ARIE can be opened as a persistent desktop-style browser app from Windows.

## Recommended Internal Setup

Use the local Command Center for day-to-day operation:

```powershell
.\scripts\install_arie_desktop_shortcut.ps1 -Mode Local -StartBot
```

This creates a desktop shortcut named `ARIE Command Center`. Opening it will:

- start the local Streamlit Command Center if it is not already running
- start the standalone ARIE Telegram listener if it is not already running
- open the Command Center in a browser app window

## Cloud Run Setup

The deployed Command Center uses an in-app authentication layer backed by
Databricks Delta tables in `ATTRIBUTION_OPS_SCHEMA`:

- `auth_users`
- `auth_sessions`
- `auth_events`

The first admin user is bootstrapped only when the user table is empty. Set
`ARIE_BOOTSTRAP_ADMIN_EMAIL` and the Secret Manager secret
`ARIE_BOOTSTRAP_ADMIN_PASSWORD`. If the password secret is not present, ARIE
falls back to `API_KEY_ADMIN` for the first bootstrap login.

Use Cloud Run proxy mode when you want the hosted Command Center but the service is private:

```powershell
.\scripts\install_arie_desktop_shortcut.ps1 -Mode CloudProxy -StartBot
```

This starts:

- `gcloud run services proxy attribution-ui`
- a browser app window at `http://127.0.0.1:8080`

Use direct Cloud Run URL mode only if the UI service is publicly accessible or your browser is authenticated:

```powershell
.\scripts\install_arie_desktop_shortcut.ps1 -Mode CloudUrl
```

## One-Time Open Without Installing

```powershell
.\scripts\arie_open_command_center.ps1 -Mode Local -StartBot
```

Logs and runtime PID files stay in the repo root:

- `streamlit_out.log`
- `streamlit_err.log`
- `streamlit.pid`
- `cloudrun_proxy_out.log`
- `cloudrun_proxy_err.log`
- `cloudrun_proxy.pid`
