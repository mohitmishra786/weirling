# Data Breach Incident Response Runbook

*Template: needs counsel review. Adapt to your jurisdiction, operational structure, and applicable data protection regulations before adopting.*

This runbook establishes the operational procedure for detecting, containing, assessing, and notifying stakeholders of personal data breaches affecting Weirling instances.

---

## 1. Overview & Statutory Timelines

When a security incident involves unauthorized access, disclosure, alteration, loss, or destruction of personal data (e.g., user account credentials, reading telemetry, forwarded newsletters, API tokens, or database backups), the following notification statutory deadlines apply:

| Authority / Stakeholder | Statutory Deadline | Legal Basis / Jurisdiction | Channel / Contact |
|---|---|---|---|
| **Data Protection Board of India (DPDP Board)** | *Without delay* upon discovering the breach | Digital Personal Data Protection Act (DPDP Act) §8(6) & DPDP Rules | Official DPDP Board Reporting Portal / [dpdp-board-portal-placeholder] |
| **CERT-In (Indian Computer Emergency Response Team)** | Within *6 hours* of notice / occurrence | Information Technology Act §70B / CERT-In Directions 2022 | incident@cert-in.org.in / +91-1800-11-4949 |
| **Lead EU Supervisory Authority (DPA)** | Within *72 hours* of becoming aware | GDPR Article 33 | Designated Lead DPA online breach notification portal |
| **Affected Users / Data Subjects** | *Without undue delay* (when high risk to individuals) | GDPR Article 34 & DPDP Act §8(6) | In-app notification and email to registered user address |
| **Enterprise Controllers (for hosted SaaS)** | Within *24 to 48 hours* (per customer DPA) | Article 28 DPA contractual commitment | Controller designated security contact |

---

## 2. Step-by-Step Response Phases

### Phase 1: Detection, Triage & Incident Declaration

1. **Verify the report:** Confirm whether personal data or production credentials are implicated.
2. **Assign Incident Roles:**
   - **Incident Commander (IC):** Leads coordination, decision-making, and timeline tracking.
   - **Technical Lead:** Investigates root cause, system logs, and containment measures.
   - **Communications & Legal Lead:** Manages counsel communication, regulatory draft notifications, and user notices.
   - **Evidence Custodian:** Maintains the evidence log and preserves forensic artifacts.
3. **Start the Clock:** Record the exact UTC timestamp when the instance team became aware of the breach.

### Phase 2: Containment & Immediate Mitigation

Do not delete or overwrite evidence during containment. Preserve logs before restarting or redeploying containers.

1. **Isolate compromised credentials:**
   - Rotate database passwords (`DATABASE_URL`, `POSTGRES_PASSWORD`).
   - Rotate application encryption keys (`SECRET_KEY`, `REFRESH_SECRET_KEY`, `SESSION_ENCRYPTION_KEY`).
   - Invalidate all active Redis refresh sessions (`cache_delete("refresh:*")`) and API tokens (`UPDATE api_tokens SET revoked = true`).
   - Rotate external third-party API keys (Groq, OpenAI, Resend, the SMTP mailbox password, Cloudflare, Lemon Squeezy / Paddle).
2. **Network Isolation:**
   - Terminate suspicious ingress connections and restrict database access to localhost / private docker network only.
   - If a crawler SSRF vulnerability is exploited, disable rendered extraction (`BROWSERLESS_HOSTED_ALLOWED=false`).
3. **Forensic Preservation:**
   - Take snapshot copies of database volumes and system logs (`docker compose logs > breach-logs.txt`).
   - Record SHA-256 hashes of preserved log bundles into the Evidence Log.

### Phase 3: Assessment & Risk Evaluation

Assess the scope, nature, and severity of the compromised data:

