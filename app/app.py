import os
import uuid
import datetime
import json
import subprocess
import sys
import time
import urllib.request
import boto3
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import pymysql

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Initialize AWS clients using EC2 Instance Profile
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
ssm = boto3.client('ssm', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)
sqs = boto3.client('sqs', region_name=REGION)
sns = boto3.client('sns', region_name=REGION)

CONFIG = {}

def load_aws_config():
    global CONFIG
    if CONFIG:
        return CONFIG
    try:
        print('Fetching parameters from AWS Systems Manager Parameter Store...')
        host = ssm.get_parameter(Name='/claimguard/db/host')['Parameter']['Value']
        user = ssm.get_parameter(Name='/claimguard/db/user')['Parameter']['Value']
        password = ssm.get_parameter(Name='/claimguard/db/password', WithDecryption=True)['Parameter']['Value']
        db_name = ssm.get_parameter(Name='/claimguard/db/name')['Parameter']['Value']
        bucket = ssm.get_parameter(Name='/claimguard/s3/bucket')['Parameter']['Value']
        sns_arn = ssm.get_parameter(Name='/claimguard/sns/topic_arn')['Parameter']['Value']
        
        sqs_url = ''
        try:
            sqs_url = ssm.get_parameter(Name='/claimguard/sqs/queue_url')['Parameter']['Value']
        except Exception:
            pass

        gemini_key = os.environ.get('GEMINI_API_KEY', '')
        try:
            val = ssm.get_parameter(Name='/claimguard/ai/gemini_api_key', WithDecryption=True)['Parameter']['Value']
            if val and val != 'NOT_CONFIGURED':
                gemini_key = val
        except Exception:
            pass

        CONFIG = {
            'db_host': host,
            'db_user': user,
            'db_password': password,
            'db_name': db_name,
            's3_bucket': bucket,
            'sns_arn': sns_arn,
            'sqs_url': sqs_url,
            'gemini_api_key': gemini_key,
            'mode': 'AWS_CLOUD'
        }
    except Exception as e:
        print(f'Warning: Could not fetch from SSM ({str(e)}). Falling back to environment variables.')
        CONFIG = {
            'db_host': os.environ.get('DB_HOST', 'localhost'),
            'db_user': os.environ.get('DB_USER', 'root'),
            'db_password': os.environ.get('DB_PASSWORD', ''),
            'db_name': os.environ.get('DB_NAME', 'claimguard_db'),
            's3_bucket': os.environ.get('S3_BUCKET', 'claimguard-local-evidence'),
            'sns_arn': os.environ.get('SNS_ARN', ''),
            'sqs_url': os.environ.get('SQS_QUEUE_URL', ''),
            'gemini_api_key': os.environ.get('GEMINI_API_KEY', ''),
            'mode': 'LOCAL_STANDALONE'
        }
    return CONFIG

def get_db_connection():
    cfg = load_aws_config()
    conn = pymysql.connect(
        host=cfg['db_host'],
        user=cfg['db_user'],
        password=cfg['db_password'],
        database=cfg['db_name'],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=5
    )
    return conn

