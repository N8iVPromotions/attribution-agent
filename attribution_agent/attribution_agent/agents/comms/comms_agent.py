"""
agents/comms/comms_agent.py
----------------------------
Takes an InsightReport and emails it to the client.

Provider selection (COMMS_PROVIDER env var):
  sendgrid  — transactional ESP; requires SENDGRID_API_KEY (default)
  gmail     — Gmail SMTP fallback; requires GMAIL_SENDER + GMAIL_APP_PASSWORD

Run standalone:
    python agents/comms/comms_agent.py --client demo_client --to client@example.com

Or import and call send_report(report, recipient_email) from a flow.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

try:
    _root = str(Path(__file__).parent.parent.parent)
except NameError:
    import inspect as _inspect

    _root = str(Path(_inspect.getfile(_inspect.currentframe())).parent.parent.parent)
sys.path.insert(0, _root)

from config.agency_config import AgencyConfig
from agents.insight.insight_agent import InsightReport, generate_insight_report
from attribution_models import ATTRIBUTION_MODEL_LABELS


# ─── HTML EMAIL TEMPLATE ──────────────────────────────────────


def _build_html(
    report: InsightReport,
    powerbi_url: str = "",
    agency_config: AgencyConfig | None = None,
) -> str:
    findings_html = "".join(f"<li>{f}</li>" for f in report.key_findings)

    narrative_html = report.narrative.replace("\n\n", "</p><p>").replace("\n", "<br>")

    effective_powerbi_url = powerbi_url or (
        agency_config.powerbi_workspace_url if agency_config else ""
    )
    powerbi_section = ""
    if effective_powerbi_url:
        powerbi_section = f"""
        <div class="cta">
            <a href="{effective_powerbi_url}" class="btn">View Live Dashboard →</a>
        </div>
        """

    roi_display = f"{report.overall_roi:.1f}x" if report.overall_roi else "N/A"
    spend_display = f"${report.total_spend:,.0f}" if report.total_spend else "$0"
    model_label = ATTRIBUTION_MODEL_LABELS.get(
        report.attribution_model, report.attribution_model
    )

    header_bg = f"#{agency_config.brand_color}" if agency_config else "#1a1a1a"
    header_label = (
        agency_config.sender_name
        if agency_config and agency_config.sender_name
        else "Attribution Report"
    )
    logo_html = (
        f'<img src="{agency_config.brand_logo_url}" '
        f'style="max-height:40px; margin-bottom:12px; display:block;"><br>'
        if agency_config and agency_config.brand_logo_url
        else ""
    )
    footer_text = (
        f"This report is delivered by {agency_config.agency_name}.<br>"
        "Questions? Reply to this email."
        if agency_config
        else f"This report was automatically generated on {report.generated_at[:10]}"
        " by your Attribution Agent.<br>Questions? Reply to this email."
    )

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: 'Georgia', serif;
        background: #f4f4f0;
        color: #1a1a1a;
        padding: 40px 20px;
    }}
    .container {{
        max-width: 680px;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 4px;
        overflow: hidden;
        box-shadow: 0 2px 20px rgba(0,0,0,0.08);
    }}
    .header {{
        background: {header_bg};
        padding: 40px;
        color: white;
    }}
    .header .label {{
        font-family: 'Courier New', monospace;
        font-size: 11px;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: #888;
        margin-bottom: 12px;
    }}
    .header h1 {{
        font-size: 26px;
        font-weight: normal;
        letter-spacing: -0.5px;
        margin-bottom: 6px;
    }}
    .header .month {{
        color: #aaa;
        font-size: 14px;
    }}
    .metrics {{
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        border-bottom: 1px solid #eee;
    }}
    .metric {{
        padding: 28px 24px;
        border-right: 1px solid #eee;
        text-align: center;
    }}
    .metric:last-child {{ border-right: none; }}
    .metric .value {{
        font-size: 28px;
        font-weight: bold;
        color: #1a1a1a;
        display: block;
        margin-bottom: 4px;
    }}
    .metric .label {{
        font-size: 11px;
        color: #999;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    .body {{
        padding: 40px;
    }}
    .body p {{
        font-size: 15px;
        line-height: 1.8;
        color: #333;
        margin-bottom: 16px;
    }}
    .section-title {{
        font-family: 'Courier New', monospace;
        font-size: 11px;
        letter-spacing: 2px;
        text-transform: uppercase;
        color: #999;
        margin: 32px 0 16px;
        padding-bottom: 8px;
        border-bottom: 1px solid #eee;
    }}
    .findings {{
        list-style: none;
        padding: 0;
    }}
    .findings li {{
        padding: 10px 0 10px 20px;
        border-bottom: 1px solid #f5f5f5;
        font-size: 14px;
        line-height: 1.6;
        color: #333;
        position: relative;
    }}
    .findings li:before {{
        content: "→";
        position: absolute;
        left: 0;
        color: #1a1a1a;
        font-weight: bold;
    }}
    .cta {{
        text-align: center;
        margin: 32px 0;
    }}
    .btn {{
        display: inline-block;
        background: #1a1a1a;
        color: white;
        padding: 14px 32px;
        text-decoration: none;
        font-size: 13px;
        letter-spacing: 1px;
        border-radius: 2px;
    }}
    .footer {{
        background: #f9f9f7;
        padding: 24px 40px;
        font-size: 12px;
        color: #aaa;
        border-top: 1px solid #eee;
        line-height: 1.6;
    }}
    .top-channel {{
        display: inline-block;
        background: #f0f0ec;
        padding: 6px 14px;
        border-radius: 2px;
        font-size: 13px;
        font-weight: bold;
        color: #1a1a1a;
        margin-bottom: 24px;
    }}
</style>
</head>
<body>
<div class="container">

    <div class="header">
        {logo_html}<div class="label">{header_label}</div>
        <h1>{report.client_name}</h1>
        <div class="month">{report.report_month}</div>
    </div>

    <div class="metrics">
        <div class="metric">
            <span class="value">${report.total_pipeline:,.0f}</span>
            <span class="label">Pipeline Value</span>
        </div>
        <div class="metric">
            <span class="value">{spend_display}</span>
            <span class="label">Ad Spend</span>
        </div>
        <div class="metric">
            <span class="value">{roi_display}</span>
            <span class="label">ROI</span>
        </div>
    </div>

    <div class="body">

        <div class="section-title">Top Channel</div>
        <span class="top-channel">↑ {report.top_channel}</span>

        <div class="section-title">Attribution Model</div>
        <p>{model_label}</p>

        <div class="section-title">Executive Summary</div>
        <p>{narrative_html}</p>

        <div class="section-title">Key Findings</div>
        <ul class="findings">
            {findings_html}
        </ul>

        {powerbi_section}

    </div>

    <div class="footer">
        {footer_text}
    </div>

</div>
</body>
</html>
"""


