"""
outreach_agent.py — Personalized cold-email sequence generator
----------------------------------------------------------------
Generates a 3-email outreach sequence for a prospect via Claude and
optionally sends it as a draft to the operator's own inbox via SMTP.
"""
import json
import os
import smtplib
from email.mime.text import MIMEText

import requests

PROSPECTS = [
    {"id": 1,  "name": "Alani Skin MD",                       "contact": "Practice Manager", "email": "info@alaniskinmd.com",                      "industry": "Med Spa",     "phone": "480-993-2218", "notes": "783 reviews, 4.8★. High-volume laser and injectables practice. Likely running Meta ads for patient acquisition."},
    {"id": 2,  "name": "Dolce Medical Spa",                   "contact": "Owner",            "email": "info@dolcemedicalspas.com",                  "industry": "Med Spa",     "phone": "480-470-6737", "notes": "562 reviews, 5.0★. Premium med spa open 6 days. High volume, active marketing presence."},
    {"id": 3,  "name": "Dentistry of Old Town Scottsdale",    "contact": "Dr. Brittain",     "email": "info@dentistryofoldtownscottsdale.com",      "industry": "Dental",      "phone": "480-719-6994", "notes": "955 reviews, 4.8★. Highest review count in area. Cosmetic dental = high ticket. Almost certainly running ads."},
    {"id": 4,  "name": "The Perfect Secret Med Spa",          "contact": "Irena",            "email": "info@itstheperfectsecret.com",               "industry": "Med Spa",     "phone": "480-647-8991", "notes": "545 reviews, 5.0★. Owner-operated boutique. Irena personally greets and tours clients. Warm, relationship-driven."},
    {"id": 5,  "name": "Advanced Dentistry",                  "contact": "Dr. Oh",           "email": "info@advdentistry.com",                      "industry": "Dental",      "phone": "480-945-4700", "notes": "304 reviews, 4.9★. Reviews mention impressive technology. Owner open to new tools."},
    {"id": 6,  "name": "BODI Fitness",                        "contact": "Owner",            "email": "info@scottsdalebodi.com",                    "industry": "Fitness",     "phone": "480-444-8062", "notes": "177 reviews, 4.8★. Boutique fitness studio. Membership model = LTV attribution is high value."},
    {"id": 7,  "name": "Scottsdale Med Spa",                  "contact": "Melissa",          "email": "info@scottsdalemedspa.beauty",               "industry": "Med Spa",     "phone": "480-454-6959", "notes": "143 reviews, 4.9★. Smaller boutique. Each patient matters more — N8iV Intelligence ROI story hits harder."},
    {"id": 8,  "name": "Pvolve Scottsdale Waterfront",        "contact": "Studio Manager",   "email": "info@studios.pvolve.com",                    "industry": "Fitness",     "phone": "480-847-2455", "notes": "National franchise. Local vs. network attribution gap is the pitch."},
    {"id": 9,  "name": "Kelly Jones Luxury Real Estate",      "contact": "Kelly Jones",      "email": "info@kellyfjones.com",                       "industry": "Real Estate", "phone": "480-399-9322", "notes": "4.9★. Luxury realtor at Camelback. Real estate + Meta lead gen = textbook N8iV Intelligence use case."},
    {"id": 10, "name": "Patricia Garrity Luxury Real Estate", "contact": "Patricia Garrity", "email": "info@patriciagarrityhomes.com",               "industry": "Real Estate", "phone": "602-942-7520", "notes": "28 reviews, 5.0★. Reviews mention exceptional marketing of listings. Same N8iV Intelligence pitch as Kelly Jones."},
]

INDUSTRY_ANGLES = {
    "Med Spa":     {"pain": "cost per closed patient vs. cost per lead",              "metric": "booked appointments and treatment revenue",    "angle": "every campaign you run should trace back to a treatment chair — not just a form fill"},
    "Dental":      {"pain": "cost per closed treatment vs. cost per new patient call", "metric": "closed treatment revenue by campaign",         "angle": "a crown case is worth $3,000 — knowing which ad drove that patient changes everything"},
    "Fitness":     {"pain": "cost per retained member vs. cost per trial signup",      "metric": "membership LTV by acquisition channel",        "angle": "the campaign that brings members who stay 12 months is worth 10x the campaign that brings 30-day cancellations"},
    "Real Estate": {"pain": "cost per closed transaction vs. cost per lead",           "metric": "closed transaction revenue by campaign",       "angle": "one closed deal can mean $30,000+ in commission — knowing which ad drove it changes every budget decision"},
}