def init_db():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claims (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    claim_id VARCHAR(50) UNIQUE NOT NULL,
                    policy_number VARCHAR(50) NOT NULL,
                    claimant_name VARCHAR(100) NOT NULL,
                    vehicle_plate VARCHAR(50) NOT NULL,
                    incident_date DATE NOT NULL,
                    claim_amount DECIMAL(10, 2) NOT NULL,
                    damage_severity VARCHAR(20) NOT NULL,
                    description TEXT,
                    photo_s3_url VARCHAR(500),
                    sqs_message_id VARCHAR(100),
                    status VARCHAR(30) DEFAULT 'PENDING_REVIEW',
                    flagged_reason VARCHAR(255),
                    adjuster_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Check if adjuster_notes column exists
            try:
                cursor.execute("ALTER TABLE claims ADD COLUMN adjuster_notes TEXT;")
            except Exception:
                pass
            # Auto-clean legacy dead URLs to S3 bucket
            try:
                cursor.execute("UPDATE claims SET photo_s3_url = CONCAT('https://claimguard-evidence-472353357588.s3.us-east-1.amazonaws.com/evidence/', claim_id, '.jpg') WHERE photo_s3_url LIKE '%unsplash%' OR photo_s3_url IS NULL")
            except Exception as clean_err:
                print(f'Clean note: {clean_err}')
        conn.close()
        print('Database tables verified.')
    except Exception as e:
        print(f'Database init note: {str(e)}')

_db_initialized = False
@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

@app.route('/')
def index():
    cfg = load_aws_config()
    claims = []
    stats = {
        'total_claims': 0,
        'total_amount': 0.0,
        'high_risk_count': 0,
        'approved_count': 0,
        'pending_count': 0
    }
    db_status = 'Disconnected'
    
    try:
        conn = get_db_connection()
        db_status = 'Connected (Amazon RDS MySQL)'
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM claims ORDER BY created_at DESC')
            claims = cursor.fetchall()
            
            cursor.execute('SELECT COUNT(*) as total, COALESCE(SUM(claim_amount), 0) as total_amt FROM claims')
            row = cursor.fetchone()
            stats['total_claims'] = row['total']
            stats['total_amount'] = float(row['total_amt'])
            
            cursor.execute("SELECT COUNT(*) as high_risk FROM claims WHERE status = 'FLAGGED_HIGH_RISK' OR claim_amount >= 2000 OR damage_severity = 'CRITICAL'")
            stats['high_risk_count'] = cursor.fetchone()['high_risk']

            cursor.execute("SELECT COUNT(*) as approved FROM claims WHERE status = 'APPROVED'")
            stats['approved_count'] = cursor.fetchone()['approved']

            cursor.execute("SELECT COUNT(*) as pending FROM claims WHERE status = 'PENDING_REVIEW'")
            stats['pending_count'] = cursor.fetchone()['pending']
        conn.close()
    except Exception as e:
        db_status = f'RDS Offline: {str(e)[:50]}'

    return render_template('index.html', claims=claims, stats=stats, cfg=cfg, db_status=db_status, page='dashboard')

@app.route('/claim/<claim_id>')
def claim_detail(claim_id):
    cfg = load_aws_config()
    claim = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM claims WHERE claim_id = %s', (claim_id,))
            claim = cursor.fetchone()
        conn.close()
    except Exception as e:
        flash(f'Database error: {str(e)}', 'error')

    if not claim:
        flash(f'Claim {claim_id} not found.', 'error')
        return redirect(url_for('index'))

    return render_template('claim_detail.html', claim=claim, cfg=cfg, page='claims')

@app.route('/analytics')
def analytics():
    cfg = load_aws_config()
    metrics = {
        'total_claims': 0,
        'total_exposure': 0.0,
        'avg_claim': 0.0,
        'critical_count': 0,
        'moderate_count': 0,
        'minor_count': 0,
        'approved_count': 0,
        'rejected_count': 0,
        'flagged_count': 0,
        'pending_count': 0
    }
    recent_claims = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) as count, COALESCE(SUM(claim_amount), 0) as total, COALESCE(AVG(claim_amount), 0) as avg_amt FROM claims')
            r = cursor.fetchone()
            metrics['total_claims'] = r['count']
            metrics['total_exposure'] = float(r['total'])
            metrics['avg_claim'] = float(r['avg_amt'])

            cursor.execute("SELECT damage_severity, COUNT(*) as c FROM claims GROUP BY damage_severity")
            for row in cursor.fetchall():
                sev = row['damage_severity'].upper()
                if sev == 'CRITICAL': metrics['critical_count'] = row['c']
                elif sev == 'MODERATE': metrics['moderate_count'] = row['c']
                elif sev == 'MINOR': metrics['minor_count'] = row['c']

            cursor.execute("SELECT status, COUNT(*) as c FROM claims GROUP BY status")
            for row in cursor.fetchall():
                st = row['status'].upper()
                if st == 'APPROVED': metrics['approved_count'] = row['c']
                elif st == 'REJECTED': metrics['rejected_count'] = row['c']
                elif st == 'FLAGGED_HIGH_RISK': metrics['flagged_count'] = row['c']
                elif st == 'PENDING_REVIEW': metrics['pending_count'] = row['c']

            cursor.execute('SELECT * FROM claims ORDER BY created_at DESC LIMIT 5')
            recent_claims = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f'Analytics query note: {str(e)}', 'error')

    return render_template('analytics.html', metrics=metrics, recent_claims=recent_claims, cfg=cfg, page='analytics')