# ─── EMAIL SENDER ─────────────────────────────────────────────


def _send_via_sendgrid(
    subject: str,
    sender_email: str,
    sender_display: str,
    recipient_email: str,
    plain_text: str,
    html_content: str,
    reply_to: str = "",
) -> None:
    """Send via SendGrid transactional API. Raises on failure."""
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Content, ReplyTo

    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        raise ValueError("SENDGRID_API_KEY not set — cannot use SendGrid provider")

    message = Mail(
        from_email=Email(sender_email, sender_display),
        to_emails=To(recipient_email),
        subject=subject,
    )
    message.add_content(Content("text/plain", plain_text))
    message.add_content(Content("text/html", html_content))
    if reply_to:
        message.reply_to = ReplyTo(reply_to)

    sg = SendGridAPIClient(api_key)
    response = sg.send(message)

    if response.status_code not in (200, 202):
        raise RuntimeError(
            f"SendGrid returned HTTP {response.status_code}: {response.body}"
        )
    logger.info(f"[Comms] SendGrid accepted delivery to {recipient_email}")


def _send_via_gmail(
    subject: str,
    sender_email: str,
    sender_display: str,
    recipient_email: str,
    plain_text: str,
    html_content: str,
    reply_to: str = "",
) -> None:
    """Send via Gmail SMTP. Raises on failure."""
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not app_password:
        raise ValueError("GMAIL_APP_PASSWORD not set — cannot use Gmail provider")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_display} <{sender_email}>"
    msg["To"] = recipient_email
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, app_password)
        smtp.sendmail(sender_email, recipient_email, msg.as_string())
    logger.info(f"[Comms] Gmail delivered to {recipient_email}")


