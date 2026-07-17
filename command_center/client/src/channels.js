// Fixed categorical order + colors, validated for the light lavender surface
// (see command_center/README.md). Color follows the channel, never its rank.

export const CHANNEL_ORDER = ["meta", "google_ads", "linkedin_ads", "tiktok_ads"];

export const CHANNELS = {
  meta: { label: "Meta Ads", color: "var(--ch-meta)", hex: "#7a63ff" },
  google_ads: { label: "Google Ads", color: "var(--ch-google)", hex: "#c4457e" },
  linkedin_ads: { label: "LinkedIn Ads", color: "var(--ch-linkedin)", hex: "#b26a00" },
  tiktok_ads: { label: "TikTok Ads", color: "var(--ch-tiktok)", hex: "#0f8a66" },
};

export const channelLabel = (id) => CHANNELS[id]?.label ?? id;
export const channelHex = (id) => CHANNELS[id]?.hex ?? "#8d87a3";

// Sources that appear on client cards (crm/revenue verification, not series)
export const SOURCE_LABELS = {
  hubspot: "HubSpot CRM",
  stripe: "Stripe revenue",
};