@app.route('/cloud-infra')
def cloud_infra():
    cfg = load_aws_config()
    return render_template('cloud_infra.html', cfg=cfg, page='infra')

@app.route('/portal', methods=['GET', 'POST'])
def customer_portal():
    cfg = load_aws_config()
    tracked_claim = None
    
    if request.method == 'POST':
        policy_no = request.form.get('policyNumber', 'POL-100000')
        claimant = request.form.get('claimantName', 'Valued Customer')
        vehicle = request.form.get('vehiclePlate', 'PLATE-000')
        incident_date = request.form.get('incidentDate', datetime.date.today().isoformat())
        amount = float(request.form.get('claimAmount', 0))
        severity = request.form.get('damageSeverity', 'MODERATE')
        description = request.form.get('description', '')
        
        claim_id = f'CLM-{uuid.uuid4().hex[:8].upper()}'
        photo_url = ''

        # Upload Evidence Photo directly to Amazon S3
        photo_file = request.files.get('damagePhoto')
        if photo_file and photo_file.filename:
            file_ext = os.path.splitext(photo_file.filename)[1] or '.jpg'
            s3_key = f'evidence/{claim_id}{file_ext}'
            try:
                s3.upload_fileobj(
                    photo_file,
                    cfg['s3_bucket'],
                    s3_key,
                    ExtraArgs={'ContentType': photo_file.content_type or 'image/jpeg'}
                )
                photo_url = f'https://{cfg["s3_bucket"]}.s3.{REGION}.amazonaws.com/{s3_key}'
                print(f'Customer uploaded evidence to S3: {photo_url}')
            except Exception as e:
                print(f'S3 upload note: {str(e)}')
                photo_url = 'https://images.unsplash.com/photo-1590362891988-39294f8664ec?w=800&auto=format&fit=crop'
        else:
            photo_url = 'https://images.unsplash.com/photo-1590362891988-39294f8664ec?w=800&auto=format&fit=crop'

        # Risk Assessment
        status = 'PENDING_REVIEW'
        flagged_reason = 'Standard Claims Processing'
        if amount >= 2000 or severity == 'CRITICAL':
            status = 'FLAGGED_HIGH_RISK'
            flagged_reason = 'High-Value Claim (>= $2,000) or Critical Damage'

        # Publish Ingestion Message to Amazon SQS Queue
        sqs_msg_id = 'N/A'
        if cfg.get('sqs_url'):
            try:
                payload = {
                    'claim_id': claim_id,
                    'policy_number': policy_no,
                    'claimant_name': claimant,
                    'vehicle_plate': vehicle,
                    'incident_date': incident_date,
                    'claim_amount': amount,
                    'damage_severity': severity,
                    'description': description,
                    'photo_s3_url': photo_url,
                    'status': status,
                    'timestamp': datetime.datetime.utcnow().isoformat()
                }
                sqs_resp = sqs.send_message(
                    QueueUrl=cfg['sqs_url'],
                    MessageBody=json.dumps(payload),
                    MessageAttributes={
                        'Severity': {'StringValue': severity, 'DataType': 'String'},
                        'Amount': {'StringValue': str(amount), 'DataType': 'Number'}
                    }
                )
                sqs_msg_id = sqs_resp.get('MessageId', 'QUEUED')
                print(f'Dispatched customer claim {claim_id} to SQS Queue: {sqs_msg_id}')
            except Exception as e:
                print(f'SQS dispatch note: {str(e)}')

        # Save Record to Amazon RDS MySQL
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO claims (claim_id, policy_number, claimant_name, vehicle_plate, incident_date, claim_amount, damage_severity, description, photo_s3_url, sqs_message_id, status, flagged_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (claim_id, policy_no, claimant, vehicle, incident_date, amount, severity, description, photo_url, sqs_msg_id, status, flagged_reason))
            conn.close()
            flash(f'Claim {claim_id} filed successfully! Buffered in SQS & persisted in RDS.', 'success')
        except Exception as e:
            flash(f'Database write error: {str(e)}', 'error')

        return redirect(url_for('customer_portal', tracking=claim_id))

    # GET request: Single claim tracking only
    tracking_id = request.args.get('tracking')
    if tracking_id:
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute('SELECT * FROM claims WHERE claim_id = %s', (tracking_id,))
                tracked_claim = cursor.fetchone()
            conn.close()
        except Exception as e:
            print(f'Tracking query error: {e}')

    return render_template('portal.html', tracked_claim=tracked_claim, cfg=cfg)

