# PrepPilotAI – Serverless AI Meal Planning API

PrepPilotAI is a **serverless REST API** built on AWS that generates structured daily meal plans using OpenAI GPT models.  
The application demonstrates real-world cloud engineering practices including Infrastructure-as-Code, secure secret management, and resilient API design.

---

## What This Project Does

- Accepts a JSON request containing calorie targets, protein goals, food dislikes, and budget
- Generates a structured daily meal plan using OpenAI
- Securely stores requests and responses in DynamoDB
- Returns clean JSON output via an HTTP API
- Gracefully handles external API rate limits using fallback logic

---

## Architecture Overview

**Client → API Gateway → AWS Lambda → OpenAI API → DynamoDB**

### AWS Services Used
- **API Gateway (HTTP API)** – Public REST endpoint
- **AWS Lambda (Python)** – Serverless backend logic
- **DynamoDB** – NoSQL storage for generated meal plans
- **SSM Parameter Store** – Secure storage of OpenAI API key
- **IAM** – Least-privilege access control
- **Terraform** – Infrastructure-as-Code

---

## Security & Best Practices

- OpenAI API key stored as a **SecureString** in SSM Parameter Store
- No secrets hard-coded in source code
- IAM roles scoped to minimum required permissions
- Fully serverless (no servers, no credentials on disk)
- Error handling and logging via CloudWatch

---

## Intelligent Fallback Behavior

If OpenAI returns an HTTP **429 (rate limit)** or other errors:
- The API **does not fail**
- A structured fallback meal plan is returned
- The response is still logged to DynamoDB
- The service remains available for demos and testing

This mirrors real production reliability patterns.

---

## Example API Request

```json
POST /generate
Content-Type: application/json

{
  "calories": 2000,
  "protein_g": 200,
  "dislikes": ["pickles", "mayo"],
  "budget_per_day_usd": 7
}
