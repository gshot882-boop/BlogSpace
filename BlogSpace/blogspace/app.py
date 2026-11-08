from flask import (Flask, render_template, redirect, url_for,
                   flash, request, jsonify, session, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user,
                         current_user, login_required)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func
from models import db, User, Post, Comment, Like
import os, base64, uuid, requests
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production')
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER']                  = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH']             = 50 * 1024 * 1024   

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'mov', 'webm'}

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_TEXT_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
GEMINI_IMAGE_URL = 'https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view    = 'login'
login_manager.login_message = 'Пожалуйста, войдите в аккаунт.'

with app.app_context():
    db.create_all()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_media_kind(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    return 'video' if ext in {'mp4', 'mov', 'webm'} else 'image'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/')
def index():
    sort = request.args.get('sort', 'new')
    if sort == 'popular':
        posts = (db.session.query(Post)
                 .outerjoin(Like, Post.id == Like.post_id)
                 .group_by(Post.id)
                 .order_by(func.count(Like.id).desc(), Post.created_at.desc())
                 .all())
    else:
        posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('index.html', posts=posts, sort=sort)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username  = request.form.get('username', '').strip()
        email     = request.form.get('email',    '').strip().lower()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        avatar    = request.form.get('avatar', '😊')

        if not username or not email or not password:
            flash('Заполните все поля.', 'error')
            return render_template('register.html')
        if password != password2:
            flash('Пароли не совпадают.', 'error')
            return render_template('register.html')
        if len(password) < 6:
            flash('Пароль должен быть минимум 6 символов.', 'error')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято.', 'error')
            return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('Email уже используется.', 'error')
            return render_template('register.html')

        user = User(username=username, email=email,
                    password=generate_password_hash(password), avatar=avatar)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        session['user_id'] = user.id
        flash(f'Добро пожаловать, {user.username}!', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=True)
            session['user_id'] = user.id
            flash('Вы вошли в аккаунт.', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Неверное имя пользователя или пароль.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('user_id', None)
    logout_user()
    flash('Вы вышли из аккаунта.', 'info')
    return redirect(url_for('index'))

@app.route('/post/<int:post_id>')
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    comments = Comment.query.filter_by(post_id=post_id).order_by(Comment.created_at).all()
    user_liked = (current_user.is_authenticated and
                  Like.query.filter_by(post_id=post_id, user_id=current_user.id).first() is not None)
    return render_template('post.html', post=post, comments=comments, user_liked=user_liked)

@app.route('/post/new', methods=['GET', 'POST'])
@login_required
def post_create():
    if request.method == 'POST':
        title    = request.form.get('title',    '').strip()
        content  = request.form.get('content',  '').strip()
        ai_image = request.form.get('ai_image', '').strip()  

        if not title or not content:
            flash('Заполните заголовок и текст поста.', 'error')
            return render_template('post_form.html', post=None)

        media_path = None
        media_type = None
        file = request.files.get('media_file')
        if file and file.filename and allowed_file(file.filename):
            ext      = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            media_path = filename
            media_type = get_media_kind(filename)

        post = Post(title=title, content=content,
                    media_path=media_path, media_type=media_type,
                    ai_image=ai_image if ai_image else None,
                    user_id=current_user.id)
        db.session.add(post)
        db.session.commit()
        flash('Пост опубликован!', 'success')
        return redirect(url_for('post_detail', post_id=post.id))
    return render_template('post_form.html', post=None)

@app.route('/post/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def post_edit(post_id):
    post = Post.query.get_or_404(post_id)
    if post.user_id != current_user.id:
        flash('Вы не можете редактировать чужой пост.', 'error')
        return redirect(url_for('post_detail', post_id=post_id))
    if request.method == 'POST':
        title   = request.form.get('title',   '').strip()
        content = request.form.get('content', '').strip()
        if not title or not content:
            flash('Заполните все поля.', 'error')
            return render_template('post_form.html', post=post)
        post.title   = title
        post.content = content

        file = request.files.get('media_file')
        if file and file.filename and allowed_file(file.filename):
            ext      = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            post.media_path = filename
            post.media_type = get_media_kind(filename)

        new_ai = request.form.get('ai_image', '').strip()
        if new_ai:
            post.ai_image = new_ai

        db.session.commit()
        flash('Пост обновлён.', 'success')
        return redirect(url_for('post_detail', post_id=post.id))
    return render_template('post_form.html', post=post)

@app.route('/post/<int:post_id>/delete', methods=['POST'])
@login_required
def post_delete(post_id):
    post = Post.query.get_or_404(post_id)
    if post.user_id != current_user.id:
        flash('Вы не можете удалить чужой пост.', 'error')
        return redirect(url_for('post_detail', post_id=post_id))
    if post.media_path:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], post.media_path))
        except FileNotFoundError:
            pass
    db.session.delete(post)
    db.session.commit()
    flash('Пост удалён.', 'info')
    return redirect(url_for('index'))