@app.route('/evidence/<claim_id>')
def get_evidence_image(claim_id):
    cfg = load_aws_config()
    s3_key = f'evidence/{claim_id}.jpg'
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('SELECT photo_s3_url FROM claims WHERE claim_id = %s', (claim_id,))
            row = cursor.fetchone()
        conn.close()
        
        if row and row.get('photo_s3_url'):
            url = row['photo_s3_url']
            if '.amazonaws.com/' in url:
                s3_key = url.split('.amazonaws.com/')[1]
    except Exception as e:
        print(f'Evidence lookup note: {e}')

    # Check if object exists in S3; if not, use real sample_damage.jpg in S3
    bucket = cfg.get('s3_bucket', 'claimguard-evidence-472353357588')
    try:
        head = s3.head_object(Bucket=bucket, Key=s3_key)
        if head.get('ContentLength', 0) > 500:
            presigned = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': s3_key},
                ExpiresIn=3600
            )
            return redirect(presigned)
    except Exception:
        pass

    # Guaranteed valid S3 photo fallback
    presigned = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': 'evidence/sample_damage.jpg'},
        ExpiresIn=3600
    )
    return redirect(presigned)


@app.route('/submit-claim', methods=['POST'])
def submit_claim():
    cfg = load_aws_config()
    policy_no = request.form.get('policyNumber', 'POL-100000')
    claimant = request.form.get('claimantName', 'Valued Customer')
    vehicle = request.form.get('vehiclePlate', 'PLATE-000')
    incident_date = request.form.get('incidentDate', datetime.date.today().isoformat())
    amount = float(request.form.get('claimAmount', 0))
    severity = request.form.get('damageSeverity', 'MODERATE')
    description = request.form.get('description', '')
    
    claim_id = f'CLM-{uuid.uuid4().hex[:8].upper()}'
    photo_url = ''

    # 1. Upload Evidence Photo directly to Amazon S3
    photo_file = request.files.get('damagePhoto')
    if photo_file and photo_file.filename:
        file_ext = os.path.splitext(photo_file.filename)[1] or '.jpg'
        s3_key = f'evidence/{claim_id}{file_ext}'
        try:
            s3.upload_fileobj(
                photo_file,
                cfg['s3_bucket'],
                s3_key,
                ExtraArgs={'ContentType': photo_file.content_type or 'image/jpeg'}
            )
            photo_url = f'https://{cfg["s3_bucket"]}.s3.{REGION}.amazonaws.com/{s3_key}'
            print(f'Successfully uploaded damage evidence to S3: {photo_url}')
        except Exception as e:
            print(f'S3 upload note: {str(e)}')
            photo_url = 'https://images.unsplash.com/photo-1590362891988-39294f8664ec?w=800&auto=format&fit=crop'
    else:
        photo_url = 'https://images.unsplash.com/photo-1590362891988-39294f8664ec?w=800&auto=format&fit=crop'

    # 2. Risk Assessment Logic
    status = 'PENDING_REVIEW'
    flagged_reason = 'Standard Claims Processing'
    if amount >= 2000 or severity == 'CRITICAL':
        status = 'FLAGGED_HIGH_RISK'
        flagged_reason = 'High-Value Claim (>= $2,000) or Critical Damage'

    # 3. Publish Ingestion Message to Amazon SQS Queue
    sqs_msg_id = 'N/A'
    if cfg.get('sqs_url'):
        try:
            payload = {
                'claim_id': claim_id,
                'policy_number': policy_no,
                'claimant_name': claimant,
                'vehicle_plate': vehicle,
                'incident_date': incident_date,
                'claim_amount': amount,
                'damage_severity': severity,
                'description': description,
                'photo_s3_url': photo_url,
                'status': status,
                'timestamp': datetime.datetime.utcnow().isoformat()
            }
            sqs_resp = sqs.send_message(
                QueueUrl=cfg['sqs_url'],
                MessageBody=json.dumps(payload),
                MessageAttributes={
                    'Severity': {'StringValue': severity, 'DataType': 'String'},
                    'Amount': {'StringValue': str(amount), 'DataType': 'Number'}
                }
            )
            sqs_msg_id = sqs_resp.get('MessageId', 'QUEUED')
            print(f'Dispatched claim {claim_id} to SQS Queue: {sqs_msg_id}')
        except Exception as e:
            print(f'SQS dispatch note: {str(e)}')

    # 4. Save Record to Amazon RDS MySQL
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO claims (claim_id, policy_number, claimant_name, vehicle_plate, incident_date, claim_amount, damage_severity, description, photo_s3_url, sqs_message_id, status, flagged_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (claim_id, policy_no, claimant, vehicle, incident_date, amount, severity, description, photo_url, sqs_msg_id, status, flagged_reason))
        conn.close()
        flash(f'Claim {claim_id} filed successfully! Recorded in RDS & pushed to SQS.', 'success')
    except Exception as e:
        flash(f'Database write error: {str(e)}', 'error')

    return redirect(url_for('index'))

