document.addEventListener('DOMContentLoaded', function() {
    // إخفاء رسائل التنبيه بعد 5 ثواني
    setTimeout(function() {
        const alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            alert.style.display = 'none';
        });
    }, 5000);

    // إدارة الوضع الداكن
    const theme = localStorage.getItem('theme') || 'light';
    applyTheme(theme);

    // تحديث شريط التقدم
    updateProgressBars();

    // إضافة تأثيرات للبطاقات
    const cards = document.querySelectorAll('.card-hover');
    cards.forEach(card => {
        card.classList.add('card-hover');
    });

    // التحقق من قوة كلمة المرور
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    passwordInputs.forEach(input => {
        input.addEventListener('input', function() {
            checkPasswordStrength(this.value, this);
        });
    });

    // تحديث الإحصائيات
    updateStats();
});

function applyTheme(theme) {
    if (theme === 'dark') {
        document.body.classList.add('dark');
    } else {
        document.body.classList.remove('dark');
    }
    localStorage.setItem('theme', theme);
}

function updateProgressBars() {
    const progressBars = document.querySelectorAll('.progress-bar');
    progressBars.forEach(bar => {
        const percentage = bar.getAttribute('data-percentage');
        bar.style.width = percentage + '%';
    });
}

function checkPasswordStrength(password, inputElement) {
    let strength = 0;
    const feedback = document.getElementById('password-feedback') || createPasswordFeedback(inputElement);

    if (password.length >= 8) strength++;
    if (/[a-z]/.test(password)) strength++;
    if (/[A-Z]/.test(password)) strength++;
    if (/[0-9]/.test(password)) strength++;
    if (/[^A-Za-z0-9]/.test(password)) strength++;

    const messages = [
        'ضعيفة جداً',
        'ضعيفة',
        'متوسطة',
        'قوية',
        'قوية جداً'
    ];

    const colors = [
        'text-red-600',
        'text-orange-600',
        'text-yellow-600',
        'text-green-500',
        'text-green-700'
    ];

    feedback.textContent = `قوة كلمة المرور: ${messages[strength]}`;
    feedback.className = `text-sm mt-1 ${colors[strength]}`;
}

function createPasswordFeedback(inputElement) {
    const feedback = document.createElement('div');
    feedback.id = 'password-feedback';
    inputElement.parentNode.appendChild(feedback);
    return feedback;
}

async function updateStats() {
    try {
        const response = await fetch('/api/stats');
        if (response.ok) {
            const data = await response.json();
            
            // تحديث الإحصائيات في الواجهة
            const statsElements = {
                'files-count': data.files_count,
                'docs-count': data.docs_count,
                'storage-used': formatBytes(data.used_storage),
                'storage-limit': formatBytes(data.storage_limit),
                'storage-percentage': Math.round((data.used_storage / data.storage_limit) * 100)
            };

            for (const [id, value] of Object.entries(statsElements)) {
                const element = document.getElementById(id);
                if (element) {
                    element.textContent = value;
                }
            }

            // تحديث شريط التقدم
            const progressBar = document.querySelector('.progress-bar');
            if (progressBar) {
                progressBar.style.width = statsElements['storage-percentage'] + '%';
            }
        }
    } catch (error) {
        console.error('Error fetching stats:', error);
    }
}

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';

    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];

    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// وظائف لإدارة الملفات والوثائق
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

function showLoading() {
    const loading = document.createElement('div');
    loading.id = 'loading-overlay';
    loading.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
    loading.innerHTML = `
        <div class="loading-spinner"></div>
        <p class="text-white mt-4">جاري التحميل...</p>
    `;
    document.body.appendChild(loading);
}

function hideLoading() {
    const loading = document.getElementById('loading-overlay');
    if (loading) {
        loading.remove();
    }
}

// إدارة التبويب
function switchTab(tabName) {
    // إخفاء جميع المحتويات
    const tabContents = document.querySelectorAll('.tab-content');
    tabContents.forEach(content => {
        content.classList.add('hidden');
    });

    // إلغاء تفعيل جميع الأزرار
    const tabButtons = document.querySelectorAll('.tab-button');
    tabButtons.forEach(button => {
        button.classList.remove('bg-blue-600', 'text-white');
        button.classList.add('bg-gray-200', 'text-gray-700');
    });

    // إظهار المحتوى المحدد وتفعيل الزر
    document.getElementById(tabName + '-tab').classList.remove('hidden');
    event.currentTarget.classList.remove('bg-gray-200', 'text-gray-700');
    event.currentTarget.classList.add('bg-blue-600', 'text-white');
}