@app.route('/post/<int:post_id>/like', methods=['POST'])
@login_required
def toggle_like(post_id):
    post = Post.query.get_or_404(post_id)
    like = Like.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    if like:
        db.session.delete(like)
        liked = False
    else:
        db.session.add(Like(post_id=post_id, user_id=current_user.id))
        liked = True
    db.session.commit()
    return jsonify({'likes': post.likes_count, 'liked': liked})

@app.route('/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    Post.query.get_or_404(post_id)
    data    = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'status': 'error', 'message': 'Комментарий пустой'}), 400
    comment = Comment(content=content, post_id=post_id, user_id=current_user.id)
    db.session.add(comment)
    db.session.commit()
    return jsonify({
        'status':   'ok',
        'username': current_user.username,
        'avatar':   current_user.avatar,
        'content':  comment.content,
        'date':     comment.created_at.strftime('%d.%m.%Y %H:%M')
    })
@app.route('/ai/generate-text', methods=['POST'])
@login_required
def ai_generate_text():
    if not GEMINI_API_KEY:
        return jsonify({'error': 'GEMINI_API_KEY не настроен'}), 500
    data  = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Заголовок пустой'}), 400

    payload = {
        "contents": [{
            "parts": [{"text": f"Напиши интересный текст для блог-поста с заголовком: «{title}». "
                               f"Текст на русском языке, 2-4 абзаца. Только текст, без пояснений."}]
        }]
    }
    try:
        resp   = requests.post(
            f"{GEMINI_TEXT_URL}?key={GEMINI_API_KEY}",
            json=payload, timeout=30
        )
        result = resp.json()

        print("GEMINI RESPONSE:", result)

        if 'candidates' in result:
            text = result['candidates'][0]['content']['parts'][0]['text']
        elif 'error' in result:
            return jsonify({'error': result['error'].get('message', 'Ошибка API')}), 500
        else:
            return jsonify({'error': f'Неизвестный ответ: {str(result)[:200]}'}), 500

        return jsonify({'text': text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/ai/generate-image', methods=['POST'])
@login_required
def ai_generate_image():
    data   = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'Промпт пустой'}), 400
    try:
        import urllib.parse
        encoded = urllib.parse.quote(prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=800&height=500&nologo=true"
        resp = requests.get(img_url, timeout=60)
        b64  = base64.b64encode(resp.content).decode('utf-8')
        return jsonify({'image': b64})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/profile')
@login_required
def profile():
    posts = Post.query.filter_by(user_id=current_user.id).order_by(Post.created_at.desc()).all()
    return render_template('profile.html', posts=posts)

@app.route('/profile/avatar', methods=['POST'])
@login_required
def update_avatar():
    avatar = request.form.get('avatar', '😊')
    current_user.avatar = avatar
    db.session.commit()
    flash('Аватар обновлён!', 'success')
    return redirect(url_for('profile'))

if __name__ == '__main__':
    app.run(debug=True)
