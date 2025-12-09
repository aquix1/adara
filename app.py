import os
import json
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from pymongo import MongoClient
from bson import ObjectId

# تهيئة التطبيق
app = Flask(__name__)
app.config['SECRET_KEY'] = 'flask-chat-secret-key-2024'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# أكواد المستخدمين المسجلين مسبقًا
PREDEFINED_CODES = {
    'YAMAN2083': {
        'name': 'يمان',
        'code': 'YAMAN2083',
        'color': 'bg-blue-500',
        'text_color': 'text-white',
        'avatar': '👨‍💻'
    },
    'TAYSEER9202': {
        'name': 'تيسير',
        'code': 'TAYSEER9202',
        'color': 'bg-green-500',
        'text_color': 'text-white',
        'avatar': '👨‍🎓'
    }
}

# اتصال MongoDB
try:
    MONGO_URI = "mongodb+srv://tncxzml:CPsMBvK4w47HOsU0@cardify.05dzz.mongodb.net/"
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()
    print("✅ Connected to MongoDB successfully")
    
    db = client['flask_chat_db']
    users_collection = db['users']
    messages_collection = db['messages']
    
    users_collection.create_index([('code', 1)], unique=True)
    messages_collection.create_index([('timestamp', -1)])
    
    print("✅ MongoDB collections ready")
    
except Exception as e:
    print(f"❌ MongoDB connection error: {e}")
    print("⚠️ Using in-memory storage")
    
    class MemoryStorage:
        def __init__(self):
            self.messages = []
            self.users = []
        
        def insert_one(self, collection, data):
            data['_id'] = str(ObjectId())
            if collection == 'messages':
                self.messages.append(data)
            else:
                self.users.append(data)
            return type('obj', (object,), {'inserted_id': data['_id']})()
        
        def find(self, collection, query=None, sort=None, limit=None):
            if collection == 'messages':
                data = self.messages.copy()
            else:
                data = self.users.copy()
            
            if query:
                filtered = []
                for item in data:
                    match = True
                    for key, value in query.items():
                        if item.get(key) != value:
                            match = False
                            break
                    if match:
                        filtered.append(item)
                data = filtered
            
            if sort:
                field, direction = sort[0]
                reverse = direction == -1
                data.sort(key=lambda x: x.get(field, ''), reverse=reverse)
            
            if limit:
                data = data[:limit]
            
            return data
        
        def update_one(self, collection, query, update, upsert=False):
            if collection == 'messages':
                data_list = self.messages
            else:
                data_list = self.users
            
            for item in data_list:
                match = True
                for key, value in query.items():
                    if item.get(key) != value:
                        match = False
                        break
                if match:
                    if '$set' in update:
                        item.update(update['$set'])
                    return type('obj', (object,), {'matched_count': 1})()
            
            if upsert:
                new_item = query.copy()
                if '$set' in update:
                    new_item.update(update['$set'])
                new_item['_id'] = str(ObjectId())
                data_list.append(new_item)
            
            return type('obj', (object,), {'matched_count': 0})()
    
    storage = MemoryStorage()
    users_collection = type('obj', (object,), {
        'insert_one': lambda data: storage.insert_one('users', data),
        'find': lambda query=None, sort=None, limit=None: storage.find('users', query, sort, limit),
        'update_one': lambda query, update, upsert=False: storage.update_one('users', query, update, upsert),
        'create_index': lambda *args: None
    })()
    
    messages_collection = type('obj', (object,), {
        'insert_one': lambda data: storage.insert_one('messages', data),
        'find': lambda query=None, sort=None, limit=None: storage.find('messages', query, sort, limit),
        'update_one': lambda query, update, upsert=False: storage.update_one('messages', query, update, upsert),
        'create_index': lambda *args: None
    })()

# إنشاء مجلد التحميلات
if not os.path.exists('uploads'):
    os.makedirs('uploads')
    print("📁 Created uploads directory")

# ============ ROUTES ============

