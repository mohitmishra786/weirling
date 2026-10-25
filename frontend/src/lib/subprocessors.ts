export interface Subprocessor {
  service: string;
  usedFor: string;
  dataSent: string;
  location: string;
  termsNotes: string;
}

// Built from docs/THIRD_PARTY_SERVICES.md (#182).
// Tests in frontend/tests/subprocessors-page.test.tsx verify synchronization.
export const SUBPROCESSORS: Subprocessor[] = [
  {
    service: "Groq (Llama 3.3 70B / 3.1 8B)",
    usedFor: "Summarization, topic extraction",
    dataSent: "Article title + text",
    location: "United States",
    termsNotes:
      "Content policy prohibits certain content classes; the digest/summarization use is compliant. No cold email. Summaries are cached to limit calls.",
  },
  {
    service: "OpenAI (optional fallback)",
    usedFor: "Summarization when OPENAI_FALLBACK_ENABLED=true",
    dataSent: "Article title + text",
    location: "United States",
    termsNotes: "Same content-policy considerations as Groq. Disabled by default.",
  },
  {
    service: "Resend",
    usedFor: "Digest and account email delivery (hosted)",
    dataSent: "Recipient email + email content",
    location: "United States",
    termsNotes:
      "Transactional/user-requested email only: no cold or bulk marketing. Include a working unsubscribe (implemented) and, for bulk sending, a physical address and SPF/DKIM/DMARC on a sending subdomain.",
  },
  {
    service: "Lemon Squeezy / Paddle",
    usedFor: "Merchant of record & subscription billing (hosted)",
    dataSent: "Customer email, country, billing token",
    location: "United States / UK",
    termsNotes:
      "Merchant of record processes payment data directly; card details never touch Weirling servers.",
  },
  {
    service: "Browserless / Chrome",
    usedFor: "Headless rendering of JS-heavy pages",
    dataSent: "Target URL",
    location: "Operator host",
    termsNotes:
      "Runs in the operator's own container. Fetching respects robots.txt and the honest-bot posture.",
  },
];
