# 🛡️ ClaimGuard — Cloud-Native Vehicle Insurance Claim & Damage Assessment System

[![AWS Cloud](https://img.shields.io/badge/AWS-Cloud--Native-FF9900?logo=amazon-aws&logoColor=white)](https://aws.amazon.com/)
[![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Amazon RDS](https://img.shields.io/badge/Amazon-RDS_MySQL_8.0-527FFF?logo=mysql&logoColor=white)](https://aws.amazon.com/rds/)
[![Serverless](https://img.shields.io/badge/Serverless-Lambda_+_SQS-FD5750?logo=aws-lambda&logoColor=white)](https://aws.amazon.com/lambda/)
[![Zero Trust](https://img.shields.io/badge/Security-Zero--Trust_IAM-00A870)](https://aws.amazon.com/iam/)

An enterprise-grade, cloud-native InsurTech platform designed to automate vehicular insurance claim filing, photo damage inspection, AI-driven repair estimation, fraud risk assessment, and real-time claims adjuster notification.

Built strictly according to **AWS Well-Architected Framework** principles across **11 interconnected AWS cloud services**, ClaimGuard ensures high availability, resilient asynchronous decoupling, and strict zero-trust security with **zero hardcoded credentials**.

---

## 📑 Table of Contents
- [System Architecture](#-system-architecture)
- [AWS Cloud Components Breakdown](#-aws-cloud-components-breakdown)
- [Dual-Portal User Experience](#-dual-portal-user-experience)
- [AI Damage Inspection & Fraud Detection](#-ai-damage-inspection--fraud-detection)
- [Project Directory Structure](#-project-directory-structure)
- [Security & Zero-Trust Best Practices](#-security--zero-trust-best-practices)
- [Getting Started & Run Instructions](#-getting-started--run-instructions)
  - [Option 1: Complete AWS Cloud Deployment (CloudFormation)](#option-1-complete-aws-cloud-deployment-cloudformation)
  - [Option 2: Running Locally with Docker](#option-2-running-locally-with-docker)
  - [Option 3: Running Standalone with Python](#option-3-running-standalone-with-python)
- [Operational Commands & Automation Scripts](#-operational-commands--automation-scripts)
- [Cloud Cost Optimization & Hibernation](#-cloud-cost-optimization--hibernation)

---

## 🏛️ System Architecture

ClaimGuard operates on a secure, multi-tier architecture spanning public compute subnets, private managed database subnets, decoupled serverless processing, and secure encrypted object storage.

```mermaid
flowchart TD
    subgraph Clients["Users & Insurers"]
        Policyholder(["👤 Policyholder / Customer"])
        Adjuster(["👔 Claims Adjuster / Admin"])
    end

    subgraph AWS_VPC["Amazon VPC (10.0.0.0/16)"]
        subgraph PublicSubnets["Public Subnets (us-east-1a / us-east-1b)"]
            IGW["Internet Gateway"]
            EC2["EC2 Web Server (t3.micro)
Docker: Gunicorn + Flask Application"]
        end

        subgraph PrivateSubnets["Private DB Subnets (us-east-1a / us-east-1b)"]
            RDS[("Amazon RDS MySQL 8.0
Private DB Subnet Group
Encrypted Storage")]
        end
        
        IGW <--> EC2
        EC2 -->|MySQL Port 3306| RDS
    end

    subgraph AWS_Storage_Config["AWS Storage & Parameter Management"]
        SSM["AWS Systems Manager
Parameter Store (SecureString KMS)"]
        S3[("Amazon S3 Evidence Bucket
AES-256 Server-Side Encryption
Block Public Access")]
    end

    subgraph AWS_Decoupled_Processing["Decoupled Serverless Tier"]
        SQS["Amazon SQS Queue
ClaimGuard-ProcessingQueue"]
        DLQ["Amazon SQS DLQ
ClaimGuard-DLQ (Poison Pill Isolation)"]
        Lambda["AWS Lambda Function
ClaimGuard-DamageAnalyzerFunction
(Python 3.11 Runtime)"]
        SNS["Amazon SNS Topic
ClaimGuard-ClaimAlerts-Topic"]
    end

    subgraph AWS_Observability["Observability & Cost Governance"]
        CW["Amazon CloudWatch
Alarms: EC2 High CPU (>75%)
Logs: Flask & Lambda Audit Trails"]
        Budgets["AWS Budgets
$1.00 Monthly Cost Threshold Alert"]
    end

    %% Interactions
    Policyholder -->|1. Submit Claim & Upload Photo| EC2
    Adjuster -->|Review & Adjudicate Claims| EC2

    EC2 -->|2. Bootstrap Config & DB Secrets| SSM
    EC2 -->|3. Store Photo Evidence| S3
    EC2 -->|4. Generate Pre-Signed URLs (1 hr expiry)| S3
    EC2 -->|5. Enqueue Claim Event| SQS

    SQS -->|Failed 3x| DLQ
    SQS -->|6. Event-Source Trigger| Lambda
    Lambda -->|7. High-Risk / High-Value Alert| SNS
    SNS -->|8. Push Email Notification| Adjuster

    EC2 -.->|System Metrics| CW
    AWS_VPC -.->|Cost Tracking| Budgets
```

---

## ☁️ AWS Cloud Components Breakdown

ClaimGuard leverages **11 core AWS Cloud Services**, each serving a dedicated architectural purpose:

| Service | Component Name | Function & Implementation Details |
| :--- | :--- | :--- |
| **Amazon VPC** | `ClaimGuardVPC` (`10.0.0.0/16`) | Isolated virtual network divided into **2 Public Subnets** and **2 Private Subnets** across two Availability Zones (`us-east-1a`, `us-east-1b`) for high-availability database clustering without expensive NAT gateway overhead. |
| **Amazon EC2** | `ClaimGuard-Web-Server` (`t3.micro`) | Scalable compute running **Amazon Linux 2023**, hosting the containerized Flask/Gunicorn production application via Docker with auto-restart policies. |
| **Amazon RDS** | `claimguard-db` (MySQL 8.0) | Managed relational database engine isolated in private subnets, protected by security group rules permitting MySQL traffic (port 3306) **only** from the EC2 web tier. |
| **Amazon S3** | `claimguard-evidence-<account-id>` | Durable object storage housing vehicular damage photos. Configured with **Block Public Access**, **AES-256 Server-Side Encryption (SSE-S3)**, and **CORS**. Images are served securely using authenticated **Pre-Signed S3 URLs** with 1-hour expiration. |
| **AWS Systems Manager** | Parameter Store (`/claimguard/*`) | Centralized secret and configuration management. Injects database credentials (`SecureString`), bucket names, queue URLs, and Gemini API keys directly into runtime memory without touching disk or environment files. |
| **Amazon SQS** | `ClaimGuard-ProcessingQueue` | Fully managed message queuing service that decouples claim submission from backend fraud analysis, preventing system degradation during traffic spikes. |
| **Amazon SQS DLQ** | `ClaimGuard-DLQ` | Dead-Letter Queue configured with `maxReceiveCount: 3` to isolate corrupted or unprocessable messages (poison-pill prevention). |
| **AWS Lambda** | `ClaimGuard-DamageAnalyzerFunction` | Serverless Python 3.11 function triggered asynchronously via SQS Event Source Mapping. Analyzes claim parameters, evaluates severity and damage totals, and triggers instant alerts. |
| **Amazon SNS** | `ClaimGuard-ClaimAlerts-Topic` | Publish/Subscribe notification topic that fans out automated email alerts to insurance adjusters for claims exceeding $2,000, high fraud scores, or flagged statuses. |
| **AWS IAM** | `ClaimGuard-EC2-Role` & Instance Profile | Implements the **Principle of Least Privilege**. Provides temporary EC2 credentials for SSM, S3, SQS, and SNS access with zero hardcoded API keys. |
| **Amazon CloudWatch** | Metric Alarms & CloudWatch Logs | Real-time observability tracking EC2 CPU utilization (`>75%`), application execution logs, and Lambda function metrics. |
| **AWS Budgets** | `ClaimGuard-Monthly-FreeTier-Budget` | Automated financial governance alerting policyholders when monthly charges exceed **$1.00**, preventing unexpected billing. |

---

## 🖥️ Dual-Portal User Experience

To simulate an authentic enterprise insurance ecosystem, ClaimGuard provides two segregated interfaces:

### 1. Customer Self-Service Portal (`/portal`)
- Tailored for policyholders to submit claims directly from mobile or desktop.
- Features real-time image previews of damage evidence before submission.
- Instant AI pre-estimation of damage severity and estimated cost.
- **Privacy Sandbox:** Policyholders can only submit and view their own claim receipt; internal claims registries are inaccessible.

### 2. Claims Adjuster Operations Center (`/`)
- Enterprise dashboard for claims investigators, adjusters, and underwriters.
- Searchable, filterable claims register with status indicators (`PENDING_REVIEW`, `APPROVED`, `FLAGGED_HIGH_RISK`, `REJECTED`).
- Comprehensive claim detail view (`/claim/<claim_id>`) showing:
  - High-resolution photographic evidence loaded via secure AWS Pre-Signed S3 URLs.
  - 5-step distributed cloud audit trail (EC2 $	o$ SSM $	o$ S3 $	o$ RDS $	o$ SQS/Lambda).
  - Adjudication action controls with instant database update and SNS fan-out email alerts.
- Live Infrastructure Topology monitor (`/cloud-infra`) showing the health of all 11 AWS services.
- Real-time Risk Analytics (`/analytics`) summarizing portfolio loss distributions and fraud frequencies.

---

## 🤖 AI Damage Inspection & Fraud Detection

ClaimGuard includes a hybrid AI inspection architecture:

1. **Multimodal Vision Inspection (Google Gemini 1.5 Flash):**
   - Evaluates damage photos and textual descriptions to identify damaged assemblies (e.g., *Front Bumper, Radiator Core, Right Headlight Assembly*).
   - Generates realistic parts and labor repair estimates along with an automated fraud risk score (0–100).
2. **InsurTech Fallback Engine:**
   - If external AI APIs are unconfigured or unavailable, an internal heuristic rule engine computes accurate estimations based on component assembly pricing dictionaries and severity weighting.

---

## 📂 Project Directory Structure

```text
ClaimGuard/
├── app/
│   ├── app.py                      # Core Flask web application & AWS SDK integration
│   ├── Dockerfile                  # Container definition (Python 3.11-slim, Gunicorn)
│   ├── requirements.txt            # Python dependencies (Flask, boto3, pymysql, etc.)
│   └── templates/
│       ├── index.html              # Claims Adjuster Workspace & Registry
│       ├── portal.html             # Customer Self-Service Claim Submission Portal
│       ├── claim_detail.html       # Claim investigation, photo viewer & adjudication
│       ├── analytics.html          # Portfolio risk and loss analytics charts
│       └── cloud_infra.html        # Interactive AWS Cloud Topology & Status map
├── iac/
│   └── claimguard-infra.yaml       # Production AWS CloudFormation Infrastructure-as-Code
├── sample_damage.jpg               # High-resolution vehicle collision photo for testing
├── set-gemini-key.ps1              # Securely store Gemini API key in SSM Parameter Store
├── start-claimguard.ps1            # Automated startup: boots RDS, checks status, starts EC2
├── stop-claimguard.ps1             # 1-Click hibernation script (stops EC2 & RDS to prevent costs)
├── .gitignore                      # Git ignore file (excludes secrets, keys, and archives)
└── README.md                       # Complete technical documentation
```

---

## 🔒 Security & Zero-Trust Best Practices

1. **Zero Hardcoded Secrets Policy:**
   - No AWS Access Keys (`AKIA...`), database passwords, or third-party API tokens are stored in the codebase or git repository.
   - All compute instances retrieve scoped IAM credentials dynamically via **EC2 Instance Profiles**.
2. **KMS & SSM Parameter Encryption:**
   - Database credentials and API keys are stored in AWS Systems Manager Parameter Store under the `SecureString` type, encrypted using AWS Key Management Service (KMS).
3. **Private Database Isolation:**
   - Amazon RDS MySQL has `PubliclyAccessible: false` and resides in dedicated private subnets with no route to the Internet Gateway.
4. **Time-Limited S3 Pre-Signed URLs:**
   - Direct public access to S3 evidence is disabled (`BlockPublicAccess: true`). Images are accessed exclusively through cryptographically signed URLs valid for 3600 seconds.

---

## 🚀 Getting Started & Run Instructions

### Option 1: Complete AWS Cloud Deployment (CloudFormation)

Deploy the entire 11-service cloud architecture with a single AWS CLI command:

```bash
# 1. Clone this repository
git clone https://github.com/Manthan2907/ClaImGuard.git
cd ClaImGuard

# 2. Deploy CloudFormation Stack
aws cloudformation deploy \
  --template-file iac/claimguard-infra.yaml \
  --stack-name ClaimGuard-Production-Stack \
  --parameter-overrides \
      DBMasterPassword="YourStrongPassword123!" \
      AlertEmail="your-email@example.com" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

Once deployment completes (~8 minutes), retrieve the EC2 Public IP from outputs:
```bash
aws cloudformation describe-stacks \
  --stack-name ClaimGuard-Production-Stack \
  --region us-east-1 \
  --query "Stacks[0].Outputs"
```

Access the application in your browser:
- **Adjuster Workspace:** `http://<EC2-PUBLIC-IP>/`
- **Customer Portal:** `http://<EC2-PUBLIC-IP>/portal`

---

### Option 2: Running Locally with Docker

You can run ClaimGuard locally on your machine with Docker:

```bash
# 1. Build the Docker container
cd app
docker build -t claimguard:latest .

# 2. Run container (uses local fallback mode or AWS credentials if configured)
docker run -d -p 80:5000 \
  -e AWS_DEFAULT_REGION=us-east-1 \
  --name claimguard-app claimguard:latest
```

Open `http://localhost/` in your web browser.

---

### Option 3: Running Standalone with Python

```bash
# 1. Create and activate a Python virtual environment
cd app
python -m venv venv

# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch Flask application
python app.py
```

The local development server will start at `http://127.0.0.1:5000/`.

---

## ⚡ Operational Commands & Automation Scripts

### 1. Start Cloud Services (Automated Health-Checked Boot)
AWS RDS MySQL takes 2–3 minutes to initialize. Use the automated startup script that monitors RDS until it reports `available` before bringing up EC2:
```powershell
powershell -ExecutionPolicy Bypass -File .\start-claimguard.ps1
```

### 2. Hibernation & Cost Prevention (Stop All Compute)
When finishing demonstrations or testing, hibernate compute instances to ensure zero credit consumption:
```powershell
powershell -ExecutionPolicy Bypass -File .\stop-claimguard.ps1
```

### 3. Configure Google Gemini AI Key
Update or supply a Google Gemini API Key securely without modifying application code or restarting servers:
```powershell
powershell -ExecutionPolicy Bypass -File .\set-gemini-key.ps1 -ApiKey "YOUR_GEMINI_API_KEY"
```

---

## 💰 Cloud Cost Optimization & Hibernation

ClaimGuard is optimized for student learning, proof-of-concepts, and cost-controlled enterprise pilots:
- **Single EC2 Web Instance (`t3.micro`):** Eligible for AWS Free Tier.
- **Single RDS Database (`db.t3.micro`):** Eligible for 750 free hours/month.
- **S3 Standard + Lifecycle:** Minimal storage costs.
- **Serverless Lambda & SQS:** 1 Million free requests/month under AWS Free Tier.
- **AWS Budgets Alert:** Set to notify if monthly bill exceeds **$1.00**.

Running `.\stop-claimguard.ps1` at the end of each session halts all hourly compute charges!

---

## 👨‍💻 Author & Repository

- **Repository:** [https://github.com/Manthan2907/ClaImGuard](https://github.com/Manthan2907/ClaImGuard)
- **Author:** Manthan Pagaria ([@Manthan2907](https://github.com/Manthan2907))
- **License:** MIT License
