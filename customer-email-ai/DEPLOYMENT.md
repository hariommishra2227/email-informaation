# AWS Deployment

Recommended path: User -> HTTPS -> App Runner or ECS Fargate web app -> RDS PostgreSQL. Long email processing should use Web app -> SQS -> worker -> Microsoft Graph -> RDS PostgreSQL -> private S3.

Required services: ECR, App Runner or ECS Fargate, RDS PostgreSQL, private S3, SQS with DLQ, Secrets Manager, CloudWatch logs/alarms, ACM HTTPS certificate, VPC and security groups.

Secrets Manager values: `DATABASE_URL`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`, and any future token-encryption key.

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
7. Deploy worker tasks when `JOB_BACKEND=sqs`.

Rollback: redeploy the previous image. Only run `alembic downgrade -1` after confirming the migration is backward-compatible.

Starting capacity for 100,000+ emails: 1 web task, 1 worker task, RDS db.t4g.medium or larger, RDS storage autoscaling, `EMAIL_FETCH_BATCH_SIZE=250`, `EMAIL_PROCESS_BATCH_SIZE=100`.

Azure redirect URI action: after AWS gives the final HTTPS URL, add that exact URL to the Microsoft Entra app redirect URIs.
