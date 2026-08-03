# DevOps Handoff Checklist

- Create RDS PostgreSQL and provide `DATABASE_URL` through Secrets Manager.
- Create a private S3 bucket with public access blocked.
- Create SQS queue and DLQ for email sync jobs.
- Create ECR repository and image push permissions.
- Configure ECS Fargate web task on port `8501`.
- Configure ALB health path `/_stcore/health`.
- Configure ECS worker command `python -m workers.email_sync_worker`.
- Inject environment variables from `.env.example`; do not bake secrets into the image.
- Run `alembic upgrade head` before first production start.
- Provide `TOKEN_CACHE_ENCRYPTION_KEY` through Secrets Manager.
- Configure CloudWatch logs and alarms for app errors, worker failures, queue age, RDS CPU/storage, and health-check failures.
- Confirm Microsoft Graph permissions are `User.Read` and `Mail.Read`.
- Update Azure redirect URI after the production HTTPS URL is available.