1. **Identify Data Categories Involved:**
   - Identity data: Email addresses, display names, bcrypt password hashes.
   - Private user content: Forwarded newsletters (`content_items` with `owner_user_id`), private RSS feeds.
   - Behavioral data: Reading history, scroll depth, active reading duration, topic weights, interest nodes.
   - Billing & payment: Customer IDs, subscription state (note: credit cards are handled exclusively by Lemon Squeezy / Paddle and never touch Weirling).
2. **Determine Exposure Volume:**
   - Query access logs to determine exact count of affected `user_id` records.
   - Determine whether data was exfiltrated or merely viewed/indexed.
3. **Risk Scoring:**
   - High risk: Plaintext credentials leaked, private forwarded emails exposed, or unauthorized exfiltration of reading profiles.
   - Low risk: Encrypted tokens rotated before decryption, or transient unauthorized probe without data extraction.

### Phase 4: Regulatory & Stakeholder Notifications

If assessment confirms compromise of personal data:

1. **Notify Regulators:**
   - **DPDP Board (India):** File formal intimation *without delay* containing the incident description, affected data categories, mitigation actions, and contact details.
   - **GDPR DPA:** Submit initial notification within *72 hours*. If full investigation is incomplete, submit a phased notification providing available details and indicating follow-up timing.
2. **Notify Affected Users:**
   - Send transparent, jargon-free email notifications to all confirmed affected users.
   - Force session reset and password change on next sign-in if credentials or password hashes were exposed.

### Phase 5: Post-Incident Remediation & Closure

1. Fix underlying vulnerability (code patch, dependency upgrade, or firewall rule update).
2. Verify all rotated credentials and run complete test suite and security audit.
3. Hold formal incident post-mortem within 5 business days and archive evidence log for statutory retention (minimum 3 to 5 years).

---

## 3. Evidence Log Table

The Evidence Custodian must record every forensic action and artifact in the following format:

| Timestamp (UTC) | Event / Action Taken | System / Artifact Involved | Performed By | Outcome / Finding | Evidence Hash (SHA-256) |
|---|---|---|---|---|---|
| 2026-XX-XX 00:00:00 | Incident detected via alerting webhook | PostgreSQL access logs | [Name / Role] | Unusual export query detected on `users` table | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 2026-XX-XX 00:15:00 | Immediate credential rotation executed | App config / Docker secrets | [Name / Role] | Database and API keys rotated; sessions revoked | `...` |
| 2026-XX-XX 01:00:00 | Log dump extracted and preserved | `/var/log/readprism/` | [Name / Role] | 42 MB gzip archive saved to offline vault | `...` |
| 2026-XX-XX 04:00:00 | DPDP Board initial intimation filed | DPDP Board Portal | [Legal Lead] | Confirmation receipt ID: [REC-XXXXX] | `...` |
| 2026-XX-XX 12:00:00 | User notifications transmitted | Resend / Postmark SMTP | [Comms Lead] | Emails delivered to [N] affected accounts | `...` |

---

## 4. Notification Message Templates

### Template A: Regulatory Authority Notification (DPDP Board / GDPR DPA)

```text
To: [Data Protection Board of India / Lead Supervisory Authority]
Date: [Date and Time UTC]
Subject: Formal Notification of Personal Data Incident: [Operator / Service Entity Name]

1. Contact Details of Data Fiduciary / Controller:
   - Organization: [Legal Entity Name]
   - Address: [Registered Address]
   - Contact Person / Grievance Officer: [Name, Title, Email, Phone]

2. Nature and Circumstances of the Incident:
   - Date and time incident occurred: [Timestamp UTC]
   - Date and time incident detected: [Timestamp UTC]
   - Description: [Brief factual summary of unauthorized access or exposure]
   - Root cause: [e.g. Exploited web vulnerability, unauthorized credential use, misconfiguration]

3. Categories and Number of Data Subjects and Records Affected:
   - Number of individuals affected: [Approximate or exact count]
   - Categories of personal data: [e.g. Email addresses, reading interaction telemetry, source lists]
   - Special / sensitive personal data involved: [None / Specify if applicable]

4. Likely Consequences and Risks:
   - Potential impact on individuals: [e.g. Risk of unsolicited communication, exposure of reading topics]
   - Assessed severity: [Low / Moderate / High]

5. Remedial and Mitigation Measures Taken or Proposed:
   - Containment actions: [e.g. Credentials rotated, affected endpoints blocked, sessions terminated]
   - Steps to assist affected individuals: [e.g. Forced password reset, direct user advisory]
   - Long-term remediation: [e.g. Infrastructure hardening, code vulnerability remediation]

6. Communication to Affected Individuals:
   - Status: [Communicated on (Date) / In progress / Not deemed high risk (rationale)]

Submitted by:
[Name and Title]
[Legal Entity Name]
```

