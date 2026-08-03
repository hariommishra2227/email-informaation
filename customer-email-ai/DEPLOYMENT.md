# AWS Deployment

Recommended architecture: ALB with ACM HTTPS -> ECS Fargate web task -> RDS PostgreSQL. Long email processing uses ECS Fargate web task -> SQS queue -> ECS Fargate worker task -> Microsoft Graph -> RDS PostgreSQL -> private S3.

Required services: ECR, ECS Fargate, ALB, ACM, RDS PostgreSQL, private S3, SQS with DLQ, Secrets Manager, CloudWatch logs/alarms, VPC private subnets and security groups.

Secrets Manager values: `DATABASE_URL`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`, `TOKEN_CACHE_ENCRYPTION_KEY`.

Container commands:

```bash
# web
streamlit run app.py --server.port=8501 --server.address=0.0.0.0

# worker
python -m workers.email_sync_worker
```

Health checks:

- ALB target group path: `/_stcore/health`
- Container diagnostic command: `python -c "from services.health import health_status; raise SystemExit(0 if health_status().get('database') == 'ok' else 1)"`
- If PostgreSQL is unavailable, the container diagnostic reports unhealthy while Streamlit may still serve its own core health path.

Migration commands:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
alembic current
alembic downgrade -1
```

Deployment:

1. Build and push the Docker image to ECR.
2. Create RDS PostgreSQL and store `DATABASE_URL` in Secrets Manager.
3. Create a private S3 bucket and SQS queue with a DLQ.
4. Grant least-privilege IAM for CloudWatch Logs, S3 object access, SQS, and Secrets Manager reads.
5. Run `alembic upgrade head` from a one-off task.
6. Deploy the web task on port `8501` and configure the health command against `services.health.health_status`.
7. Deploy worker tasks with `JOB_BACKEND=sqs`.
8. Configure SQS visibility timeout above the largest expected sync batch; start at 15 minutes and tune with CloudWatch queue age.

Rollback: redeploy the previous image. Only run `alembic downgrade -1` after confirming the migration is backward-compatible.

Starting capacity for 100,000+ emails: 1 web task, 1 worker task, RDS db.t4g.medium or larger, RDS storage autoscaling, `EMAIL_FETCH_BATCH_SIZE=250`, `EMAIL_PROCESS_BATCH_SIZE=100`.

Attachment processing is disabled by default with `ATTACHMENT_PROCESSING_ENABLED=false`; enable only after S3 bucket/IAM policy validation.

Token cache encryption: rotate `TOKEN_CACHE_ENCRYPTION_KEY` only with a planned re-encryption window. Losing the key invalidates persisted MSAL token caches and users must reconnect Outlook.

Azure redirect URI action: after AWS gives the final HTTPS URL, add that exact URL to the Microsoft Entra app redirect URIs.