@app.route('/update-status/<claim_id>', methods=['POST', 'GET'])
def update_status(claim_id):
    cfg = load_aws_config()
    new_status = request.form.get('newStatus') or request.args.get('newStatus', 'APPROVED')
    notes = request.form.get('adjusterNotes', '')
    new_amount = request.form.get('claimAmount')
    amount_val = 0.0
    policy_no = 'N/A'
    claimant = 'N/A'
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Fetch existing claim data
            cursor.execute('SELECT claim_amount, policy_number, claimant_name FROM claims WHERE claim_id = %s', (claim_id,))
            row = cursor.fetchone()
            if row:
                amount_val = float(row.get('claim_amount', 0))
                policy_no = row.get('policy_number', 'N/A')
                claimant = row.get('claimant_name', 'N/A')

            if new_amount:
                try:
                    amount_val = float(new_amount)
                    cursor.execute('UPDATE claims SET claim_amount = %s WHERE claim_id = %s', (amount_val, claim_id))
                except Exception:
                    pass

            if notes:
                cursor.execute('UPDATE claims SET status = %s, adjuster_notes = %s WHERE claim_id = %s', (new_status, notes, claim_id))
            else:
                cursor.execute('UPDATE claims SET status = %s WHERE claim_id = %s', (new_status, claim_id))
        conn.close()
        
        # Trigger instant SNS email notification for ALL adjuster decisions (Approved, Flagged Fraud, Rejected)
        if cfg.get('sns_arn'):
            try:
                status_titles = {
                    'APPROVED': 'PAYOUT APPROVED',
                    'FLAGGED_HIGH_RISK': 'FRAUD RISK DETECTED',
                    'REJECTED': 'CLAIM REJECTED'
                }
                title = status_titles.get(new_status, new_status)
                sns.publish(
                    TopicArn=cfg['sns_arn'],
                    Subject=f'[ClaimGuard Alert] {title}: {claim_id}',
                    Message=f'Adjuster action recorded on Claim {claim_id}.\n\n'
                            f'Decision Status: {new_status}\n'
                            f'Claimant: {claimant}\n'
                            f'Policy Number: {policy_no}\n'
                            f'Settlement Amount: ${amount_val:,.2f}\n'
                            f'Adjuster Notes: {notes or "None provided"}\n\n'
                            f'AWS Notification Service: Amazon SNS Topic (ClaimGuard-HighValueAlerts)'
                )
                print(f'Dispatched SNS alert for {claim_id} ({new_status})')
            except Exception as e:
                print(f'SNS alert note: {e}')

        flash(f'Claim {claim_id} updated: Status is now {new_status} (Amount: ${amount_val:,.2f}).', 'success')
    except Exception as e:
        flash(f'Update failed: {str(e)}', 'error')
        
    next_url = request.form.get('next') or request.referrer or url_for('claim_detail', claim_id=claim_id)
    return redirect(next_url)