### Template B: Affected User Advisory Email

```text
Subject: Important security notice regarding your Weirling account

Dear [Name / Reader],

We are writing to inform you of a security incident that may have affected information associated with your Weirling account on [Date / Time UTC].

What happened:
On [Date], we identified [brief plain-language description: e.g. unauthorized access to a database backup / a vulnerability allowing unauthorized viewing of reading profiles]. We took immediate steps to contain the issue and secured the affected systems within [X hours].

What information was involved:
Our investigation confirms that the following data associated with your account was accessed:
- [List specific categories: e.g., your email address and reading list]
- Note: Your password was stored as a salted bcrypt cryptographic hash and was not exposed in plaintext. Payment card information is handled entirely by our merchant of record ([Lemon Squeezy / Paddle]) and never touches Weirling servers.

What we have done:
- Immediately rotated all server access credentials and invalidated all active sessions.
- Applied software fixes to eliminate the vulnerability.
- Notified relevant data protection regulatory authorities as required by law.

What you should do:
- For your protection, we have signed you out of all sessions. Please sign in and choose a new password at [Login URL].
- If you use the same password on other services, we strongly recommend changing it on those accounts as well.
- Remain vigilant against phishing emails attempting to solicit sensitive information.

If you have questions or concerns, please contact our Grievance Officer directly at [GRIEVANCE_EMAIL] or visit [Contact URL].

Sincerely,
[Name / Title]
Weirling Team
```

---

## 5. Tabletop-Exercise Checklist

Run this simulation drill at least once every 12 months or prior to public multi-tenant launch.

### Exercise Preparation
- [ ] Select scenario (e.g. Scenario A: Leaked database backup; Scenario B: Crawler SSRF exfiltrating newsletter tokens; Scenario C: Compromised developer session).
- [ ] Designate simulation facilitator and participant roles (Incident Commander, Technical Investigator, Comms/Legal, Evidence Scribe).
- [ ] Establish simulated communication channel (separate from live operational Slack/Discord to avoid false alarms).

### Simulation Drill Checkpoints
- [ ] **T+00m (Detection):** Alert triggered. Incident declared; roles assigned; initial timestamp recorded.
- [ ] **T+15m (Containment):** Technical team simulates credential rotation and session invalidation; verifies rollback procedures.
- [ ] **T+45m (Assessment):** Query simulated logs to scope impacted `user_id` list and classify data categories.
- [ ] **T+90m (Regulatory Intimation):** Draft Template A completed with scenario details; legal verification of statutory reporting criteria (DPDP "without delay" vs GDPR 72h).
- [ ] **T+120m (User Advisory):** Draft Template B completed; review plain-language clarity, user action items, and support channels.
- [ ] **T+150m (Evidence Audit):** Review simulated Evidence Log for completeness, cryptographic hashes, and chain of custody.

### Post-Exercise Debrief
- [ ] Document drill duration, bottlenecks encountered, and missing log fields.
- [ ] Update runbook contacts, escalation paths, and automated forensic scripts.
- [ ] Record completion date and participant roster in compliance records.