@app.route('/')
def index():
    """الصفحة الرئيسية - تسجيل الدخول"""
    if 'user' in session:
        return redirect(url_for('chat'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    """معالجة تسجيل الدخول"""
    code = request.form.get('code', '').strip().upper()
    
    if code in PREDEFINED_CODES:
        user_data = PREDEFINED_CODES[code].copy()
        session['user'] = user_data
        
        users_collection.update_one(
            {'code': code},
            {'$set': {
                'name': user_data['name'],
                'code': code,
                'color': user_data['color'],
                'avatar': user_data.get('avatar', '👤'),
                'is_online': True,
                'last_login': datetime.now(),
                'last_seen': datetime.now()
            }},
            upsert=True
        )
        
        return redirect(url_for('chat'))
    
    return render_template('login.html', error='الكود غير صحيح! الرجاء المحاولة مرة أخرى.')

@app.route('/chat')
def chat():
    """صفحة الدردشة"""
    if 'user' not in session:
        return redirect(url_for('index'))
    
    # جلب الرسائل القديمة
    messages = list(messages_collection.find(
        {},
        {'_id': 0, 'user_code': 1, 'user_name': 1, 'user_color': 1, 
         'message': 1, 'timestamp': 1, 'message_type': 1, 'file_url': 1,
         'file_name': 1, 'file_type': 1}
    ).sort('timestamp', 1).limit(50))
    
    # تحويل timestamps
    for message in messages:
        if 'timestamp' in message and isinstance(message['timestamp'], datetime):
            message['timestamp'] = message['timestamp'].strftime('%H:%M')
    
    # جلب المستخدمين المتصلين
    online_users = list(users_collection.find(
        {'is_online': True},
        {'_id': 0, 'name': 1, 'code': 1, 'color': 1}
    ))
    
    return render_template('chat.html',
                         user=session['user'],
                         messages=messages,
                         online_users=online_users)

@app.route('/logout')
def logout():
    """تسجيل الخروج"""
    if 'user' in session:
        user_code = session['user']['code']
        users_collection.update_one(
            {'code': user_code},
            {'$set': {'is_online': False}}
        )
        session.pop('user', None)
    
    return redirect(url_for('index'))

@app.route('/upload_image', methods=['POST'])
def upload_image():
    """رفع صورة"""
    if 'user' not in session:
        return jsonify({'error': 'غير مسموح'}), 401
    
    if 'image' not in request.files:
        return jsonify({'error': 'لم يتم اختيار صورة'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار صورة'}), 400
    
    # التحقق من نوع الملف
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    
    if file_ext not in allowed_extensions:
        return jsonify({'error': 'نوع الملف غير مدعوم'}), 400
    
    # إنشاء اسم فريد للملف
    unique_filename = f"{uuid.uuid4()}.{file_ext}"
    
    try:
        # حفظ محلياً
        file_path = os.path.join('uploads', unique_filename)
        file.save(file_path)
        file_url = f"/uploads/{unique_filename}"
        
        return jsonify({
            'success': True,
            'file_url': file_url,
            'file_name': file.filename,
            'file_type': 'image'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    """رفع تسجيل صوتي"""
    if 'user' not in session:
        return jsonify({'error': 'غير مسموح'}), 401
    
    if 'audio' not in request.files:
        return jsonify({'error': 'لم يتم تسجيل صوت'}), 400
    
    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'لم يتم تسجيل صوت'}), 400
    
    # إنشاء اسم فريد للملف
    unique_filename = f"{uuid.uuid4()}.wav"
    
    try:
        # حفظ محلياً
        file_path = os.path.join('uploads', unique_filename)
        file.save(file_path)
        file_url = f"/uploads/{unique_filename}"
        
        # حساب مدة الصوت (تقريبي)
        import wave
        try:
            with wave.open(file_path, 'rb') as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                duration = frames / float(rate)
        except:
            duration = 0
        
        return jsonify({
            'success': True,
            'file_url': file_url,
            'file_name': f"تسجيل صوتي ({int(duration)} ثانية)",
            'file_type': 'audio',
            'duration': duration
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """خدمة الملفات المحفوظة محلياً"""
    try:
        return send_from_directory('uploads', filename)
    except Exception as e:
        return f"File not found: {filename}", 404

# ============ SOCKET EVENTS ============

connected_clients = {}

@socketio.on('connect')
def handle_connect():
    print(f"✅ Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_clients:
        user_code = connected_clients[request.sid]
        users_collection.update_one(
            {'code': user_code},
            {'$set': {'is_online': False}}
        )
        del connected_clients[request.sid]
        
        user = users_collection.find_one({'code': user_code})
        if user:
            emit('user_status', {
                'user': user['name'],
                'status': 'disconnected',
                'message': f"{user['name']} غادر الدردشة"
            }, broadcast=True)

@socketio.on('user_connected')
def handle_user_connected(data):
    if 'user' in session:
        user_code = session['user']['code']
        connected_clients[request.sid] = user_code
        
        users_collection.update_one(
            {'code': user_code},
            {'$set': {
                'is_online': True,
                'last_seen': datetime.now()
            }}
        )
        
        emit('user_status', {
            'user': session['user']['name'],
            'status': 'connected',
            'message': f"{session['user']['name']} انضم إلى الدردشة"
        }, broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    if 'user' not in session:
        return
    
    user = session['user']
    message_text = data.get('message', '').strip()
    message_type = data.get('message_type', 'text')
    file_url = data.get('file_url', '')
    file_name = data.get('file_name', '')
    file_type = data.get('file_type', '')
    
    if not message_text and not file_url:
        return
    
    # إنشاء كائن الرسالة
    message_data = {
        'user_code': user['code'],
        'user_name': user['name'],
        'user_color': user['color'],
        'user_text_color': user['text_color'],
        'message': message_text,
        'message_type': message_type,
        'timestamp': datetime.now()
    }
    
    # إذا كان هناك ملف
    if file_url:
        message_data.update({
            'file_url': file_url,
            'file_name': file_name,
            'file_type': file_type
        })
    
    # حفظ الرسالة في MongoDB
    result = messages_collection.insert_one(message_data)
    message_data['_id'] = str(result.inserted_id)
    message_data['timestamp'] = datetime.now().strftime('%H:%M')
    
    # إرسال الرسالة إلى جميع المستخدمين
    emit('new_message', message_data, broadcast=True)

@socketio.on('typing')
def handle_typing(data):
    if 'user' in session:
        emit('user_typing', {
            'user': session['user']['name'],
            'is_typing': data.get('is_typing', False)
        }, broadcast=True)

@socketio.on('recording_status')
def handle_recording_status(data):
    if 'user' in session:
        emit('user_recording', {
            'user': session['user']['name'],
            'is_recording': data.get('is_recording', False)
        }, broadcast=True)

@socketio.on('get_online_users')
def handle_get_online_users():
    online_users = list(users_collection.find(
        {'is_online': True},
        {'_id': 0, 'name': 1, 'code': 1, 'color': 1}
    ))
    emit('update_online_users', {'users': online_users}, broadcast=True)

# ============ إنشاء ملفات HTML ============

def create_templates():
    """إنشاء مجلد templates والملفات HTML"""
    templates_dir = 'templates'
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)
        print("📁 Created templates directory")
    
    # ملف login.html
    login_html = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>تسجيل الدخول - دردشة متكاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { font-family: system-ui, -apple-system, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }
    </style>
</head>
<body class="flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl p-8 max-w-md w-full">
        <div class="text-center mb-8">
            <div class="inline-flex items-center justify-center w-16 h-16 bg-blue-100 rounded-full mb-4">
                <i class="fas fa-comments text-3xl text-blue-600"></i>
            </div>
            <h1 class="text-3xl font-bold text-gray-800 mb-2">مرحباً بك</h1>
            <p class="text-gray-600">أدخل كود الدخول للانضمام إلى الدردشة المتكاملة</p>
        </div>
        
        {% if error %}
        <div class="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl">
            <div class="flex items-center">
                <i class="fas fa-exclamation-circle text-red-500 ml-2"></i>
                <p class="text-red-600 font-medium">{{ error }}</p>
            </div>
        </div>
        {% endif %}
        
        <form method="POST" action="/login" class="space-y-6">
            <div>
                <label class="block text-gray-700 text-sm font-medium mb-2" for="code">
                    كود الدخول
                </label>
                <div class="relative">
                    <div class="absolute inset-y-0 right-0 pr-3 flex items-center pointer-events-none">
                        <i class="fas fa-key text-gray-400"></i>
                    </div>
                    <input 
                        type="text" 
                        id="code" 
                        name="code" 
                        required
                        class="w-full pl-4 pr-10 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent transition duration-200"
                        placeholder="أدخل الكود هنا"
                        autocomplete="off"
                        autofocus
                    >
            
            <button type="submit" class="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white py-3 rounded-xl font-semibold hover:from-blue-700 hover:to-purple-700 transition duration-200 shadow-lg">
                <i class="fas fa-sign-in-alt ml-2"></i>
                دخول إلى الدردشة
            </button>
        </form>
        
    </div>
</body>
</html>
    '''
    
    # ملف chat.html مع مشغل صوت متطور
    chat_html = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>دردشة متكاملة - {{ user.name }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { font-family: system-ui, -apple-system, sans-serif; }
        .message-enter { animation: slideInRight 0.3s ease-out; }
        @keyframes slideInRight {
            from { transform: translateX(20px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .typing-indicator {
            display: inline-flex;
            align-items: center;
        }
        .typing-dot {
            width: 8px;
            height: 8px;
            margin: 0 2px;
            background-color: #999;
            border-radius: 50%;
            animation: typing 1.5s infinite ease-in-out;
        }
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #f1f1f1; border-radius: 4px; }
        ::-webkit-scrollbar-thumb { background: #888; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #555; }
        
        .record-btn.recording {
            animation: pulse 1.5s infinite;
            background-color: #ef4444 !important;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .chat-image {
            max-width: 300px;
            max-height: 300px;
            border-radius: 12px;
            cursor: pointer;
        }
        
        .audio-player {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 12px;
            padding: 12px;
            color: white;
        }
        
        .audio-progress {
            height: 6px;
            background: rgba(255, 255, 255, 0.3);
            border-radius: 3px;
            margin-top: 8px;
            overflow: hidden;
            cursor: pointer;
        }
        
        .audio-progress-bar {
            height: 100%;
            background: white;
            border-radius: 3px;
            width: 0%;
            transition: width 0.1s linear;
        }
        
        .audio-time {
            font-size: 11px;
            opacity: 0.8;
            margin-top: 4px;
        }
        
        /* مؤشر الصوت أثناء التشغيل */
        .audio-playing {
            position: relative;
        }
        
        .audio-playing::after {
            content: '';
            position: absolute;
            top: -2px;
            right: -2px;
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        
        /* أمواج صوتية */
        .sound-wave {
            display: flex;
            align-items: center;
            height: 20px;
            margin-top: 5px;
        }
        
        .wave-bar {
            width: 3px;
            background: white;
            margin: 0 1px;
            border-radius: 2px;
            animation: wave 1.5s ease-in-out infinite;
        }
        
        .wave-bar:nth-child(2) { animation-delay: 0.2s; }
        .wave-bar:nth-child(3) { animation-delay: 0.4s; }
        .wave-bar:nth-child(4) { animation-delay: 0.6s; }
        .wave-bar:nth-child(5) { animation-delay: 0.8s; }
        
        @keyframes wave {
            0%, 100% { height: 5px; }
            50% { height: 15px; }
        }
    </style>
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="h-screen flex flex-col">
        <!-- شريط العنوان -->
        <header class="bg-gradient-to-r from-blue-600 to-purple-600 text-white shadow-lg">
            <div class="container mx-auto px-4 py-4">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-4 space-x-reverse">
                        <div class="{{ user.color }} w-12 h-12 rounded-full flex items-center justify-center text-2xl">
                            {{ user.avatar }}
                        </div>
                        <div>
                            <h1 class="text-2xl font-bold">مرحباً {{ user.name }}</h1>
                            <p class="text-blue-100">دردشة متكاملة - إرسال صور وتسجيلات</p>
                        </div>
                    </div>
                    <div class="flex items-center space-x-4 space-x-reverse">
                        <a href="/logout" 
                           class="bg-white/20 hover:bg-white/30 px-6 py-2 rounded-full font-semibold transition duration-200 flex items-center">
                            <i class="fas fa-sign-out-alt ml-2"></i>
                            خروج
                        </a>
                    </div>
                </div>
            </div>
        </header>

        <!-- منطقة الدردشة الرئيسية -->
        <div class="flex-1 flex overflow-hidden">
            <main class="flex-1 flex flex-col bg-gray-50">
                <!-- منطقة الرسائل -->
                <div id="messagesContainer" class="flex-1 overflow-y-auto p-4 md:p-6">
                    {% for message in messages %}
                    <div class="message-enter mb-4" id="message_{{ loop.index }}">
                        {% if message.user_code == user.code %}
                        <!-- رسالتي -->
                        <div class="flex justify-start">
                            <div class="max-w-xs md:max-w-md lg:max-w-lg bg-blue-100 rounded-2xl rounded-tr-none p-4 ml-12">
                                <div class="flex items-center mb-2">
                                    <div class="w-8 h-8 {{ message.user_color }} rounded-full flex items-center justify-center text-white text-sm font-bold ml-2">
                                        {{ message.user_name|first }}
                                    </div>
                                    <span class="font-semibold text-gray-800">{{ message.user_name }}</span>
                                    <span class="text-xs text-gray-500 mr-auto pr-2">{{ message.timestamp }}</span>
                                </div>
                                
                                {% if message.message_type == 'image' %}
                                <!-- عرض الصورة -->
                                <div class="mb-2">
                                    <img src="{{ message.file_url }}" 
                                         alt="{{ message.file_name }}"
                                         class="chat-image cursor-pointer"
                                         onclick="openImageModal('{{ message.file_url }}')">
                                    <p class="text-xs text-gray-500 mt-1">{{ message.file_name }}</p>
                                </div>
                                {% if message.message %}
                                <p class="text-gray-800 mt-2">{{ message.message }}</p>
                                {% endif %}
                                
                                {% elif message.message_type == 'audio' %}
                                <!-- مشغل الصوت المتطور -->
                                <div class="audio-player mb-2" id="audioPlayer_{{ loop.index }}">
                                    <div class="flex items-center justify-between mb-2">
                                        <div class="flex items-center">
                                            <i class="fas fa-volume-up text-white ml-2"></i>
                                            <span class="text-white text-sm mr-2">{{ message.file_name }}</span>
                                        </div>
                                        <div class="flex items-center space-x-2 space-x-reverse">
                                            <button onclick="togglePlay('{{ message.file_url }}', {{ loop.index }})" 
                                                    class="bg-white text-purple-600 w-8 h-8 rounded-full flex items-center justify-center hover:bg-gray-100 audio-play-btn"
                                                    id="playBtn_{{ loop.index }}">
                                                <i class="fas fa-play" id="playIcon_{{ loop.index }}"></i>
                                            </button>
                                            <button onclick="stopAudio('{{ loop.index }}')" 
                                                    class="bg-white/20 text-white w-8 h-8 rounded-full flex items-center justify-center hover:bg-white/30">
                                                <i class="fas fa-stop"></i>
                                            </button>
                                        </div>
                                    </div>
                                    
                                    <!-- شريط التقدم -->
                                    <div class="audio-progress" onclick="seekAudio(event, '{{ loop.index }}')">
                                        <div class="audio-progress-bar" id="progressBar_{{ loop.index }}"></div>
                                    </div>
                                    
                                    <!-- وقت الصوت -->
                                    <div class="flex justify-between items-center mt-2">
                                        <span class="audio-time" id="currentTime_{{ loop.index }}">0:00</span>
                                        <span class="audio-time" id="duration_{{ loop.index }}">0:00</span>
                                    </div>
                                    
                                    <!-- أمواج صوتية (تظهر أثناء التشغيل) -->
                                    <div class="sound-wave hidden" id="waveform_{{ loop.index }}">
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                    </div>
                                </div>
                                {% if message.message %}
                                <p class="text-gray-800 mt-2">{{ message.message }}</p>
                                {% endif %}
                                
                                {% else %}
                                <!-- رسالة نصية عادية -->
                                <p class="text-gray-800">{{ message.message }}</p>
                                {% endif %}
                            </div>
                        </div>
                        {% else %}
                        <!-- رسالة الشخص الآخر -->
                        <div class="flex justify-end">
                            <div class="max-w-xs md:max-w-md lg:max-w-lg bg-white rounded-2xl rounded-tl-none p-4 mr-12 shadow-sm border border-gray-100">
                                <div class="flex items-center mb-2">
                                    <div class="w-8 h-8 {{ message.user_color }} rounded-full flex items-center justify-center text-white text-sm font-bold ml-2">
                                        {{ message.user_name|first }}
                                    </div>
                                    <span class="font-semibold text-gray-800">{{ message.user_name }}</span>
                                    <span class="text-xs text-gray-500 mr-auto pr-2">{{ message.timestamp }}</span>
                                </div>
                                
                                {% if message.message_type == 'image' %}
                                <!-- عرض الصورة -->
                                <div class="mb-2">
                                    <img src="{{ message.file_url }}" 
                                         alt="{{ message.file_name }}"
                                         class="chat-image cursor-pointer"
                                         onclick="openImageModal('{{ message.file_url }}')">
                                    <p class="text-xs text-gray-500 mt-1">{{ message.file_name }}</p>
                                </div>
                                {% if message.message %}
                                <p class="text-gray-800 mt-2">{{ message.message }}</p>
                                {% endif %}
                                
                                {% elif message.message_type == 'audio' %}
                                <!-- مشغل الصوت المتطور -->
                                <div class="audio-player mb-2" id="audioPlayer_{{ loop.index }}">
                                    <div class="flex items-center justify-between mb-2">
                                        <div class="flex items-center">
                                            <i class="fas fa-volume-up text-white ml-2"></i>
                                            <span class="text-white text-sm mr-2">{{ message.file_name }}</span>
                                        </div>
                                        <div class="flex items-center space-x-2 space-x-reverse">
                                            <button onclick="togglePlay('{{ message.file_url }}', {{ loop.index }})" 
                                                    class="bg-white text-purple-600 w-8 h-8 rounded-full flex items-center justify-center hover:bg-gray-100 audio-play-btn"
                                                    id="playBtn_{{ loop.index }}">
                                                <i class="fas fa-play" id="playIcon_{{ loop.index }}"></i>
                                            </button>
                                            <button onclick="stopAudio('{{ loop.index }}')" 
                                                    class="bg-white/20 text-white w-8 h-8 rounded-full flex items-center justify-center hover:bg-white/30">
                                                <i class="fas fa-stop"></i>
                                            </button>
                                        </div>
                                    </div>
                                    
                                    <!-- شريط التقدم -->
                                    <div class="audio-progress" onclick="seekAudio(event, '{{ loop.index }}')">
                                        <div class="audio-progress-bar" id="progressBar_{{ loop.index }}"></div>
                                    </div>
                                    
                                    <!-- وقت الصوت -->
                                    <div class="flex justify-between items-center mt-2">
                                        <span class="audio-time" id="currentTime_{{ loop.index }}">0:00</span>
                                        <span class="audio-time" id="duration_{{ loop.index }}">0:00</span>
                                    </div>
                                    
                                    <!-- أمواج صوتية (تظهر أثناء التشغيل) -->
                                    <div class="sound-wave hidden" id="waveform_{{ loop.index }}">
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                        <div class="wave-bar"></div>
                                    </div>
                                </div>
                                {% if message.message %}
                                <p class="text-gray-800 mt-2">{{ message.message }}</p>
                                {% endif %}
                                
                                {% else %}
                                <!-- رسالة نصية عادية -->
                                <p class="text-gray-800">{{ message.message }}</p>
                                {% endif %}
                            </div>
                        </div>
                        {% endif %}
                    </div>
                    {% endfor %}
                    
                    <!-- مؤشرات -->
                    <div id="typingIndicator" class="hidden">
                        <div class="flex justify-start mb-4">
                            <div class="bg-gray-200 rounded-2xl rounded-tr-none p-4 ml-12">
                                <div class="flex items-center space-x-2 space-x-reverse">
                                    <span id="typingUser" class="font-semibold text-gray-600"></span>
                                    <div class="typing-indicator">
                                        <div class="typing-dot"></div>
                                        <div class="typing-dot"></div>
                                        <div class="typing-dot"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- منطقة الإرسال المتقدمة -->
                <div class="border-t border-gray-200 bg-white p-4">
                    <!-- أدوات الإرسال -->
                    <div class="flex items-center space-x-4 space-x-reverse mb-3">
                        <!-- زر رفع صورة -->
                        <button onclick="document.getElementById('imageInput').click()" 
                                class="w-10 h-10 rounded-full bg-blue-100 text-blue-600 hover:bg-blue-200 transition duration-200 flex items-center justify-center">
                            <i class="fas fa-image"></i>
                        </button>
                        
                        <!-- زر تسجيل الصوت -->
                        <button id="recordButton" 
                                class="w-10 h-10 rounded-full bg-red-100 text-red-600 hover:bg-red-200 transition duration-200 flex items-center justify-center record-btn">
                            <i class="fas fa-microphone"></i>
                        </button>
                        
                        <!-- زر كاميرا -->
                        <button onclick="openCamera()" 
                                class="w-10 h-10 rounded-full bg-green-100 text-green-600 hover:bg-green-200 transition duration-200 flex items-center justify-center">
                            <i class="fas fa-camera"></i>
                        </button>
                    </div>
                    
                    <!-- نموذج الإرسال -->
                    <form id="messageForm" class="flex items-center space-x-4 space-x-reverse">
                        <div class="flex-1 relative">
                            <input 
                                type="text" 
                                id="messageInput" 
                                autocomplete="off"
                                placeholder="اكتب رسالتك هنا أو أرسل صورة/تسجيل..." 
                                class="w-full border border-gray-300 rounded-full py-3 px-6 pr-12 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition duration-200"
                            >
                            <button type="button" id="sendButton" class="absolute left-3 top-1/2 transform -translate-y-1/2 bg-gradient-to-r from-blue-600 to-purple-600 text-white p-2 rounded-full hover:from-blue-700 hover:to-purple-700 transition duration-200">
                                <i class="fas fa-paper-plane"></i>
                            </button>
                        </div>
                    </form>
                    
                    <!-- إدخال الملفات المخفي -->
                    <input type="file" id="imageInput" accept="image/*" class="hidden">
                    <input type="file" id="audioInput" accept="audio/*" class="hidden">
                    <input type="file" id="cameraInput" accept="image/*" capture="environment" class="hidden">
                    
                    <!-- مؤشر التسجيل -->
                    <div id="recordingTimer" class="hidden text-center mt-2">
                        <div class="inline-flex items-center bg-red-100 text-red-700 px-4 py-2 rounded-full">
                            <i class="fas fa-circle text-red-500 ml-2 animate-pulse"></i>
                            <span id="timer">00:00</span>
                            <button onclick="stopRecording()" class="text-red-700 hover:text-red-900 mr-2">
                                <i class="fas fa-stop"></i>
                            </button>
                        </div>
                    </div>
                    
                    <p class="text-xs text-gray-500 text-center mt-3">
                        <i class="fas fa-lightbulb ml-1"></i>
                        اضغط Enter للإرسال أو استخدم الأدوات للإرسال المتقدم
                    </p>
                </div>
            </main>
        </div>
    </div>
    
    <!-- مكتبات إضافية -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.0/socket.io.js"></script>
    <script>
        // الاتصال بـ SocketIO
        const socket = io();
        let typingTimer;
        let mediaRecorder;
        let audioChunks = [];
        let recordingTimer;
        let seconds = 0;
        let isRecording = false;
        
        // تخزين كائنات الصوت
        const audioPlayers = {};
        
        // عند الاتصال
        socket.on('connect', function() {
            console.log('Connected to chat server');
            socket.emit('user_connected', {
                name: '{{ user.name }}',
                code: '{{ user.code }}'
            });
        });
        
        // استقبال رسالة جديدة
        socket.on('new_message', function(data) {
            addMessage(data);
            scrollToBottom();
        });
        
        // مؤشر الكتابة
        socket.on('user_typing', function(data) {
            showTypingIndicator(data.user, data.is_typing);
        });
        
        // مؤشر التسجيل
        socket.on('user_recording', function(data) {
            showRecordingIndicator(data.user, data.is_recording);
        });
        
        // إضافة رسالة جديدة
        function addMessage(data) {
            const messagesContainer = document.getElementById('messagesContainer');
            const isMyMessage = data.user_code === '{{ user.code }}';
            
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message-enter mb-4';
            const messageId = Date.now(); // معرف فريد للرسالة
            
            // إنشاء وقت الرسالة
            const now = new Date();
            const timeString = now.toLocaleTimeString('ar-EG', {hour: '2-digit', minute:'2-digit'});
            
            let contentHtml = '';
            
            if (data.message_type === 'image') {
                contentHtml = `
                    <div class="mb-2">
                        <img src="${data.file_url}" 
                             alt="${data.file_name || 'صورة'}"
                             class="chat-image cursor-pointer"
                             onclick="openImageModal('${data.file_url}')">
                        <p class="text-xs text-gray-500 mt-1">${data.file_name || 'صورة'}</p>
                    </div>
                    ${data.message ? `<p class="text-gray-800 mt-2">${data.message}</p>` : ''}
                `;
            } else if (data.message_type === 'audio') {
                contentHtml = `
                    <div class="audio-player mb-2" id="audioPlayer_${messageId}">
                        <div class="flex items-center justify-between mb-2">
                            <div class="flex items-center">
                                <i class="fas fa-volume-up text-white ml-2"></i>
                                <span class="text-white text-sm mr-2">${data.file_name || 'تسجيل صوتي'}</span>
                            </div>
                            <div class="flex items-center space-x-2 space-x-reverse">
                                <button onclick="togglePlay('${data.file_url}', ${messageId})" 
                                        class="bg-white text-purple-600 w-8 h-8 rounded-full flex items-center justify-center hover:bg-gray-100 audio-play-btn"
                                        id="playBtn_${messageId}">
                                    <i class="fas fa-play" id="playIcon_${messageId}"></i>
                                </button>
                                <button onclick="stopAudio(${messageId})" 
                                        class="bg-white/20 text-white w-8 h-8 rounded-full flex items-center justify-center hover:bg-white/30">
                                    <i class="fas fa-stop"></i>
                                </button>
                            </div>
                        </div>
                        
                        <!-- شريط التقدم -->
                        <div class="audio-progress" onclick="seekAudio(event, ${messageId})">
                            <div class="audio-progress-bar" id="progressBar_${messageId}"></div>
                        </div>
                        
                        <!-- وقت الصوت -->
                        <div class="flex justify-between items-center mt-2">
                            <span class="audio-time" id="currentTime_${messageId}">0:00</span>
                            <span class="audio-time" id="duration_${messageId}">0:00</span>
                        </div>
                        
                        <!-- أمواج صوتية -->
                        <div class="sound-wave hidden" id="waveform_${messageId}">
                            <div class="wave-bar"></div>
                            <div class="wave-bar"></div>
                            <div class="wave-bar"></div>
                            <div class="wave-bar"></div>
                            <div class="wave-bar"></div>
                        </div>
                    </div>
                    ${data.message ? `<p class="text-gray-800 mt-2">${data.message}</p>` : ''}
                `;
            } else {
                contentHtml = `<p class="text-gray-800">${data.message}</p>`;
            }
            
            messageDiv.innerHTML = `
                ${isMyMessage ? 
                    `<div class="flex justify-start">
                        <div class="max-w-xs md:max-w-md lg:max-w-lg bg-blue-100 rounded-2xl rounded-tr-none p-4 ml-12">
                            <div class="flex items-center mb-2">
                                <div class="w-8 h-8 ${data.user_color} rounded-full flex items-center justify-center text-white text-sm font-bold ml-2">
                                    ${data.user_name.charAt(0)}
                                </div>
                                <span class="font-semibold text-gray-800">${data.user_name}</span>
                                <span class="text-xs text-gray-500 mr-auto pr-2">${timeString}</span>
                            </div>
                            ${contentHtml}
                        </div>
                    </div>` 
                    : 
                    `<div class="flex justify-end">
                        <div class="max-w-xs md:max-w-md lg:max-w-lg bg-white rounded-2xl rounded-tl-none p-4 mr-12 shadow-sm border border-gray-100">
                            <div class="flex items-center mb-2">
                                <div class="w-8 h-8 ${data.user_color} rounded-full flex items-center justify-center text-white text-sm font-bold ml-2">
                                    ${data.user_name.charAt(0)}
                                </div>
                                <span class="font-semibold text-gray-800">${data.user_name}</span>
                                <span class="text-xs text-gray-500 mr-auto pr-2">${timeString}</span>
                            </div>
                            ${contentHtml}
                        </div>
                    </div>`
                }
            `;
            
            messagesContainer.appendChild(messageDiv);
            
            // إذا كانت رسالة صوتية، قم بتهيئة المشغل
            if (data.message_type === 'audio') {
                setTimeout(() => {
                    initAudioPlayer(data.file_url, messageId);
                }, 100);
            }
        }
        
        // مؤشر الكتابة
        function showTypingIndicator(userName, isTyping) {
            const indicator = document.getElementById('typingIndicator');
            const typingUser = document.getElementById('typingUser');
            
            if (isTyping && userName !== '{{ user.name }}') {
                typingUser.textContent = userName;
                indicator.classList.remove('hidden');
                scrollToBottom();
            } else {
                indicator.classList.add('hidden');
            }
        }
        
        // مؤشر التسجيل
        function showRecordingIndicator(userName, isRecording) {
            // يمكن إضافة مؤشر مرئي هنا
            if (isRecording && userName !== '{{ user.name }}') {
                console.log(`${userName} is recording...`);
            }
        }
        
        // التمرير لأسفل
        function scrollToBottom() {
            const container = document.getElementById('messagesContainer');
            container.scrollTop = container.scrollHeight;
        }
        
        // إرسال الرسالة
        document.getElementById('messageForm').addEventListener('submit', function(e) {
            e.preventDefault();
            sendMessage();
        });
        
        document.getElementById('sendButton').addEventListener('click', sendMessage);
        
        function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if (message) {
                socket.emit('send_message', { 
                    message: message,
                    message_type: 'text'
                });
                input.value = '';
                
                // إخفاء مؤشر الكتابة
                socket.emit('typing', { is_typing: false });
                clearTimeout(typingTimer);
            }
        }
        
        // مؤشر الكتابة أثناء الكتابة
        document.getElementById('messageInput').addEventListener('input', function() {
            clearTimeout(typingTimer);
            
            if (this.value.trim()) {
                socket.emit('typing', { is_typing: true });
                
                typingTimer = setTimeout(() => {
                    socket.emit('typing', { is_typing: false });
                }, 2000);
            } else {
                socket.emit('typing', { is_typing: false });
            }
        });
        
        // إرسال بالضغط على Enter
        document.getElementById('messageInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // ============ إدارة الملفات ============
        
        // عند اختيار صورة
        document.getElementById('imageInput').addEventListener('change', function(e) {
            if (e.target.files.length > 0) {
                uploadFile(e.target.files[0], 'image');
            }
        });
        
        // عند التقاط صورة من الكاميرا
        function openCamera() {
            document.getElementById('cameraInput').click();
        }
        
        document.getElementById('cameraInput').addEventListener('change', function(e) {
            if (e.target.files.length > 0) {
                uploadFile(e.target.files[0], 'image');
            }
        });
        
        // رفع ملف إلى السيرفر
        async function uploadFile(file, type) {
            const formData = new FormData();
            formData.append(type, file);
            
            try {
                const response = await fetch(`/upload_${type}`, {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.success) {
                    // إرسال الرسالة مع الملف
                    socket.emit('send_message', {
                        message: document.getElementById('messageInput').value,
                        message_type: type,
                        file_url: result.file_url,
                        file_name: result.file_name,
                        file_type: type
                    });
                    
                    document.getElementById('messageInput').value = '';
                    showNotification(`${type === 'image' ? 'الصورة' : 'التسجيل'} تم إرساله`, 'success');
                } else {
                    showNotification(result.error || 'فشل في رفع الملف', 'warning');
                }
            } catch (error) {
                showNotification('خطأ في رفع الملف', 'warning');
            }
        }
        
        // ============ التسجيلات الصوتية ============
        
        document.getElementById('recordButton').addEventListener('click', function() {
            if (!isRecording) {
                startRecording();
            } else {
                stopRecording();
            }
        });
        
        async function startRecording() {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                
                mediaRecorder.ondataavailable = event => {
                    audioChunks.push(event.data);
                };
                
                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const file = new File([audioBlob], `تسجيل_${Date.now()}.webm`, { type: 'audio/webm' });
                    
                    // رفع التسجيل
                    await uploadFile(file, 'audio');
                    
                    // إيقاف المؤقت
                    clearInterval(recordingTimer);
                    seconds = 0;
                    document.getElementById('timer').textContent = '00:00';
                    document.getElementById('recordingTimer').classList.add('hidden');
                    
                    // إعادة تعيين زر التسجيل
                    const recordBtn = document.getElementById('recordButton');
                    recordBtn.classList.remove('recording');
                    recordBtn.innerHTML = '<i class="fas fa-microphone"></i>';
                    
                    // إرسال حالة التوقف عن التسجيل
                    socket.emit('recording_status', { is_recording: false });
                    
                    // إيقاف الميكروفون
                    stream.getTracks().forEach(track => track.stop());
                };
                
                // بدء التسجيل
                mediaRecorder.start();
                
                // تحديث واجهة المستخدم
                const recordBtn = document.getElementById('recordButton');
                recordBtn.classList.add('recording');
                recordBtn.innerHTML = '<i class="fas fa-stop"></i>';
                
                // إظهار المؤقت
                document.getElementById('recordingTimer').classList.remove('hidden');
                
                // بدء المؤقت
                recordingTimer = setInterval(() => {
                    seconds++;
                    const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
                    const secs = (seconds % 60).toString().padStart(2, '0');
                    document.getElementById('timer').textContent = `${mins}:${secs}`;
                }, 1000);
                
                // إرسال حالة بدء التسجيل
                socket.emit('recording_status', { is_recording: true });
                isRecording = true;
                
            } catch (error) {
                showNotification('تعذر الوصول إلى الميكروفون', 'warning');
            }
        }
        
        function stopRecording() {
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                mediaRecorder.stop();
                isRecording = false;
            }
        }
        
        // ============ مشغل الصوتيات المتطور ============
        
        // تهيئة مشغل الصوت
        function initAudioPlayer(audioUrl, playerId) {
            if (!audioPlayers[playerId]) {
                audioPlayers[playerId] = {
                    audio: new Audio(audioUrl),
                    isPlaying: false,
                    updateInterval: null
                };
                
                const audio = audioPlayers[playerId].audio;
                
                // عند تحميل البيانات
                audio.addEventListener('loadedmetadata', function() {
                    const duration = formatTime(audio.duration);
                    document.getElementById(`duration_${playerId}`).textContent = duration;
                });
                
                // عند تحديث الوقت
                audio.addEventListener('timeupdate', function() {
                    updateAudioProgress(playerId);
                });
                
                // عند انتهاء الصوت
                audio.addEventListener('ended', function() {
                    stopAudio(playerId);
                });
            }
        }
        
        // تشغيل/إيقاف الصوت
        function togglePlay(audioUrl, playerId) {
            initAudioPlayer(audioUrl, playerId);
            
            const player = audioPlayers[playerId];
            const playBtn = document.getElementById(`playBtn_${playerId}`);
            const playIcon = document.getElementById(`playIcon_${playerId}`);
            const waveform = document.getElementById(`waveform_${playerId}`);
            
            if (player.isPlaying) {
                // إيقاف الصوت
                player.audio.pause();
                player.isPlaying = false;
                playIcon.className = 'fas fa-play';
                playBtn.classList.remove('audio-playing');
                if (waveform) waveform.classList.add('hidden');
                
                // إيقاف التحديث
                if (player.updateInterval) {
                    clearInterval(player.updateInterval);
                    player.updateInterval = null;
                }
            } else {
                // إيقاف جميع الأصوات الأخرى
                stopAllAudios();
                
                // تشغيل الصوت
                player.audio.play();
                player.isPlaying = true;
                playIcon.className = 'fas fa-pause';
                playBtn.classList.add('audio-playing');
                if (waveform) waveform.classList.remove('hidden');
                
                // بدء التحديث
                player.updateInterval = setInterval(() => {
                    updateAudioProgress(playerId);
                }, 100);
            }
        }
        
        // إيقاف الصوت
        function stopAudio(playerId) {
            if (audioPlayers[playerId]) {
                const player = audioPlayers[playerId];
                const playBtn = document.getElementById(`playBtn_${playerId}`);
                const playIcon = document.getElementById(`playIcon_${playerId}`);
                const waveform = document.getElementById(`waveform_${playerId}`);
                const progressBar = document.getElementById(`progressBar_${playerId}`);
                const currentTime = document.getElementById(`currentTime_${playerId}`);
                
                player.audio.pause();
                player.audio.currentTime = 0;
                player.isPlaying = false;
                playIcon.className = 'fas fa-play';
                playBtn.classList.remove('audio-playing');
                if (waveform) waveform.classList.add('hidden');
                if (progressBar) progressBar.style.width = '0%';
                if (currentTime) currentTime.textContent = '0:00';
                
                // إيقاف التحديث
                if (player.updateInterval) {
                    clearInterval(player.updateInterval);
                    player.updateInterval = null;
                }
            }
        }
        
        // إيقاف جميع الأصوات
        function stopAllAudios() {
            for (const playerId in audioPlayers) {
                stopAudio(playerId);
            }
        }
        
        // تحديث شريط التقدم
        function updateAudioProgress(playerId) {
            if (audioPlayers[playerId]) {
                const player = audioPlayers[playerId];
                const progressBar = document.getElementById(`progressBar_${playerId}`);
                const currentTimeElem = document.getElementById(`currentTime_${playerId}`);
                const durationElem = document.getElementById(`duration_${playerId}`);
                
                if (player.audio.duration) {
                    const progress = (player.audio.currentTime / player.audio.duration) * 100;
                    if (progressBar) progressBar.style.width = `${progress}%`;
                    
                    // تحديث الوقت الحالي
                    if (currentTimeElem) {
                        currentTimeElem.textContent = formatTime(player.audio.currentTime);
                    }
                    
                    // تحديث الوقت المتبقي
                    if (durationElem) {
                        const remaining = player.audio.duration - player.audio.currentTime;
                        durationElem.textContent = `-${formatTime(remaining)}`;
                    }
                }
            }
        }
        
        // الانتقال في الصوت عند النقر على شريط التقدم
        function seekAudio(event, playerId) {
            if (audioPlayers[playerId]) {
                const progressBar = event.currentTarget;
                const rect = progressBar.getBoundingClientRect();
                const x = event.clientX - rect.left;
                const percentage = (x / rect.width) * 100;
                
                if (audioPlayers[playerId].audio.duration) {
                    const newTime = (percentage / 100) * audioPlayers[playerId].audio.duration;
                    audioPlayers[playerId].audio.currentTime = newTime;
                }
            }
        }
        
        // تنسيق الوقت (ثواني إلى دقائق:ثواني)
        function formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }
        
        function openImageModal(src) {
            window.open(src, '_blank');
        }
        
        // ============ إشعارات ============
        
        function showNotification(message, type) {
            const colors = {
                success: '#10b981',
                warning: '#f59e0b',
                info: '#3b82f6'
            };
            
            const notification = document.createElement('div');
            notification.className = 'fixed top-4 right-4 z-50 animate-fadeInDown';
            notification.innerHTML = `
                <div class="bg-white rounded-xl shadow-xl p-4 max-w-sm border-r-4" style="border-right-color: ${colors[type] || colors.info}">
                    <div class="flex items-center">
                        <div class="w-10 h-10 rounded-full flex items-center justify-center text-white ml-3" style="background: ${colors[type] || colors.info}">
                            <i class="fas fa-${type === 'success' ? 'check' : type === 'warning' ? 'exclamation' : 'info'}"></i>
                        </div>
                        <div>
                            <p class="font-medium text-gray-800">${message}</p>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.appendChild(notification);
            
            setTimeout(() => {
                notification.remove();
            }, 3000);
        }
        
        // ============ التهيئة ============
        
        window.addEventListener('load', function() {
            scrollToBottom();
            
            // تهيئة مشغلات الصوت الموجودة
            const audioElements = document.querySelectorAll('[id^="audioPlayer_"]');
            audioElements.forEach(element => {
                const playerId = element.id.split('_')[1];
                const audioUrl = element.querySelector('audio') ? element.querySelector('audio').src : null;
                if (audioUrl) {
                    initAudioPlayer(audioUrl, playerId);
                }
            });
        });
        
        // إضافة أنيميشن للـ CSS
        const style = document.createElement('style');
        style.textContent = `
            @keyframes fadeInDown {
                from { opacity: 0; transform: translateY(-20px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .animate-fadeInDown {
                animation: fadeInDown 0.3s ease-out;
            }
        `;
        document.head.appendChild(style);
    </script>
</body>
</html>
    '''
    
    # حفظ الملفات
    with open(os.path.join(templates_dir, 'login.html'), 'w', encoding='utf-8') as f:
        f.write(login_html)
    
    with open(os.path.join(templates_dir, 'chat.html'), 'w', encoding='utf-8') as f:
        f.write(chat_html)
    
    print("✅ HTML templates created successfully")

# ============ تشغيل التطبيق ============

if __name__ == '__main__':
    # إنشاء مجلد القوالب
    create_templates()
    
    print("=" * 70)
    print("🚀 بدء تشغيل تطبيق الدردشة المتكاملة...")
    print("=" * 70)
    print("📱 افتح: http://localhost:5000")
    print("🔑 أكواد الدخول:")
    print("   - YAMAN2083 لدخول كـ يمان")
    print("   - TAYSEER9202 لدخول كـ تيسير")
    print("💡 المميزات المتاحة:")
    print("   📸 إرسال الصور من الكاميرا أو الملفات")
    print("   🎤 تسجيلات صوتية مباشرة")
    print("   🔊 مشغل صوت متطور مع شريط تقدم")
    print("   ⏱️ عرض الوقت المتبقي للصوت")
    print("   ⚡ دردشة فورية بدون تحديث")
    print("=" * 70)
    
    # تشغيل التطبيق
    socketio.run(app, 
                 debug=True, 
                 host='0.0.0.0', 
                 port=5000,
                 allow_unsafe_werkzeug=True)
