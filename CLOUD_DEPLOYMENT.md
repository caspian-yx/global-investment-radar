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

## 3. Optional durable history

No database secret is required for the first cloud run. Without `DATABASE_URL`, the workflow uses SQLite on the temporary GitHub runner. The HTML artifact and email survive, but the SQLite history does not.

For durable history, create a Neon Free PostgreSQL project and add the optional GitHub Actions secret `DATABASE_URL`, containing the Neon pooled connection string with TLS enabled.

Change the scheme copied from Neon from `postgresql://` to `postgresql+psycopg://` if needed by SQLAlchemy. Keep the rest of the generated connection string unchanged. The workflow installs the locked `psycopg` driver, and the application creates its current tables on first connection. This milestone intentionally does not add a migration service or database backup system.

## 4. First cloud run

1. Open the private repository's **Actions** tab.
2. Select **Daily Radar**.
3. Select **Run workflow** on the `main` branch.
4. Keep `data_mode` set to `live`.
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

- `AI_ENABLED=false`, `DAILY_LLM_BUDGET=0`, and `LLM_PROVIDER=disabled` are fixed in the workflow.
- The workflow has read-only repository permissions.
- Third-party Actions are pinned to full commit SHAs.
- `.env`, databases, reports, caches, and local work files are ignored.
- The `doctor` command reports only the database driver, never a connection string.
- Reports are Actions artifacts for 14 days and are never committed automatically.
- GitHub-hosted runners for private repositories consume the account's included Actions allowance. Set a GitHub Actions budget with **Stop usage when budget limit is reached** if a strict zero-charge boundary is required.

## 7. Required human actions remaining

- Create the private GitHub repository.
- Authenticate Git locally and push `main`.
- Create the Gmail App Password or another SMTP credential.
- Add the six required email Secrets.
- Optionally create Neon and add `DATABASE_URL`.
- Run the manual workflow and confirm both the email and artifact.

Only after the first manual cloud run succeeds should the scheduled run be considered operational.
