# Cloud MVP deployment checklist

The repository is prepared for a private GitHub repository. Do not make the repository public and do not commit a local `.env` file.

## 1. Create the private repository

1. In GitHub, choose **New repository**.
2. Name it `global-investment-radar`.
3. Select **Private**.
4. Do not initialize it with a README, `.gitignore`, or license because those files already exist locally.
5. Copy the repository HTTPS or SSH URL.
6. From this project directory, add and push the remote:

```bash
git remote add origin YOUR_PRIVATE_REPOSITORY_URL
git push -u origin main
```

Never put a token in the remote URL. Use GitHub's normal credential manager, SSH authentication, or `gh auth login`.

## 2. Configure email delivery

The simplest zero-extra-cost option for one personal daily report is Gmail SMTP. It reuses the existing generic SMTP adapter and requires no email SDK.

1. Use an existing Gmail account or create one.
2. Enable Google Account 2-Step Verification.
3. Create a dedicated App Password named `Global Investment Radar`.
4. Save the 16-character App Password once; do not put it in `.env`, code, issues, or workflow YAML.

In the private repository, open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `EMAIL_FROM`: the Gmail sender address.
- `EMAIL_TO`: the destination email address; it may be the same account.
- `SMTP_HOST`: `smtp.gmail.com`.
- `SMTP_PORT`: `587`.
- `SMTP_USERNAME`: the full Gmail sender address.
- `SMTP_PASSWORD`: the dedicated Google App Password, without spaces.

The workflow uses STARTTLS. A normal Google account password must not be used. App Passwords can be unavailable for some managed, security-key-only, or Advanced Protection accounts. If that applies, use a transactional SMTP provider instead.

Alternative: Resend SMTP has a free tier, but production sending requires a domain that you control and verify. Configure `smtp.resend.com`, port `587`, username `resend`, and use a Resend API key as `SMTP_PASSWORD`. This is only zero-cost if you already own a domain.

## 3. Durable audience and delivery state

The C9.2 audience source of truth must be a private PostgreSQL database shared
by the local operator and GitHub Actions. Local SQLite is for development and
tests only; production audience mode fails closed when `DATABASE_URL` is
SQLite. The runner does not import or reconstruct a local database file.

Create a Neon Free PostgreSQL project (or equivalent private PostgreSQL
service) and add the GitHub Actions secret `DATABASE_URL`, containing the
pooled connection string with TLS enabled. Configure the same value in the
operator's untracked local `.env`.

Change the scheme copied from Neon from `postgresql://` to
`postgresql+psycopg://` if needed by SQLAlchemy. Keep the rest of the generated
connection string unchanged. The workflow installs the locked `psycopg` driver;
both the local admin and runner apply additive migration 008 idempotently.
Also configure `AUDIENCE_ENABLED` only through the deployment environment,
`AUDIENCE_ADMIN_TOKEN` locally, and the SMTP secrets. Configure `OPERATOR_EMAIL`
separately when using the local operator-only **Send Test To Me** path; it is
not required for production audience fan-out itself.
Recipient addresses, notes, tokens and credentials must not be committed or
placed in workflow YAML.

Audience delivery requires both `AUDIENCE_ENABLED=true` and the explicit
`delivery_target=audience` path. The scheduled `daily.yml` path now selects
`live + ai + zh-CN + audience`; manual dispatch still defaults to
`live + no-ai + single` and requires an explicit audience choice. Delivery
reservation is at-most-once; an existing PENDING state is treated as an
unknown outcome and is not automatically retried.

## 4. First cloud run

1. Open the private repository's **Actions** tab.
2. Select **Daily Radar**.
3. Select **Run workflow** on the `main` branch.
4. For a non-audience smoke test, keep the safe manual defaults. For a
   deliberate audience test, explicitly select `live`, `ai`, `zh-CN`, and
   `audience`.
5. Open the running job and confirm the logs show:
   - safe effective configuration;
   - each provider starting and finishing;
   - document, market, macro, cluster, and event counts;
   - generated report path and SHA-256;
   - email accepted by the SMTP server;
   - final elapsed time.
6. Download the `global-investment-radar-<run id>` artifact and open the HTML report.
7. Confirm the message arrived in `EMAIL_TO`, including the spam folder on the first run.

If SMTP configuration or delivery fails, the job fails visibly. The generated report is still uploaded by the `always()` artifact step when report generation succeeded.

## 5. Schedule

The schedule is `17 22 * * *` in UTC, equivalent to 06:17 every day in Asia/Shanghai on the following calendar day. The application independently uses `BUSINESS_TIMEZONE=Asia/Shanghai`, so the report date is the Shanghai calendar date.

GitHub scheduled workflows are best effort and may be delayed. The non-zero minute avoids the busiest start-of-hour window. Scheduled workflows run from the default branch, so `daily.yml` must remain on `main`.

## 6. Security and cost controls

- Scheduled runs use `AI_ENABLED=true`, `DAILY_LLM_BUDGET=1`,
  `AI_MAX_REQUESTS_PER_RUN=10`, DeepSeek, and one optional C9.1 localization
  request. Thus the normal hard maximum is 10 structured explanation calls
  plus 1 localization call for a briefing identity; persisted localization
  snapshots reduce later runs to zero new localization calls.
- Manual defaults remain `AI_ENABLED=false`, localization off, and single delivery.
- The workflow has read-only repository permissions.
- Third-party Actions are pinned to full commit SHAs.
- `.env`, databases, reports, caches, and local work files are ignored.
- The `doctor` command reports only the database driver, never a connection string.
- Reports are Actions artifacts for 14 days and are never committed automatically.
- GitHub-hosted runners for private repositories consume the account's included Actions allowance. Set a GitHub Actions budget with **Stop usage when budget limit is reached** if a strict zero-charge boundary is required.

## 7. Required human actions remaining

- The private repository, Supabase PostgreSQL authority, GitHub secrets, and
  operator recipient are already configured for this activation.
- Add beta recipients only through the local Admin described in the repository
  README; do not edit PostgreSQL or commit audience data.
- Confirm the first scheduled audience delivery and artifact after activation.

Only after the first manual cloud run succeeds should the scheduled run be considered operational.
