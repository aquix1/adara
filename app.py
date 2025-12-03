from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import bcrypt
import os
import base64
import random
import string
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'my-secret-key')

# MongoDB connection
client = MongoClient(os.getenv('MONGO_URI'))
db = client['secure_file_manager']

# Collections
users_collection = db['users']
files_collection = db['files']
docs_collection = db['docs']
settings_collection = db['settings']
deletion_codes_collection = db['deletion_codes']

# --- Encryption setup ---
def generate_key_from_password(password: str | bytes, salt: bytes = None):
    if isinstance(password, str):
        password = password.encode('utf-8')
    if salt is None:
        salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000
    )
    key = base64.urlsafe_b64encode(kdf.derive(password))
    return key, salt

default_password = os.getenv('ENCRYPTION_PASSWORD', 'default-encryption-password')
encryption_key, encryption_salt = generate_key_from_password(default_password)
cipher_suite = Fernet(encryption_key)

def get_user_cipher_suite(user_id):
    """Return Fernet cipher for user"""
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if user and user.get('encryption_password') and user.get('encryption_salt'):
        password = user['encryption_password']
        salt = user['encryption_salt']
        if isinstance(password, str):
            password = password.encode('utf-8')
        key, _ = generate_key_from_password(password, salt)
        return Fernet(key)
    return cipher_suite

# --- Helper Functions ---
def generate_deletion_code(length=6):
    """Generate random deletion confirmation code"""
    return ''.join(random.choices(string.digits, k=length))

def store_deletion_code(user_id, code_type):
    """Store deletion code in database"""
    code = generate_deletion_code()
    deletion_codes_collection.update_one(
        {'user_id': user_id, 'code_type': code_type},
        {'$set': {
            'code': code,
            'created_at': datetime.utcnow(),
            'expires_at': datetime.utcnow().timestamp() + 300  # 5 minutes
        }},
        upsert=True
    )
    return code

def verify_deletion_code(user_id, code, code_type):
    """Verify deletion code"""
    record = deletion_codes_collection.find_one({
        'user_id': user_id,
        'code_type': code_type,
        'code': code
    })
    
    if not record:
        return False
    
    # Check if code expired
    if datetime.utcnow().timestamp() > record['expires_at']:
        deletion_codes_collection.delete_one({'_id': record['_id']})
        return False
    
    # Delete used code
    deletion_codes_collection.delete_one({'_id': record['_id']})
    return True

def apply_user_settings(user_id):
    """Apply user settings to session"""
    settings = settings_collection.find_one({'user_id': user_id})
    if settings:
        session['theme'] = settings.get('theme', 'light')
        session['language'] = settings.get('language', 'ar')
        session['auto_logout'] = settings.get('auto_logout', 60)

# --- Routes ---

