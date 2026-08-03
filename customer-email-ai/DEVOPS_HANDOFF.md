# DevOps Handoff Checklist

- Create RDS PostgreSQL and provide `DATABASE_URL` through Secrets Manager.
- Create a private S3 bucket with public access blocked.
- Create SQS queue and DLQ for email sync jobs.
- Create ECR repository and image push permissions.
- Configure App Runner or ECS Fargate on port `8501`.
- Inject environment variables from `.env.example`; do not bake secrets into the image.
- Run `alembic upgrade head` before first production start.
- Configure CloudWatch logs and alarms for app errors, worker failures, queue age, RDS CPU/storage, and health-check failures.
- Confirm Microsoft Graph permissions are `User.Read` and `Mail.Read`.
- Update Azure redirect URI after the production HTTPS URL is available.
