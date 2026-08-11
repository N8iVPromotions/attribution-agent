# ARIE Command Center

The only supported ARIE Command Center UI is the Vercel app in `arie-portal/`.

## Production URL

```text
https://arie-command-center.vercel.app
```

## Local Development

```powershell
cd C:\Users\zajen\attribution-agent\arie-portal
npm.cmd install
npm.cmd run dev
```

Then open the local Next.js URL printed by the dev server.

## Authentication

The Vercel app uses server-side Basic Auth for the internal pilot release:

- `ARIE_BASIC_AUTH_USER`
- `ARIE_BASIC_AUTH_PASSWORD`

Production fails closed if either value is missing.

## Backend Dependencies

The browser never receives Databricks, GCP, or ARIE API credentials. Server-side
routes use:

- Databricks SQL for command center data.
- GCP Workload Identity Federation for Cloud Run job execution.
- Optional FastAPI control API access for approval mutations.

Required production variables are documented in `arie-portal/README.md`.

## Removed Paths

The old Streamlit, Replit, and static prototype command centers are retired.
Do not use local desktop launcher scripts or Cloud Run `attribution-ui` as
operator surfaces.