def generate_email_sequence(prospect: dict) -> dict:
    """Generate a personalized 3-email outreach sequence via Claude."""
    angle = INDUSTRY_ANGLES.get(prospect["industry"], INDUSTRY_ANGLES["Med Spa"])

    prompt = f"""You are writing outreach emails for Zajen, founder of N8iV Intelligence by N8iV PROMOTIONS — a closed-loop marketing attribution service.

ABOUT ZAJEN & LOOP:
- N8iV Intelligence automatically connects Meta ad spend to HubSpot CRM deals
- It pulls data, runs an attribution SQL model, generates a detailed report, and delivers it to the client inbox on the 1st of every month
- Clients get the answer: which campaigns drove closed revenue, where budget is wasted, what to do next month
- No dashboard to log into. Just the answer delivered automatically.
- Pricing: $1,500 setup fee + $750/month (Starter tier)

ZAJEN'S VOICE RULES:
- Warm but precise. Like explaining something to a smart friend over coffee.
- Short sentences. Specific details. No corporate jargon.
- Never say: excited to share, game changer, leverage, journey, deep dive, synergy
- Always end with one open question or a soft CTA for a 15-minute call
- Lead with curiosity, never pitch in the first line
- Reference something specific about their business

PROSPECT:
- Business: {prospect['name']}
- Contact: {prospect['contact']}
- Industry: {prospect['industry']}
- Key notes: {prospect['notes']}
- Industry pain point: {angle['pain']}
- What N8iV Intelligence shows them: {angle['metric']}
- The angle that resonates: {angle['angle']}

Generate exactly 3 emails as a JSON object with this structure. Respond ONLY with valid JSON, no markdown, no preamble:
{{
  "email1": {{
    "subject": "...",
    "body": "...",
    "day": "Day 1 — First Touch"
  }},
  "email2": {{
    "subject": "...",
    "body": "...",
    "day": "Day 5 — Follow-up 1"
  }},
  "email3": {{
    "subject": "...",
    "body": "...",
    "day": "Day 12 — Follow-up 2"
  }}
}}

RULES FOR EACH EMAIL:

Email 1 (First Touch):
- Open with a specific observation about their business (use the notes)
- Ask ONE qualifying question about how they currently measure ad performance
- Never mention N8iV Intelligence by name in email 1
- End with: "Would it be worth a 15-minute call to show you what that looks like?"
- Sign off: "Zajen\\nN8iV PROMOTIONS\\nzajen@n8ivpromotions.com"
- Max 180 words

Email 2 (Day 5 Follow-up — no reply):
- Reference the first email briefly without being pushy
- Add one new data point or insight relevant to their industry
- End with a different open question than email 1
- Keep it under 120 words
- Sign off: "Zajen\\nzajen@n8ivpromotions.com"

Email 3 (Day 12 Follow-up — still no reply):
- Short, direct, no pressure
- The "last touch" — mention you will not follow up again
- Leave the door open with something genuinely useful (a thought, an insight, a question they can sit with)
- Under 80 words
- Sign off: "Zajen"
"""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    response.raise_for_status()

    text = response.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def send_draft_to_self(email: dict, prospect: dict, sender: str, app_password: str) -> None:
    """Send a draft email to the operator's own inbox for review before forwarding."""
    subject = f"[DRAFT → {prospect['name']}] {email['subject']}"
    body = (
        f"PROSPECT: {prospect['name']} ({prospect['industry']})\n"
        f"TO: {prospect['email']}\n"
        f"SEND: {email.get('day', '')}\n"
        f"{'─' * 60}\n\n"
        f"{email['body']}"
    )

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = sender

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, sender, msg.as_string())