def send_report(
    report: InsightReport,
    recipient_email: str,
    powerbi_url: str = "",
    agency_config: AgencyConfig | None = None,
) -> bool:
    """
    Send the InsightReport as an HTML email.
    Provider is controlled by COMMS_PROVIDER env var (sendgrid|gmail).
    When agency_config is provided, uses agency branding and sender details.
    Returns True if sent successfully.
    """
    provider = os.environ.get("COMMS_PROVIDER", "sendgrid").lower()

    sender_email = (
        agency_config.sender_email
        if agency_config and agency_config.sender_email
        else os.environ.get("GMAIL_SENDER", "")
    )
    if not sender_email:
        raise ValueError(
            "Sender email not configured (GMAIL_SENDER or agency_config.sender_email)"
        )

    sender_display = (
        agency_config.sender_name
        if agency_config and agency_config.sender_name
        else "Attribution Agent"
    )
    reply_to = (
        agency_config.reply_to if agency_config and agency_config.reply_to else ""
    )
    subject = f"Attribution Report — {report.client_name} — {report.report_month}"

    plain_text = (
        f"Attribution Report | {report.client_name} | {report.report_month}\n\n"
        f"{report.narrative}\n\n"
        f"Key Findings:\n"
        + "\n".join(f"• {f}" for f in report.key_findings)
        + f"\n\nTop Channel:    {report.top_channel}\n"
        f"Model:          {ATTRIBUTION_MODEL_LABELS.get(report.attribution_model, report.attribution_model)}\n"
        f"Pipeline Value: ${report.total_pipeline:,.0f}\n"
        f"Ad Spend:       ${report.total_spend:,.0f}\n"
        f"ROI:            {report.overall_roi:.1f}x\n\n"
        "---\nPowered by N8iV Promotions."
    )
    html_content = _build_html(report, powerbi_url, agency_config)

    logger.info(f"[Comms] Sending report to {recipient_email} via {provider}...")

    if provider == "sendgrid":
        _send_via_sendgrid(
            subject,
            sender_email,
            sender_display,
            recipient_email,
            plain_text,
            html_content,
            reply_to,
        )
    else:
        _send_via_gmail(
            subject,
            sender_email,
            sender_display,
            recipient_email,
            plain_text,
            html_content,
            reply_to,
        )

    logger.info(f"[Comms] Report delivered to {recipient_email}")
    return True


# ─── FULL PIPELINE ────────────────────────────────────────────


def run_full_pipeline(
    client_id: str,
    recipient_email: str,
    powerbi_url: str = "",
    attribution_model: str | None = None,
) -> dict:
    """
    End-to-end: generate insight report + send email.
    This is what the monthly Databricks Job calls.
    """
    logger.info(f"[Comms] Running full pipeline for {client_id}")

    # 1. Generate insight report
    report = generate_insight_report(
        client_id=client_id,
        attribution_model=attribution_model,
    )

    # 2. Send email
    sent = send_report(
        report=report,
        recipient_email=recipient_email,
        powerbi_url=powerbi_url,
    )

    return {
        "client_id": client_id,
        "report_month": report.report_month,
        "recipient": recipient_email,
        "email_sent": sent,
        "top_channel": report.top_channel,
        "total_pipeline": report.total_pipeline,
        "attribution_model": report.attribution_model,
    }


# ─── AGENCY CONVENIENCE WRAPPER ──────────────────────────────


def send_agency_report(
    report: InsightReport,
    recipient_email: str,
    agency_config: AgencyConfig,
    powerbi_url: str = "",
) -> bool:
    """Send a white-labeled report under agency branding."""
    return send_report(
        report=report,
        recipient_email=recipient_email,
        powerbi_url=powerbi_url,
        agency_config=agency_config,
    )


# ─── CLI ENTRYPOINT ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=str, required=True)
    parser.add_argument("--to", type=str, required=True, help="Recipient email address")
    parser.add_argument(
        "--powerbi", type=str, default="", help="Power BI dashboard URL (optional)"
    )
    parser.add_argument("--attribution-model", type=str, default=None)
    args = parser.parse_args()

    result = run_full_pipeline(
        client_id=args.client,
        recipient_email=args.to,
        powerbi_url=args.powerbi,
        attribution_model=args.attribution_model,
    )

    print(f"\nReport sent to {result['recipient']}")
    print(f"  Month:    {result['report_month']}")
    print(f"  Pipeline: ${result['total_pipeline']:,.0f}")
    print(f"  Channel:  {result['top_channel']}")