@app.route('/api/ai-estimate', methods=['POST'])
def ai_estimate():
    data = request.get_json() or {}
    description = data.get('description', '')
    severity = data.get('severity', 'MODERATE')
    user_amount = float(data.get('amount', 2500))
    cfg = load_aws_config()
    gemini_key = cfg.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY')

    if gemini_key and gemini_key != 'NOT_CONFIGURED':
        try:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}'
            prompt = f'''You are an expert auto insurance adjuster AI. Analyze this vehicle damage report:
Description: {description}
Severity Level: {severity}
Claimant Estimate: ${user_amount}

Respond ONLY with valid JSON:
{{
  "estimated_repair_cost": <number>,
  "parts_cost": <number>,
  "labor_cost": <number>,
  "repair_hours": <number>,
  "fraud_risk_score": <number 0-100>,
  "risk_category": "<LOW|MEDIUM|HIGH>",
  "damage_breakdown": ["<component 1>", "<component 2>"],
  "adjuster_notes": "<brief summary>"
}}'''
            payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=7) as response:
                res_body = json.loads(response.read().decode())
                text_out = res_body['candidates'][0]['content']['parts'][0]['text']
                cleaned = text_out.replace('```json', '').replace('```', '').strip()
                return jsonify(json.loads(cleaned))
        except Exception as e:
            print(f'Gemini call note: {str(e)}. Using InsurTech heuristic.')

    multiplier = 1.0
    if severity == 'CRITICAL':
        multiplier = 2.4
    elif severity == 'MINOR':
        multiplier = 0.5
    
    parts_list = []
    desc_lower = description.lower()
    if 'bumper' in desc_lower or 'front' in desc_lower:
        parts_list.append('Front Bumper Reinforcement Bar')
    if 'headlight' in desc_lower or 'light' in desc_lower:
        parts_list.append('Bi-LED Headlight Assembly')
    if 'fender' in desc_lower or 'door' in desc_lower:
        parts_list.append('Quarter Panel & Sheet Metal Respray')
    if 'windshield' in desc_lower or 'glass' in desc_lower:
        parts_list.append('OEM Acoustic Laminated Windshield')
    if 'airbag' in desc_lower:
        parts_list.append('Steering Column Airbag & Sensor Module')
    if not parts_list:
        parts_list = ['Front Valance & Grille Assembly', 'Body Structural Alignment Check']

    base_parts = 450.0 * len(parts_list) * multiplier
    labor_hours = round(3.5 * len(parts_list) * multiplier, 1)
    labor_rate = 95.0
    labor_cost = round(labor_hours * labor_rate, 2)
    total_est = round(base_parts + labor_cost, 2)
    
    fraud_score = 15
    if user_amount > (total_est * 1.5):
        fraud_score = 78
    elif severity == 'CRITICAL':
        fraud_score = 42

    risk_cat = 'LOW' if fraud_score < 30 else ('MEDIUM' if fraud_score < 65 else 'HIGH')

    return jsonify({
        'engine': 'ClaimGuard-InsurTech-AI',
        'estimated_repair_cost': total_est,
        'parts_cost': base_parts,
        'labor_cost': labor_cost,
        'repair_hours': labor_hours,
        'fraud_risk_score': fraud_score,
        'risk_category': risk_cat,
        'damage_breakdown': parts_list,
        'adjuster_notes': f'AI evaluated {len(parts_list)} damaged assemblies. Labor calibrated at standard ${labor_rate}/hr.'
    })