@app.before_request
def before_request():
    """Apply settings before each request"""
    if 'user_id' in session:
        apply_user_settings(session['user_id'])

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# -------- Register --------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        encryption_password = request.form.get('encryption_password', password)

        if users_collection.find_one({'email': email}):
            flash('البريد الإلكتروني مسجل مسبقاً', 'error')
            return redirect(url_for('register'))

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        encryption_salt = os.urandom(16)
        enc_pass_bytes = encryption_password.encode('utf-8')
        encryption_key, _ = generate_key_from_password(enc_pass_bytes, encryption_salt)

        user = {
            'username': username,
            'email': email,
            'password': hashed_password,
            'encryption_password': enc_pass_bytes,
            'encryption_salt': encryption_salt,
            'created_at': datetime.utcnow(),
            'last_login': datetime.utcnow(),
            'is_active': True,
            'storage_limit': 100 * 1024 * 1024,
            'used_storage': 0
        }
        result = users_collection.insert_one(user)

        # Default settings
        default_settings = {
            'user_id': str(result.inserted_id),
            'theme': 'light',
            'language': 'ar',
            'auto_logout': 60,
            'two_factor_auth': False,
            'notifications': True,
            'created_at': datetime.utcnow()
        }
        settings_collection.insert_one(default_settings)

        flash('تم إنشاء الحساب بنجاح، يمكنك تسجيل الدخول الآن', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

# -------- Login --------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember_me = 'remember_me' in request.form

        user = users_collection.find_one({'email': email, 'is_active': True})
        if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
            session['user_id'] = str(user['_id'])
            session['username'] = user['username']

            users_collection.update_one({'_id': user['_id']}, {'$set': {'last_login': datetime.utcnow()}})
            session.permanent = remember_me
            
            # Apply user settings
            apply_user_settings(session['user_id'])
            
            flash('تم تسجيل الدخول بنجاح', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('البريد الإلكتروني أو كلمة المرور غير صحيحة', 'error')

    return render_template('login.html')

# -------- Dashboard --------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('logout'))

    files_count = files_collection.count_documents({'user_id': user_id})
    docs_count = docs_collection.count_documents({'user_id': user_id})

    total_file_size = files_collection.aggregate([
        {'$match': {'user_id': user_id}},
        {'$group': {'_id': None, 'total': {'$sum': '$file_size'}}}
    ])
    used_storage = 0
    result = list(total_file_size)
    if result:
        used_storage = result[0].get('total', 0)

    users_collection.update_one({'_id': ObjectId(user_id)}, {'$set': {'used_storage': used_storage}})
    storage_limit = user.get('storage_limit', 100*1024*1024)
    storage_percentage = (used_storage / storage_limit) * 100 if storage_limit > 0 else 0
    user_files = list(files_collection.find({'user_id': session['user_id']}).sort('uploaded_at', -1))
    return render_template('dashboard.html',
                           username=session.get('username', 'User'),
                           user=user,
                           files_count=files_count,
                           docs_count=docs_count,
                           used_storage=used_storage,
                           storage_limit=storage_limit,
                           files=user_files,
                           storage_percentage=min(storage_percentage, 100))

# -------- Files --------
@app.route('/files')
def files():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_files = list(files_collection.find({'user_id': session['user_id']}).sort('uploaded_at', -1))
    return render_template('files.html', files=user_files, now=datetime.utcnow())

@app.route('/upload_file', methods=['POST'])
def upload_file():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('logout'))

    if 'file' not in request.files:
        flash('لم يتم اختيار ملف', 'error')
        return redirect(url_for('files'))
    file = request.files['file']
    if file.filename == '':
        flash('لم يتم اختيار ملف', 'error')
        return redirect(url_for('files'))

    content = file.read()
    file_size = len(content)

    if user.get('used_storage',0) + file_size > user.get('storage_limit',100*1024*1024):
        flash('لا توجد مساحة كافية، يرجى حذف بعض الملفات', 'error')
        return redirect(url_for('files'))

    user_cipher = get_user_cipher_suite(user_id)
    encrypted_content = user_cipher.encrypt(content)

    files_collection.insert_one({
        'user_id': user_id,
        'filename': file.filename,
        'encrypted_content': encrypted_content,
        'uploaded_at': datetime.utcnow(),
        'file_size': file_size,
        'file_type': file.content_type
    })

    users_collection.update_one({'_id': ObjectId(user_id)}, {'$inc': {'used_storage': file_size}})
    flash('تم رفع الملف بنجاح', 'success')
    return redirect(url_for('files'))

@app.route('/download_file/<file_id>')
def download_file(file_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    file_data = files_collection.find_one({'_id': ObjectId(file_id), 'user_id': session['user_id']})
    if not file_data:
        flash('الملف غير موجود', 'error')
        return redirect(url_for('files'))

    user_cipher = get_user_cipher_suite(session['user_id'])
    decrypted_content = user_cipher.decrypt(file_data['encrypted_content'])
    return Response(
        decrypted_content,
        mimetype=file_data.get('file_type','application/octet-stream'),
        headers={'Content-Disposition': f'attachment; filename={file_data["filename"]}'}
    )

@app.route('/delete_file/<file_id>', methods=['POST'])
def delete_file(file_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Check confirmation text
    confirmation = request.form.get('confirmation', '')
    if confirmation != 'حذف':
        flash('يرجى كتابة "حذف" بشكل صحيح للتأكيد', 'error')
        return redirect(url_for('delete_file_get', file_id=file_id))

    file_data = files_collection.find_one({'_id': ObjectId(file_id), 'user_id': session['user_id']})
    if file_data:
        # Decrement used storage
        users_collection.update_one(
            {'_id': ObjectId(session['user_id'])}, 
            {'$inc': {'used_storage': -file_data.get('file_size', 0)}}
        )
        # Delete file
        files_collection.delete_one({'_id': ObjectId(file_id)})
        flash('تم حذف الملف بنجاح', 'success')
    else:
        flash('الملف غير موجود', 'error')
    return redirect(url_for('files'))
# Add GET method for backward compatibility (with confirmation)
@app.route('/delete_file/<file_id>', methods=['GET'])
def delete_file_get(file_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    file_data = files_collection.find_one({'_id': ObjectId(file_id), 'user_id': session['user_id']})
    if not file_data:
        flash('الملف غير موجود', 'error')
        return redirect(url_for('files'))
    
    # Show confirmation page for GET requests
    return render_template('confirm_delete_single.html', file=file_data)


@app.route('/delete_all_files', methods=['GET', 'POST'])
def delete_all_files():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    
    if request.method == 'POST':
        confirmation_code = request.form.get('confirmation_code', '')
        
        if not verify_deletion_code(user_id, confirmation_code, 'all_files'):
            flash('كود التأكيد غير صحيح أو منتهي الصلاحية', 'error')
            return redirect(url_for('delete_all_files'))
        
        # Delete all user files
        result = files_collection.delete_many({'user_id': user_id})
        
        # Reset used storage
        users_collection.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'used_storage': 0}}
        )
        
        flash(f'تم حذف {result.deleted_count} ملف بنجاح', 'success')
        return redirect(url_for('files'))
    
    # Generate and store new confirmation code
    code = store_deletion_code(user_id, 'all_files')
    return render_template('confirm_deletion.html', 
                         action_type='all_files',
                         confirmation_code=code,
                         message='حذف جميع الملفات',
                         description='سيتم حذف جميع ملفاتك بشكل دائم ولا يمكن استرجاعها.')

# -------- Docs --------
@app.route('/docs')
def docs():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_docs = list(docs_collection.find({'user_id': session['user_id']}).sort('created_at', -1))
    return render_template('docs.html', docs=user_docs)

@app.route('/add_doc', methods=['POST'])
def add_doc():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    title = request.form.get('title','')
    content = request.form.get('content','')
    tags = request.form.get('tags','').split(',')

    if not title or not content:
        flash('يرجى ملء جميع الحقول', 'error')
        return redirect(url_for('docs'))

    user_cipher = get_user_cipher_suite(session['user_id'])
    encrypted_content = user_cipher.encrypt(content.encode('utf-8'))

    docs_collection.insert_one({
        'user_id': session['user_id'],
        'title': title,
        'encrypted_content': encrypted_content,
        'tags': [t.strip() for t in tags if t.strip()],
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
        'is_encrypted': True
    })
    flash('تم إضافة الوثيقة بنجاح', 'success')
    return redirect(url_for('docs'))

@app.route('/view_doc/<doc_id>')
def view_doc(doc_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    doc_data = docs_collection.find_one({'_id': ObjectId(doc_id), 'user_id': session['user_id']})
    if not doc_data:
        flash('الوثيقة غير موجودة', 'error')
        return redirect(url_for('docs'))

    user_cipher = get_user_cipher_suite(session['user_id'])
    decrypted_content = user_cipher.decrypt(doc_data['encrypted_content']).decode('utf-8')
    return render_template('view_doc.html', doc=doc_data, content=decrypted_content)

@app.route('/edit_doc/<doc_id>', methods=['GET','POST'])
def edit_doc(doc_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    doc_data = docs_collection.find_one({'_id': ObjectId(doc_id), 'user_id': session['user_id']})
    if not doc_data:
        flash('الوثيقة غير موجودة', 'error')
        return redirect(url_for('docs'))

    if request.method == 'POST':
        title = request.form.get('title','')
        content = request.form.get('content','')
        tags = request.form.get('tags','').split(',')

        if not title or not content:
            flash('يرجى ملء جميع الحقول', 'error')
            return redirect(url_for('edit_doc', doc_id=doc_id))

        user_cipher = get_user_cipher_suite(session['user_id'])
        encrypted_content = user_cipher.encrypt(content.encode('utf-8'))

        docs_collection.update_one({'_id': ObjectId(doc_id)}, {'$set': {
            'title': title,
            'encrypted_content': encrypted_content,
            'tags': [t.strip() for t in tags if t.strip()],
            'updated_at': datetime.utcnow()
        }})
        flash('تم تحديث الوثيقة بنجاح', 'success')
        return redirect(url_for('view_doc', doc_id=doc_id))

    user_cipher = get_user_cipher_suite(session['user_id'])
    decrypted_content = user_cipher.decrypt(doc_data['encrypted_content']).decode('utf-8')
    return render_template('edit_doc.html', doc=doc_data, content=decrypted_content)

@app.route('/delete_doc/<doc_id>')
def delete_doc(doc_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    doc_data = docs_collection.find_one({'_id': ObjectId(doc_id), 'user_id': session['user_id']})
    if doc_data:
        docs_collection.delete_one({'_id': ObjectId(doc_id)})
        flash('تم حذف الوثيقة بنجاح', 'success')
    else:
        flash('الوثيقة غير موجودة', 'error')
    return redirect(url_for('docs'))

@app.route('/delete_all_docs', methods=['GET', 'POST'])
def delete_all_docs():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    
    if request.method == 'POST':
        confirmation_code = request.form.get('confirmation_code', '')
        
        if not verify_deletion_code(user_id, confirmation_code, 'all_docs'):
            flash('كود التأكيد غير صحيح أو منتهي الصلاحية', 'error')
            return redirect(url_for('delete_all_docs'))
        
        # Delete all user documents
        result = docs_collection.delete_many({'user_id': user_id})
        flash(f'تم حذف {result.deleted_count} وثيقة بنجاح', 'success')
        return redirect(url_for('docs'))
    
    # Generate and store new confirmation code
    code = store_deletion_code(user_id, 'all_docs')
    return render_template('confirm_deletion.html', 
                         action_type='all_docs',
                         confirmation_code=code,
                         message='حذف جميع الوثائق',
                         description='سيتم حذف جميع وثائقك بشكل دائم ولا يمكن استرجاعها.')

# -------- Account Deletion --------
@app.route('/delete_account', methods=['GET', 'POST'])
def delete_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    
    if request.method == 'POST':
        confirmation_code = request.form.get('confirmation_code', '')
        password = request.form.get('password', '')
        
        # Verify password first
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user['password']):
            flash('كلمة المرور غير صحيحة', 'error')
            return redirect(url_for('delete_account'))
        
        # Verify confirmation code
        if not verify_deletion_code(user_id, confirmation_code, 'account'):
            flash('كود التأكيد غير صحيح أو منتهي الصلاحية', 'error')
            return redirect(url_for('delete_account'))
        
        # Delete all user data
        files_collection.delete_many({'user_id': user_id})
        docs_collection.delete_many({'user_id': user_id})
        settings_collection.delete_many({'user_id': user_id})
        deletion_codes_collection.delete_many({'user_id': user_id})
        
        # Deactivate user account (soft delete)
        users_collection.update_one(
            {'_id': ObjectId(user_id)}, 
            {'$set': {'is_active': False, 'deleted_at': datetime.utcnow()}}
        )
        
        session.clear()
        flash('تم حذف حسابك بنجاح', 'success')
        return redirect(url_for('index'))
    
    # Generate and store new confirmation code
    code = store_deletion_code(user_id, 'account')
    return render_template('confirm_deletion.html', 
                         action_type='account',
                         confirmation_code=code,
                         message='حذف الحساب',
                         description='سيتم حذف حسابك وكل بياناتك بشكل دائم ولا يمكن استرجاعها.',
                         require_password=True)

# -------- Settings --------
@app.route('/settings', methods=['GET','POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))


    user_id = session['user_id']
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('logout'))

    if request.method == 'POST':
        new_username = request.form.get('username')
        new_email = request.form.get('email')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')

        updates = {}
        if new_username and new_username != user.get('username'):
            updates['username'] = new_username
            session['username'] = new_username

        if new_email and new_email != user.get('email'):
            if users_collection.find_one({'email': new_email, '_id': {'$ne': ObjectId(user_id)}}):
                flash('البريد الإلكتروني مسجل مسبقاً', 'error')
            else:
                updates['email'] = new_email

        if new_password:
            if current_password and bcrypt.checkpw(current_password.encode('utf-8'), user['password']):
                updates['password'] = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            else:
                flash('كلمة المرور الحالية غير صحيحة', 'error')
                return redirect(url_for('profile'))

        if updates:
            users_collection.update_one({'_id': ObjectId(user_id)}, {'$set': updates})
            flash('تم تحديث الملف الشخصي بنجاح', 'success')
        else:
            flash('لم يتم إجراء أي تغييرات', 'info')
    user_id = session['user_id']
    user_settings = settings_collection.find_one({'user_id': user_id})

    if request.method == 'POST':
        new_settings = {
            'theme': request.form.get('theme','light'),
            'language': request.form.get('language','ar'),
            'auto_logout': int(request.form.get('auto_logout',60)),
            'two_factor_auth': 'two_factor_auth' in request.form,
            'notifications': 'notifications' in request.form,
            'updated_at': datetime.utcnow()
        }
        
        # Update settings in database
        settings_collection.update_one(
            {'user_id': user_id}, 
            {'$set': new_settings},
            upsert=True
        )
        
        # Apply settings to current session
        apply_user_settings(user_id)

        # Handle encryption password change
        new_encryption_password = request.form.get('encryption_password')
        if new_encryption_password and new_encryption_password.strip():
            try:
                # Re-encrypt all user data with new password
                user_cipher_old = get_user_cipher_suite(user_id)
                
                # Generate new salt and key
                new_salt = os.urandom(16)
                new_key, _ = generate_key_from_password(new_encryption_password.encode('utf-8'), new_salt)
                user_cipher_new = Fernet(new_key)
                
                # Re-encrypt files
                user_files = files_collection.find({'user_id': user_id})
                for file in user_files:
                    try:
                        decrypted_content = user_cipher_old.decrypt(file['encrypted_content'])
                        re_encrypted_content = user_cipher_new.encrypt(decrypted_content)
                        files_collection.update_one(
                            {'_id': file['_id']},
                            {'$set': {'encrypted_content': re_encrypted_content}}
                        )
                    except:
                        continue  # Skip files that can't be decrypted
                
                # Re-encrypt documents
                user_docs = docs_collection.find({'user_id': user_id})
                for doc in user_docs:
                    try:
                        decrypted_content = user_cipher_old.decrypt(doc['encrypted_content'])
                        re_encrypted_content = user_cipher_new.encrypt(decrypted_content)
                        docs_collection.update_one(
                            {'_id': doc['_id']},
                            {'$set': {'encrypted_content': re_encrypted_content}}
                        )
                    except:
                        continue  # Skip docs that can't be decrypted
                
                # Update user encryption credentials
                users_collection.update_one(
                    {'_id': ObjectId(user_id)}, 
                    {'$set': {
                        'encryption_password': new_encryption_password.encode('utf-8'),
                        'encryption_salt': new_salt
                    }}
                )
                
                flash('تم تحديث كلمة مرور التشفير وإعادة تشفير جميع البيانات', 'success')
            except Exception as e:
                flash('حدث خطأ أثناء تحديث كلمة مرور التشفير', 'error')
        
        flash('تم حفظ الإعدادات بنجاح', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', settings=user_settings, user=user)

# -------- Profile --------
@app.route('/profile', methods=['GET','POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('logout'))

    if request.method == 'POST':
        new_username = request.form.get('username')
        new_email = request.form.get('email')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')

        updates = {}
        if new_username and new_username != user.get('username'):
            updates['username'] = new_username
            session['username'] = new_username

        if new_email and new_email != user.get('email'):
            if users_collection.find_one({'email': new_email, '_id': {'$ne': ObjectId(user_id)}}):
                flash('البريد الإلكتروني مسجل مسبقاً', 'error')
            else:
                updates['email'] = new_email

        if new_password:
            if current_password and bcrypt.checkpw(current_password.encode('utf-8'), user['password']):
                updates['password'] = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            else:
                flash('كلمة المرور الحالية غير صحيحة', 'error')
                return redirect(url_for('profile'))

        if updates:
            users_collection.update_one({'_id': ObjectId(user_id)}, {'$set': updates})
            flash('تم تحديث الملف الشخصي بنجاح', 'success')
        else:
            flash('لم يتم إجراء أي تغييرات', 'info')
        return redirect(url_for('profile'))

    return render_template('profile.html', user=user)

# -------- Logout --------
@app.route('/logout')
def logout():
    session.clear()
    flash('تم تسجيل الخروج بنجاح', 'success')
    return redirect(url_for('index'))

# -------- API --------
@app.route('/api/stats')
def api_stats():
    if 'user_id' not in session:
        return jsonify({'error':'غير مصرح'}),401
    user_id = session['user_id']
    files_count = files_collection.count_documents({'user_id': user_id})
    docs_count = docs_collection.count_documents({'user_id': user_id})
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    return jsonify({
        'files_count': files_count,
        'docs_count': docs_count,
        'used_storage': user.get('used_storage',0),
        'storage_limit': user.get('storage_limit',100*1024*1024)
    })

# --- Run ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