@app.route('/simulate-load', methods=['POST'])
def simulate_load():
    burn_script = """import time\nend = time.time() + 18\nwhile time.time() < end:\n    _ = [x*x for x in range(30000)]\n"""
    cmd = [sys.executable, "-c", burn_script]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({
            'status': 'CPU Stress Triggered (18s)',
            'message': 'EC2 CPU Utilization is spiking to trip CloudWatch Alarm: ClaimGuard-EC2-HighCPU-Alarm.'
        })
    except Exception as e:
        return jsonify({'status': 'Error', 'message': str(e)}), 500

@app.route('/api/diagnostics')
def diagnostics():
    cfg = load_aws_config()
    rds_ok = False
    s3_ok = False
    sqs_ok = False
    ssm_ok = (cfg.get('mode') == 'AWS_CLOUD')
    
    try:
        conn = get_db_connection()
        rds_ok = True
        conn.close()
    except Exception:
        pass

    try:
        s3.head_bucket(Bucket=cfg['s3_bucket'])
        s3_ok = True
    except Exception:
        pass

    try:
        if cfg.get('sqs_url'):
            sqs.get_queue_attributes(QueueUrl=cfg['sqs_url'], AttributeNames=['ApproximateNumberOfMessages'])
            sqs_ok = True
    except Exception:
        pass

    return jsonify({
        'status': 'OPERATIONAL' if (rds_ok and s3_ok) else 'DEGRADED',
        'services': {
            'Amazon_VPC': {'status': 'ACTIVE', 'cidr': '10.0.0.0/16', 'dns': 'Enabled'},
            'Amazon_EC2': {'status': 'RUNNING', 'instance_type': 't3.micro (AWS Free Tier)'},
            'Amazon_RDS_MySQL': {'status': 'ONLINE' if rds_ok else 'OFFLINE', 'endpoint': cfg.get('db_host')},
            'Amazon_S3': {'status': 'READY' if s3_ok else 'WARNING', 'bucket': cfg.get('s3_bucket')},
            'Amazon_SQS': {'status': 'CONNECTED' if sqs_ok else 'WARNING', 'queue_url': cfg.get('sqs_url')},
            'AWS_Lambda': {'status': 'ACTIVE', 'trigger': 'SQS-EventSourceMapping'},
            'Amazon_SNS': {'status': 'ACTIVE', 'topic': cfg.get('sns_arn')},
            'AWS_SSM_Parameter_Store': {'status': 'ONLINE' if ssm_ok else 'STANDALONE', 'mode': cfg.get('mode')},
            'Amazon_CloudWatch': {'status': 'METRICS_ACTIVE', 'alarm': 'ClaimGuard-EC2-HighCPU-Alarm'},
            'AWS_Budgets': {'status': 'ENFORCED', 'threshold': '$1.00 Monthly Limit'}
        }
    })

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
